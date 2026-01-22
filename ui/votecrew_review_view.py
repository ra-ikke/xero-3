"""Persistent UI controls for votecrew review approvals."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import discord

from helpers.interaction_utils import safe_reply
from helpers.session_export import get_session_marker_state
from helpers.submission_facade import (
    build_end_marker_message,
    build_review_parts_from_export_payload_v1,
    post_review_results_and_close_thread,
    _start_new_session_for_panel,
)
from helpers.submission_panel import build_submission_panel_embed, parse_panel_footer
from helpers.validation_utils import has_mapcrew_role, has_public_role
from resources.channels import CHANNELS

logger = logging.getLogger(__name__)


def _env_bool(key: str, default: bool = False) -> bool:
    raw = (os.getenv(key, str(default)) or "").strip().lower()
    return raw in ("1", "true", "yes", "y", "on")


def _has_manage_permission(interaction: discord.Interaction) -> bool:
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    channel = interaction.channel
    if not member or not channel:
        return False
    perms = channel.permissions_for(member)
    return bool(perms.manage_threads or perms.manage_messages)


async def _find_panel_message_and_meta(
    bot: discord.Client, *, category_code: str
) -> tuple[Optional[discord.Message], dict[str, Optional[int] | str]]:
    session_manager_id = (CHANNELS.get("session_manager") if isinstance(CHANNELS, dict) else None) or ""
    if not str(session_manager_id).isdigit():
        return None, parse_panel_footer("")

    try:
        session_manager = await bot.fetch_channel(int(session_manager_id))
    except Exception:
        session_manager = None
    if not isinstance(session_manager, discord.TextChannel):
        return None, parse_panel_footer("")

    bot_user = bot.user
    bot_id = getattr(bot_user, "id", None)
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


def _extract_embed_field(embed: discord.Embed, name: str) -> Optional[str]:
    for field in embed.fields or []:
        if (field.name or "").strip().lower() == name.strip().lower():
            return str(field.value).strip()
    return None


def _parse_votecrew_meta(message: discord.Message) -> dict[str, Optional[str]]:
    embed = message.embeds[0] if message.embeds else None
    if not embed:
        return {"category": None, "session": None, "thread_id": None, "status": None}
    return {
        "category": _extract_embed_field(embed, "Category"),
        "session": _extract_embed_field(embed, "Session"),
        "thread_id": _extract_embed_field(embed, "Thread ID"),
        "status": _extract_embed_field(embed, "Status"),
    }


async def _load_payload_from_message(message: discord.Message) -> dict[str, Any]:
    if not message.attachments:
        raise ValueError("missing_attachment")
    attachment = message.attachments[0]
    data = await attachment.read()
    try:
        payload = json.loads((data or b"").decode("utf-8-sig", errors="replace"))
    except Exception as exc:
        raise ValueError("invalid_json") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid_payload")
    return payload


async def _resolve_reviewer_name(
    *,
    guild: Optional[discord.Guild],
    reviewer_user_id: Optional[int],
) -> str:
    reviewer_name = "Maps Reviewer"
    if not reviewer_user_id or not guild:
        return reviewer_name
    member = guild.get_member(reviewer_user_id)
    if not member:
        try:
            member = await guild.fetch_member(reviewer_user_id)  # type: ignore[attr-defined]
        except Exception:
            member = None
    if member and has_public_role(member) and has_mapcrew_role(member):
        return member.display_name
    return "Private Member"


async def _resolve_votecrew_name(
    *,
    guild: Optional[discord.Guild],
    reviewer_user_id: Optional[int],
) -> str:
    if not reviewer_user_id or not guild:
        return "Unknown Votecrew"
    member = guild.get_member(reviewer_user_id)
    if not member:
        try:
            member = await guild.fetch_member(reviewer_user_id)  # type: ignore[attr-defined]
        except Exception:
            member = None
    if member:
        return member.display_name
    return "Unknown Votecrew"


async def _finalize_session(
    *,
    bot: discord.Client,
    category_code: str,
    thread: discord.Thread,
    panel_msg: Optional[discord.Message],
    last_no: int,
    session_no: int,
) -> dict[str, Optional[int] | bool]:
    last_finished_ts: int | None = None
    try:
        end_msg = await thread.send(build_end_marker_message(category_code=category_code, session_no=int(session_no)))
        if end_msg and end_msg.created_at:
            last_finished_ts = int(end_msg.created_at.timestamp())
    except Exception:
        logger.exception("Failed to send session end marker (%s)", category_code)

    try:
        if panel_msg and panel_msg.embeds:
            cleared_embed = build_submission_panel_embed(
                category_code,
                last_session_no=max(last_no, int(session_no)),
                is_locked=bool(getattr(thread, "locked", False)),
                last_finished_ts=last_finished_ts,
            )
            await panel_msg.edit(embeds=[cleared_embed])
    except Exception:
        logger.exception("Failed to clear current session in panel embed for %s", category_code)

    auto_next = _env_bool("SESSION_API_AUTO_CREATE_NEXT_SESSION", default=True)
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


async def _update_votecrew_message(
    *,
    message: discord.Message,
    status_text: str,
    votecrew_name: Optional[str] = None,
    decided_by: Optional[str] = None,
    disable_buttons: bool = True,
) -> None:
    if not message.embeds:
        return
    embed = message.embeds[0]
    status_lower = status_text.strip().lower()
    if status_lower == "published":
        embed.color = discord.Color.green()
    elif status_lower == "published manually":
        embed.color = discord.Color.blue()
    elif status_lower == "rejected":
        embed.color = discord.Color.red()
    else:
        embed.color = discord.Color.orange()
    fields = list(embed.fields or [])
    if decided_by and not any(f.name.lower() == "decision by" for f in fields):
        fields.append(discord.EmbedField(name="Decision by", value=decided_by, inline=True))
    embed.clear_fields()
    for field in fields:
        name = field.name
        value = field.value
        if name.lower() == "status":
            value = status_text
        elif votecrew_name and name.lower() == "votecrew":
            value = votecrew_name
        elif decided_by and name.lower() == "decision by":
            value = decided_by
        embed.add_field(name=name, value=value, inline=field.inline)
    view = VotecrewReviewView(is_done=disable_buttons)
    await message.edit(embeds=[embed], view=view)


class VotecrewReviewView(discord.ui.View):
    """Persistent view attached to votecrew review requests."""

    def __init__(self, *, is_done: bool = False):
        super().__init__(timeout=None)
        if is_done:
            for item in self.children:
                item.disabled = True

    @discord.ui.button(
        label="Approve review",
        style=discord.ButtonStyle.success,
        custom_id="votecrew_review:approve",
    )
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not _has_manage_permission(interaction):
            await safe_reply(interaction, "Missing permission (requires `manage_threads` or `manage_messages`).", ephemeral=True)
            return

        msg = interaction.message
        if not msg:
            await safe_reply(interaction, "Could not find the source message.", ephemeral=True)
            return

        meta = _parse_votecrew_meta(msg)
        if (meta.get("status") or "").lower().startswith("published"):
            await safe_reply(interaction, "This review is already marked as published.", ephemeral=True)
            return

        thread_id_raw = (meta.get("thread_id") or "").strip()
        session_no_raw = (meta.get("session") or "").strip()
        category_code = (meta.get("category") or "").strip()
        if not thread_id_raw.isdigit() or not session_no_raw.isdigit() or not category_code:
            await safe_reply(interaction, "Missing metadata to approve this review.", ephemeral=True)
            return
        thread_id = int(thread_id_raw)
        session_no = int(session_no_raw)

        try:
            payload = await _load_payload_from_message(msg)
        except ValueError:
            await safe_reply(interaction, "Invalid or missing JSON payload.", ephemeral=True)
            return

        session_obj = payload.get("session") or {}
        if not isinstance(session_obj, dict):
            session_obj = {}
        items = payload.get("items")
        if not isinstance(items, list):
            await safe_reply(interaction, "Invalid JSON payload (missing items).", ephemeral=True)
            return

        reviewer_user_id = session_obj.get("reviewerUserId")
        if isinstance(reviewer_user_id, str) and reviewer_user_id.isdigit():
            reviewer_user_id = int(reviewer_user_id)
        if not isinstance(reviewer_user_id, int):
            reviewer_user_id = None

        reviewer_name = "Private Member"
        votecrew_name = await _resolve_votecrew_name(
            guild=interaction.guild,
            reviewer_user_id=reviewer_user_id,
        )

        try:
            channel = await interaction.client.fetch_channel(thread_id)
        except Exception:
            channel = None
        if not isinstance(channel, discord.Thread):
            await safe_reply(interaction, "I couldn't access the category thread.", ephemeral=True)
            return

        bot_user_id = getattr(getattr(interaction.client, "user", None), "id", None)
        state = await get_session_marker_state(thread=channel, history_limit=5000, bot_user_id=bot_user_id)
        if not state.get("is_active"):
            await safe_reply(interaction, "There is no active session for this category.", ephemeral=True)
            return

        parts = build_review_parts_from_export_payload_v1(
            category_code=category_code,
            items=[it for it in items if isinstance(it, dict)],
        )

        try:
            posted = await post_review_results_and_close_thread(
                bot=interaction.client,
                category_code=category_code,
                thread_id=thread_id,
                session_no=session_no,
                parts=parts,
                reviewer_name=reviewer_name,
            )
        except Exception:
            logger.exception("Failed to approve votecrew review (thread=%s)", thread_id)
            await safe_reply(interaction, "Failed to publish this review.", ephemeral=True)
            return

        panel_msg, panel_meta = await _find_panel_message_and_meta(interaction.client, category_code=category_code)
        last_no = int(panel_meta.get("last") or 0)
        await _finalize_session(
            bot=interaction.client,
            category_code=category_code,
            thread=channel,
            panel_msg=panel_msg,
            last_no=last_no,
            session_no=session_no,
        )

        decided_by = interaction.user.display_name if isinstance(interaction.user, discord.Member) else str(interaction.user)
        await _update_votecrew_message(
            message=msg,
            status_text="Published",
            votecrew_name=votecrew_name,
            decided_by=decided_by,
        )
        await safe_reply(interaction, f"📣 Results posted in the session thread: {posted.jump_url}", ephemeral=True)

    @discord.ui.button(
        label="Approve review manually",
        style=discord.ButtonStyle.primary,
        custom_id="votecrew_review:approve_manual",
    )
    async def approve_manual_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not _has_manage_permission(interaction):
            await safe_reply(interaction, "Missing permission (requires `manage_threads` or `manage_messages`).", ephemeral=True)
            return

        msg = interaction.message
        if not msg:
            await safe_reply(interaction, "Could not find the source message.", ephemeral=True)
            return

        meta = _parse_votecrew_meta(msg)
        if (meta.get("status") or "").lower().startswith("published"):
            await safe_reply(interaction, "This review is already marked as published.", ephemeral=True)
            return

        votecrew_name = None
        try:
            payload = await _load_payload_from_message(msg)
            session_obj = payload.get("session") or {}
            reviewer_user_id = session_obj.get("reviewerUserId")
            if isinstance(reviewer_user_id, str) and reviewer_user_id.isdigit():
                reviewer_user_id = int(reviewer_user_id)
            if not isinstance(reviewer_user_id, int):
                reviewer_user_id = None
            votecrew_name = await _resolve_votecrew_name(
                guild=interaction.guild,
                reviewer_user_id=reviewer_user_id,
            )
        except Exception:
            votecrew_name = None

        decided_by = interaction.user.display_name if isinstance(interaction.user, discord.Member) else str(interaction.user)
        await _update_votecrew_message(
            message=msg,
            status_text="Published manually",
            votecrew_name=votecrew_name,
            decided_by=decided_by,
        )
        await safe_reply(interaction, "Marked as published manually.", ephemeral=True)

    @discord.ui.button(
        label="Reject review",
        style=discord.ButtonStyle.danger,
        custom_id="votecrew_review:reject",
    )
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not _has_manage_permission(interaction):
            await safe_reply(interaction, "Missing permission (requires `manage_threads` or `manage_messages`).", ephemeral=True)
            return

        msg = interaction.message
        if not msg:
            await safe_reply(interaction, "Could not find the source message.", ephemeral=True)
            return

        meta = _parse_votecrew_meta(msg)
        if (meta.get("status") or "").lower().startswith("published"):
            await safe_reply(interaction, "This review is already marked as published.", ephemeral=True)
            return
        if (meta.get("status") or "").lower().startswith("rejected"):
            await safe_reply(interaction, "This review is already marked as rejected.", ephemeral=True)
            return

        votecrew_name = None
        try:
            payload = await _load_payload_from_message(msg)
            session_obj = payload.get("session") or {}
            reviewer_user_id = session_obj.get("reviewerUserId")
            if isinstance(reviewer_user_id, str) and reviewer_user_id.isdigit():
                reviewer_user_id = int(reviewer_user_id)
            if not isinstance(reviewer_user_id, int):
                reviewer_user_id = None
            votecrew_name = await _resolve_votecrew_name(
                guild=interaction.guild,
                reviewer_user_id=reviewer_user_id,
            )
        except Exception:
            votecrew_name = None

        decided_by = interaction.user.display_name if isinstance(interaction.user, discord.Member) else str(interaction.user)
        await _update_votecrew_message(
            message=msg,
            status_text="Rejected",
            votecrew_name=votecrew_name,
            decided_by=decided_by,
        )
        await safe_reply(interaction, "Review rejected.", ephemeral=True)
