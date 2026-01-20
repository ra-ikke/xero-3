"""Small HTTP API to read the *current* submission session as JSON.

This is intended for external tooling (e.g. desktop reviewer) that wants to
fetch the active session content without downloading a TXT from Discord.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import discord
from aiohttp import web

from helpers.session_export import collect_session_maps, get_session_marker_state, normalize_category_code
from helpers.auth_token import find_auth_record_by_token
from helpers.validation_utils import has_mapcrew_role, has_public_role
from helpers.submission_facade import (
    build_end_marker_message,
    build_review_parts_from_export_payload_v1,
    build_start_marker_message,
    get_category_thread,
    get_or_create_category_thread,
    post_review_results_and_close_thread,
    _start_new_session_for_panel,
)
from helpers.submission_panel import build_submission_panel_embed
from helpers.submission_panel import parse_panel_footer
from resources.channels import CHANNELS

logger = logging.getLogger(__name__)


def _env_bool(key: str, default: bool = False) -> bool:
    raw = (os.getenv(key, str(default)) or "").strip().lower()
    return raw in ("1", "true", "yes", "y", "on")


async def _find_current_thread_id(bot: discord.Client, *, category_code: str) -> Optional[int]:
    try:
        thread = await get_category_thread(bot, category_code=category_code)
    except Exception:
        thread = None
    return int(thread.id) if isinstance(thread, discord.Thread) else None


async def _find_panel_message_and_meta(
    bot: discord.Client, *, category_code: str
) -> tuple[Optional[discord.Message], dict[str, Optional[int] | str]]:
    bot_user = bot.user
    bot_id = getattr(bot_user, "id", None)
    session_manager_id = (CHANNELS.get("session_manager") if isinstance(CHANNELS, dict) else None) or ""
    if not str(session_manager_id).isdigit() or not bot_id:
        return None, parse_panel_footer("")

    try:
        session_manager = await bot.fetch_channel(int(session_manager_id))
    except Exception:
        session_manager = None
    if not isinstance(session_manager, discord.TextChannel):
        return None, parse_panel_footer("")

    candidates: list[discord.Message] = []
    try:
        candidates = await session_manager.pins()
    except Exception:
        candidates = []
    if not candidates:
        try:
            candidates = [m async for m in session_manager.history(limit=200, oldest_first=False)]
        except Exception:
            candidates = []

    for m in candidates:
        if getattr(getattr(m, "author", None), "id", None) != bot_id:
            continue
        if not m.embeds:
            continue
        footer = getattr(m.embeds[0].footer, "text", "") if m.embeds[0].footer else ""
        if not footer.startswith(f"map_submission_panel:{category_code}"):
            continue
        return m, parse_panel_footer(footer)

    return None, parse_panel_footer("")


async def _handle_get_session(request: web.Request) -> web.StreamResponse:
    bot: discord.Client = request.app["bot"]

    raw_category = (
        request.match_info.get("category")
        or request.query.get("categoryType")
        or request.query.get("category")
        or ""
    )
    category_code = normalize_category_code(raw_category)
    if not category_code:
        return web.json_response(
            {"error": "missing_category", "hint": "Use ?categoryType=p4 or /session/P4"},
            status=400,
        )

    token = (os.getenv("SESSION_API_TOKEN") or "").strip()
    if token:
        auth = (request.headers.get("Authorization") or "").strip()
        if auth != f"Bearer {token}":
            return web.json_response({"error": "unauthorized"}, status=401)

    thread_id = await _find_current_thread_id(bot, category_code=category_code)
    if not thread_id:
        return web.json_response({"error": "no_active_session", "category": category_code}, status=404)

    try:
        ch = await bot.fetch_channel(int(thread_id))
    except Exception:
        ch = None
    if not isinstance(ch, discord.Thread):
        return web.json_response({"error": "thread_not_accessible", "threadId": thread_id}, status=404)

    bot_user_id = getattr(getattr(bot, "user", None), "id", None)
    state = await get_session_marker_state(thread=ch, history_limit=5000, bot_user_id=bot_user_id)
    if not state.get("is_active"):
        return web.json_response({"error": "no_active_session", "category": category_code}, status=404)

    try:
        data: dict[str, Any] = await collect_session_maps(
            thread=ch,
            category_code=category_code,
            history_limit=5000,
            bot_user_id=bot_user_id,
        )
    except Exception:
        logger.exception("Failed to collect session maps (thread=%s, category=%s)", thread_id, category_code)
        return web.json_response({"error": "failed_to_collect"}, status=500)

    return web.json_response(data, status=200)


async def _handle_get_auth(request: web.Request) -> web.StreamResponse:
    bot: discord.Client = request.app["bot"]

    raw_token = (request.query.get("token") or "").strip()
    auth_header = (request.headers.get("Authorization") or "").strip()
    if not raw_token and auth_header.lower().startswith("bearer "):
        raw_token = auth_header.split(" ", 1)[-1].strip()

    if not raw_token:
        return web.json_response(
            {"error": "missing_token", "hint": "Send ?token=... or Authorization: Bearer <token>"},
            status=400,
        )

    record = await find_auth_record_by_token(bot=bot, token=raw_token)
    if not record:
        return web.json_response({"error": "invalid_token"}, status=404)

    user_id = record.get("user_id")
    if not isinstance(user_id, int):
        try:
            user_id = int(user_id)
        except Exception:
            user_id = None
    if not user_id:
        return web.json_response({"error": "invalid_record"}, status=500)

    # Resolve user + member info.
    user = bot.get_user(user_id)
    if not user:
        try:
            user = await bot.fetch_user(user_id)
        except Exception:
            user = None

    guild_id = record.get("guild_id")
    guild = bot.get_guild(int(guild_id)) if guild_id else None
    member = None
    if guild:
        member = guild.get_member(user_id)
        if not member:
            try:
                member = await guild.fetch_member(user_id)  # type: ignore[attr-defined]
            except Exception:
                member = None

    avatar_url = None
    if member and member.display_avatar:
        avatar_url = str(member.display_avatar.url)
    elif user and user.display_avatar:
        avatar_url = str(user.display_avatar.url)

    roles = []
    if member:
        for role in member.roles:
            if role.is_default():
                continue
            roles.append({"id": int(role.id), "name": str(role.name)})

    display_name = None
    if member:
        display_name = member.display_name
    elif user:
        display_name = user.name

    return web.json_response(
        {
            "ok": True,
            "token": raw_token,
            "user": {
                "id": int(user_id),
                "name": display_name or str(user_id),
                "username": str(user) if user else str(user_id),
                "avatar": avatar_url,
                "roles": roles,
            },
            "record": {
                "created_at": record.get("created_at"),
                "guild_id": record.get("guild_id"),
            },
        },
        status=200,
    )


async def _handle_post_review(request: web.Request) -> web.StreamResponse:
    bot: discord.Client = request.app["bot"]

    token = (os.getenv("SESSION_API_TOKEN") or "").strip()
    if token:
        auth = (request.headers.get("Authorization") or "").strip()
        if auth != f"Bearer {token}":
            return web.json_response({"error": "unauthorized"}, status=401)

    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)

    if not isinstance(payload, dict):
        return web.json_response({"error": "invalid_payload"}, status=400)

    schema_version = payload.get("schemaVersion")
    if schema_version != 1:
        return web.json_response({"error": "unsupported_schema", "schemaVersion": schema_version}, status=400)

    session_obj = payload.get("session") or {}
    if not isinstance(session_obj, dict):
        session_obj = {}

    raw_category = (
        request.match_info.get("category")
        or session_obj.get("category")
        or payload.get("category")
        or payload.get("categoryType")
        or request.query.get("categoryType")
        or ""
    )
    category_code = normalize_category_code(str(raw_category))
    if not category_code:
        return web.json_response({"error": "missing_category"}, status=400)

    panel_msg, panel_meta = await _find_panel_message_and_meta(bot, category_code=category_code)
    current_session_no = panel_meta.get("current_no")
    last_no = int(panel_meta.get("last") or 0)

    try:
        thread = await get_category_thread(bot, category_code=category_code)
    except Exception:
        thread = None
    if not isinstance(thread, discord.Thread):
        return web.json_response({"error": "no_active_session", "category": category_code}, status=404)

    bot_user_id = getattr(getattr(bot, "user", None), "id", None)
    state = await get_session_marker_state(thread=thread, history_limit=5000, bot_user_id=bot_user_id)
    if not state.get("is_active") or not current_session_no:
        return web.json_response({"error": "no_active_session", "category": category_code}, status=404)

    thread_id = int(thread.id)

    items = payload.get("items")
    if not isinstance(items, list):
        return web.json_response({"error": "missing_items"}, status=400)

    # Convert export payload -> parts -> post into thread -> close thread.
    try:
        parts = build_review_parts_from_export_payload_v1(
            category_code=category_code,
            items=[it for it in items if isinstance(it, dict)],
        )

        reviewer_name = str(payload.get("reviewer") or payload.get("reviewerName") or "Maps Reviewer")
        reviewer_user_id = session_obj.get("reviewerUserId")
        if isinstance(reviewer_user_id, str) and reviewer_user_id.isdigit():
            reviewer_user_id = int(reviewer_user_id)
        if not isinstance(reviewer_user_id, int):
            reviewer_user_id = None

        # Resolve reviewer identity based on roles (public + mapcrew) if possible.
        if reviewer_user_id and panel_msg and panel_msg.guild:
            guild = panel_msg.guild
            member = guild.get_member(reviewer_user_id)
            if not member:
                try:
                    member = await guild.fetch_member(reviewer_user_id)  # type: ignore[attr-defined]
                except Exception:
                    member = None
            if member and has_public_role(member) and has_mapcrew_role(member):
                reviewer_name = member.display_name
            else:
                reviewer_name = "Private Member"

        posted = await post_review_results_and_close_thread(
            bot=bot,
            category_code=category_code,
            thread_id=int(thread_id),
            session_no=int(current_session_no),
            parts=parts,
            reviewer_name=reviewer_name,
        )
    except ValueError as exc:
        return web.json_response(
            {
                "error": "cannot_post",
                "detail": str(exc),
                "category": category_code,
                "threadId": int(thread_id),
            },
            status=400,
        )
    except Exception:
        logger.exception("Failed to post review (category=%s thread=%s)", category_code, thread_id)
        return web.json_response({"error": "failed_to_post"}, status=500)

    # Close session (marker) and clear current session in the panel embed.
    last_finished_ts: int | None = None
    try:
        end_msg = await thread.send(build_end_marker_message(category_code=category_code, session_no=int(current_session_no)))
        if end_msg and end_msg.created_at:
            last_finished_ts = int(end_msg.created_at.timestamp())
    except Exception:
        logger.exception("Failed to send session end marker (%s)", category_code)

    try:
        if panel_msg and panel_msg.embeds:
            cleared_embed = build_submission_panel_embed(
                category_code,
                last_session_no=max(last_no, int(current_session_no)),
                is_locked=bool(getattr(thread, "locked", False)),
                last_finished_ts=last_finished_ts,
            )
            await panel_msg.edit(embeds=[cleared_embed])
    except Exception:
        logger.exception("Failed to clear current session in panel embed for %s", category_code)

    # Auto-create the next session (to match the Discord button behavior).
    auto_next = _env_bool("SESSION_API_AUTO_CREATE_NEXT_SESSION", default=True)
    # Allow turning it off per-request.
    if str(request.query.get("autoNext") or "").strip().lower() in ("0", "false", "no", "n", "off"):
        auto_next = False

    new_thread_id: int | None = None
    new_session_no: int | None = None
    if auto_next and panel_msg:
        try:
            result = await _start_new_session_for_panel(
                client=bot,
                panel_msg=panel_msg,
                category_code=category_code,
                last_finished_ts=last_finished_ts,
            )
            new_thread_id = int(result.get("thread_id")) if result.get("thread_id") else None
            new_session_no = int(result.get("session_no")) if result.get("session_no") else None
        except Exception as exc:
            logger.exception("Failed to auto-create next session (%s)", category_code)
            # Don't fail the review POST; just report the failure.
            return web.json_response(
                {
                    "ok": True,
                    "category": category_code,
                    "threadId": int(thread_id),
                    "sessionNo": int(current_session_no),
                    "jumpUrl": getattr(posted, "jump_url", None),
                    "autoNext": {"ok": False, "error": str(exc)},
                },
                status=200,
            )

    return web.json_response(
        {
            "ok": True,
            "category": category_code,
            "threadId": int(thread_id),
            "sessionNo": int(current_session_no),
            "jumpUrl": getattr(posted, "jump_url", None),
            "autoNext": {"ok": bool(new_thread_id), "threadId": new_thread_id, "sessionNo": new_session_no},
        },
        status=200,
    )


async def start_session_api(bot: discord.Client) -> Optional[web.AppRunner]:
    """Starts the API if enabled via env. Returns the runner (for cleanup)."""
    if not _env_bool("SESSION_API_ENABLED", default=False):
        return None

    host = (os.getenv("SESSION_API_HOST") or "127.0.0.1").strip()
    port_raw = (os.getenv("SESSION_API_PORT") or "8765").strip()
    try:
        port = int(port_raw)
    except Exception:
        port = 8765

    app = web.Application()
    app["bot"] = bot
    app.router.add_get("/session", _handle_get_session)
    app.router.add_get("/session/{category}", _handle_get_session)
    app.router.add_get("/auth", _handle_get_auth)
    app.router.add_post("/session/review", _handle_post_review)
    app.router.add_post("/session/{category}/review", _handle_post_review)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    logger.info("Session API started on http://%s:%s", host, port)
    return runner


async def stop_session_api(runner: Optional[web.AppRunner]) -> None:
    if not runner:
        return
    try:
        await runner.cleanup()
    except Exception:
        logger.exception("Failed to stop Session API")

