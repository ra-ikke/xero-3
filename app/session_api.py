"""Small HTTP API to read the *current* submission session as JSON.

This is intended for external tooling (e.g. desktop reviewer) that wants to
fetch the active session content without downloading a TXT from Discord.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional
from datetime import datetime
from io import BytesIO
import json

import discord
from aiohttp import web

from helpers.discussion import create_discussion
from helpers.session_export import collect_session_maps, get_session_marker_state, normalize_category_code
from helpers.auth_token import find_auth_record_by_token
from helpers.validation_utils import has_mapcrew_role, has_public_role, validate_map_code
from helpers.submission_facade import (
    build_end_marker_message,
    build_review_parts_from_export_payload_v1,
    build_review_messages_from_parts,
    build_start_marker_message,
    get_category_thread,
    get_or_create_category_thread,
    post_review_results_and_close_thread,
    _start_new_session_for_panel,
)
from helpers.submission_panel import build_submission_panel_embed
from helpers.submission_panel import parse_panel_footer
from resources.category_list import CATEGORY_LIST
from resources.channels import CHANNELS
from resources.get_tag import RACING_DISCUSSION_SENTINEL, resolve_discussion_category_code
from service.map_service import draw_map_png, draw_map_url, fetch_map
from ui.votecrew_review_view import VotecrewReviewView

logger = logging.getLogger(__name__)


def _env_bool(key: str, default: bool = False) -> bool:
    raw = (os.getenv(key, str(default)) or "").strip().lower()
    return raw in ("1", "true", "yes", "y", "on")


_VALID_DISC_TYPES = frozenset({"PERM", "EDIT", "DEPERM", "OTHER"})


def _extract_user_token(request: web.Request) -> str:
    raw_token = (request.query.get("token") or "").strip()
    auth_header = (request.headers.get("Authorization") or "").strip()
    if not raw_token and auth_header.lower().startswith("bearer "):
        raw_token = auth_header.split(" ", 1)[-1].strip()
    return raw_token


def _normalize_mapcode_from_path(raw: str) -> tuple[Optional[str], Optional[str]]:
    validation = validate_map_code((raw or "").strip(), min_digits=1)
    if not validation.is_valid:
        return None, "invalid_map_code"
    return validation.formatted_code, None


def _find_category_for_map_type(map_type: str) -> Optional[dict[str, Any]]:
    return next((cat for cat in CATEGORY_LIST if cat.get("name") == map_type), None)


async def _resolve_user_from_token(
    bot: discord.Client,
    token: str,
) -> tuple[Optional[discord.User], Optional[dict[str, Any]], Optional[str]]:
    if not token:
        return None, None, "missing_token"

    record = await find_auth_record_by_token(bot=bot, token=token)
    if not record:
        return None, None, "invalid_token"

    user_id = record.get("user_id")
    if not isinstance(user_id, int):
        try:
            user_id = int(user_id)
        except Exception:
            user_id = None
    if not user_id:
        return None, None, "invalid_record"

    user = bot.get_user(user_id)
    if not user:
        try:
            user = await bot.fetch_user(user_id)
        except Exception:
            user = None
    if not user:
        return None, None, "user_not_found"

    return user, record, None


async def _resolve_votecrew_name(
    bot: discord.Client,
    *,
    reviewer_user_id: Optional[int],
    guild: Optional[discord.Guild] = None,
) -> str:
    if not reviewer_user_id:
        return "Unknown Votecrew"
    member = None
    guild = guild or (bot.guilds[0] if bot.guilds else None)
    if guild:
        member = guild.get_member(reviewer_user_id)
        if not member:
            try:
                member = await guild.fetch_member(reviewer_user_id)  # type: ignore[attr-defined]
            except Exception:
                member = None
    if member:
        return member.display_name
    return "Unknown Votecrew"


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


async def _finalize_review_post(
    *,
    bot: discord.Client,
    category_code: str,
    thread: discord.Thread,
    panel_msg: Optional[discord.Message],
    last_no: int,
    current_session_no: int,
    auto_next: bool,
) -> dict[str, Optional[int] | bool]:
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
            return {"auto_next_ok": False, "auto_next_error": str(exc)}

    return {"auto_next_ok": bool(new_thread_id), "auto_next_thread_id": new_thread_id, "auto_next_session_no": new_session_no}


async def _handle_get_session(request: web.Request) -> web.StreamResponse:
    bot: discord.Client = request.app["bot"]

    raw_category = (
        request.match_info.get("category")
        or request.query.get("categoryType")
        or request.query.get("category")
        or ""
    )
    logger.info(
        "GET /session received path=%s query=%s match_info=%s raw_category=%r",
        request.path_qs,
        dict(request.query),
        dict(request.match_info),
        raw_category,
    )
    category_code = normalize_category_code(raw_category)
    logger.info("GET /session normalized category raw=%r -> %r", raw_category, category_code)
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
        logger.warning(
            "GET /session no active thread found for category=%s raw_category=%r query=%s",
            category_code,
            raw_category,
            dict(request.query),
        )
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
        logger.warning(
            "GET /session thread exists but session is inactive category=%s thread_id=%s state=%s",
            category_code,
            thread_id,
            state,
        )
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


async def _handle_get_map(request: web.Request) -> web.StreamResponse:
    raw_mapcode = request.match_info.get("mapcode") or ""
    code, error = _normalize_mapcode_from_path(raw_mapcode)
    if error or not code:
        return web.json_response({"error": error or "invalid_map_code"}, status=400)

    try:
        map_data = await fetch_map(code)
    except Exception:
        logger.exception("Failed to fetch map %s", code)
        return web.json_response({"error": "failed_to_fetch", "map": code}, status=500)

    if not map_data:
        return web.json_response({"error": "map_not_found", "map": code}, status=404)

    payload = {"code": code, "xml": map_data.xml, "raw": False}
    image_url = await draw_map_url(payload)
    if not image_url or not isinstance(image_url, str) or not image_url.startswith("http"):
        image_url = None

    category = _find_category_for_map_type(map_data.map_type)
    category_name = category.get("name") if category else (map_data.map_type or None)
    category_emoji = category.get("emoji") if category else None

    return web.json_response(
        {
            "status": "received",
            "content": {
                "map": code,
                "author": map_data.maker or "Unknown Author",
                "category": category_name,
                "xml": map_data.xml,
            },
            "imageUrl": image_url,
            "categoryEmoji": category_emoji,
        },
        status=200,
    )


async def _handle_get_map_image(request: web.Request) -> web.StreamResponse:
    raw_mapcode = request.match_info.get("mapcode") or ""
    code, error = _normalize_mapcode_from_path(raw_mapcode)
    if error or not code:
        return web.json_response({"error": error or "invalid_map_code"}, status=400)

    try:
        map_data = await fetch_map(code)
    except Exception:
        logger.exception("Failed to fetch map %s", code)
        return web.json_response({"error": "failed_to_fetch", "map": code}, status=500)

    if not map_data:
        return web.json_response({"error": "map_not_found", "map": code}, status=404)

    payload = {"code": code, "xml": map_data.xml, "raw": False}
    fmt = (request.query.get("format") or "png").strip().lower()

    if fmt == "url":
        image_url = await draw_map_url(payload)
        if not image_url or not isinstance(image_url, str):
            return web.json_response({"error": "failed_to_render", "map": code}, status=500)
        return web.Response(text=image_url, content_type="text/plain", status=200)

    png = await draw_map_png(payload)
    if not png:
        return web.json_response({"error": "failed_to_render", "map": code}, status=500)

    return web.Response(body=png, content_type="image/png", status=200)


async def _handle_get_auth(request: web.Request) -> web.StreamResponse:
    bot: discord.Client = request.app["bot"]

    raw_token = _extract_user_token(request)
    if not raw_token:
        return web.json_response(
            {"error": "missing_token", "hint": "Send ?token=... or Authorization: Bearer <token>"},
            status=400,
        )

    user, record, error = await _resolve_user_from_token(bot, raw_token)
    if error == "invalid_token":
        return web.json_response({"error": "invalid_token"}, status=404)
    if error == "invalid_record":
        return web.json_response({"error": "invalid_record"}, status=500)
    if error == "user_not_found" or not user or not record:
        return web.json_response({"error": "user_not_found"}, status=404)

    user_id = int(user.id)

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


async def _handle_post_discussion(request: web.Request) -> web.StreamResponse:
    bot: discord.Client = request.app["bot"]

    raw_token = _extract_user_token(request)
    user, record, auth_error = await _resolve_user_from_token(bot, raw_token)
    if auth_error == "missing_token":
        return web.json_response(
            {
                "error": "missing_token",
                "hint": "Send ?token=... or Authorization: Bearer <user_token>",
            },
            status=400,
        )
    if auth_error == "invalid_token":
        return web.json_response({"error": "invalid_token"}, status=401)
    if auth_error == "invalid_record":
        return web.json_response({"error": "invalid_record"}, status=500)
    if auth_error == "user_not_found" or not user or not record:
        return web.json_response({"error": "user_not_found"}, status=404)

    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)

    if not isinstance(payload, dict):
        return web.json_response({"error": "invalid_payload"}, status=400)

    map_code = str(payload.get("mapCode") or payload.get("code") or "").strip()
    if not map_code:
        return web.json_response({"error": "missing_map_code"}, status=400)

    raw_category = str(
        payload.get("category")
        or payload.get("categoryType")
        or payload.get("categoryCode")
        or ""
    ).strip()
    category_code = normalize_category_code(raw_category)
    if raw_category.strip().upper() == RACING_DISCUSSION_SENTINEL:
        category_code = RACING_DISCUSSION_SENTINEL
    resolved_category = resolve_discussion_category_code(category_code or raw_category)
    if not resolved_category:
        return web.json_response({"error": "missing_category"}, status=400)
    category_code = resolved_category

    raw_disc_type = str(payload.get("discType") or payload.get("disc_type") or "").strip().upper()
    if not raw_disc_type:
        return web.json_response({"error": "missing_disc_type"}, status=400)
    if raw_disc_type not in _VALID_DISC_TYPES:
        return web.json_response(
            {
                "error": "invalid_disc_type",
                "hint": "Use PERM, EDIT, DEPERM or OTHER.",
                "discType": raw_disc_type,
            },
            status=400,
        )

    if raw_disc_type == "OTHER":
        disc_description = str(
            payload.get("description") or payload.get("discDescription") or ""
        ).strip()
        if not disc_description:
            return web.json_response(
                {
                    "error": "missing_description",
                    "hint": "description is required when discType is OTHER.",
                },
                status=400,
            )
        disc_type = disc_description
    else:
        disc_type = raw_disc_type

    notify = bool(payload.get("notify"))

    result = await create_discussion(
        client=bot,
        map_code=map_code,
        category_code=category_code,
        disc_type=disc_type,
        notify=notify,
        user=user,
        interaction=None,
    )

    if not result.get("success"):
        return web.json_response(
            {
                "error": "cannot_create_discussion",
                "detail": result.get("error") or "Failed to create discussion.",
            },
            status=400,
        )

    thread = result.get("thread")
    map_data = result.get("map_data") or {}
    display_name = (
        getattr(user, "global_name", None)
        or getattr(user, "display_name", None)
        or str(user)
    )

    return web.json_response(
        {
            "ok": True,
            "threadId": int(thread.id) if thread else None,
            "jumpUrl": getattr(thread, "jump_url", None) if thread else None,
            "mapCode": map_data.get("code"),
            "mapAuthor": map_data.get("author"),
            "category": category_code,
            "discType": disc_type,
            "notify": notify,
            "requestedBy": {
                "id": int(user.id),
                "name": display_name,
                "username": str(user),
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

    votecrew_flag = bool(payload.get("votecrew"))
    post_as_private = bool(payload.get("postAsPrivate"))

    if post_as_private and not votecrew_flag:
        reviewer_name = "Private Member"
    # Resolve reviewer identity based on roles (public + mapcrew) if possible.
    elif reviewer_user_id and panel_msg and panel_msg.guild:
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
    if votecrew_flag:
        votecrew_channel_id = (CHANNELS.get("mc_votecrew_review") if isinstance(CHANNELS, dict) else None) or ""
        if not str(votecrew_channel_id).isdigit():
            return web.json_response({"error": "votecrew_channel_not_configured"}, status=500)

        try:
            votecrew_channel = await bot.fetch_channel(int(votecrew_channel_id))
        except Exception:
            votecrew_channel = None
        if not isinstance(votecrew_channel, discord.TextChannel):
            return web.json_response({"error": "votecrew_channel_unavailable"}, status=500)

        votecrew_name = await _resolve_votecrew_name(
            bot,
            reviewer_user_id=reviewer_user_id,
            guild=panel_msg.guild if panel_msg else None,
        )
        chunks = build_review_messages_from_parts(
            category_code=category_code,
            session_no=int(current_session_no),
            parts=parts,
            reviewer_name=reviewer_name,
        )
        if not chunks:
            chunks = [f"**{category_code} — Session review results (Session #{int(current_session_no)})**\n(no content)"]

        safe_payload = dict(payload)
        if "userToken" in safe_payload:
            safe_payload["userToken"] = "***"
        payload_bytes = json.dumps(safe_payload, ensure_ascii=False, indent=2).encode("utf-8")
        today = datetime.utcnow().date().isoformat()
        filename = f"votecrew_{category_code}_{today}.json"
        file = discord.File(BytesIO(payload_bytes), filename=filename)

        embed = discord.Embed(
            title="Votecrew review pending",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Category", value=str(category_code), inline=True)
        embed.add_field(name="Session", value=str(current_session_no), inline=True)
        embed.add_field(name="Thread ID", value=str(thread_id), inline=True)
        embed.add_field(name="Votecrew", value=str(votecrew_name), inline=True)
        embed.add_field(name="Reviewer User ID", value=str(reviewer_user_id or "Unknown"), inline=True)
        embed.add_field(name="Status", value="Pending", inline=True)

        view = VotecrewReviewView()
        first_content = chunks[0]
        try:
            posted = await votecrew_channel.send(content=first_content, embed=embed, view=view, file=file)
            for extra in chunks[1:]:
                await votecrew_channel.send(content=extra)
        except Exception:
            logger.exception("Failed to post votecrew review message")
            return web.json_response({"error": "failed_to_post_votecrew"}, status=500)

        return web.json_response(
            {
                "ok": True,
                "queued": True,
                "category": category_code,
                "threadId": int(thread_id),
                "sessionNo": int(current_session_no),
                "messageId": int(posted.id),
                "jumpUrl": getattr(posted, "jump_url", None),
            },
            status=200,
        )

    # Convert export payload -> parts -> post into thread -> close thread.
    try:
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

    auto_next = _env_bool("SESSION_API_AUTO_CREATE_NEXT_SESSION", default=True)
    # Allow turning it off per-request.
    if str(request.query.get("autoNext") or "").strip().lower() in ("0", "false", "no", "n", "off"):
        auto_next = False
    result = await _finalize_review_post(
        bot=bot,
        category_code=category_code,
        thread=thread,
        panel_msg=panel_msg,
        last_no=last_no,
        current_session_no=int(current_session_no),
        auto_next=auto_next,
    )

    auto_next_ok = bool(result.get("auto_next_ok"))
    return web.json_response(
        {
            "ok": True,
            "category": category_code,
            "threadId": int(thread_id),
            "sessionNo": int(current_session_no),
            "jumpUrl": getattr(posted, "jump_url", None),
            "autoNext": {
                "ok": auto_next_ok,
                "threadId": result.get("auto_next_thread_id"),
                "sessionNo": result.get("auto_next_session_no"),
            },
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
    app.router.add_get("/map/{mapcode}/image", _handle_get_map_image)
    app.router.add_get("/map/{mapcode}", _handle_get_map)
    app.router.add_post("/session/review", _handle_post_review)
    app.router.add_post("/session/{category}/review", _handle_post_review)
    app.router.add_post("/discussion", _handle_post_discussion)

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

