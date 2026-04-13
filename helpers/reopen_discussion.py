"""Helpers to reopen closed discussion threads and restore controls."""

from __future__ import annotations

import logging
import re
from typing import Optional

import discord

from helpers.validation_utils import has_mapcrew_role, has_trial_mapcrew_role
from resources.category_list import CATEGORY_LIST
from resources.emoji import EMOJI_LIST
from resources.get_tag import get_tag_ids

logger = logging.getLogger(__name__)


def _find_category(code: str) -> Optional[dict]:
    return next((c for c in CATEGORY_LIST if c.get("name") == code), None)


def _parse_discussion_metadata(title: str) -> tuple[Optional[dict], str]:
    category_match = re.search(r"\[(P\d+)\]", title or "")
    if not category_match:
        return None, "OTHER"

    original_category = _find_category(category_match.group(1))
    remainder = (title or "").replace(category_match.group(0), "", 1)
    disc_type_match = re.search(r"\[([^\]]+)\]", remainder)
    allowed_types = {"PERM", "DEPERM", "EDIT", "MOVE"}
    disc_type = "OTHER"
    if disc_type_match:
        candidate = disc_type_match.group(1).strip().upper()
        if candidate in allowed_types:
            disc_type = candidate
    return original_category, disc_type


async def _find_controls_message(thread: discord.Thread, *, bot_user_id: int | None) -> Optional[discord.Message]:
    candidates: list[discord.Message] = []
    try:
        candidates.extend(await thread.pins())
    except Exception:
        candidates = []

    if not candidates:
        try:
            candidates = [m async for m in thread.history(limit=80, oldest_first=False)]
        except Exception:
            candidates = []

    for message in candidates:
        if bot_user_id and getattr(getattr(message, "author", None), "id", None) != bot_user_id:
            continue
        content = (message.content or "").strip()
        if "Discussion controls:" in content:
            return message
    return None


async def _find_poll_message(thread: discord.Thread, *, bot_user_id: int | None) -> Optional[discord.Message]:
    try:
        async for message in thread.history(limit=80, oldest_first=True):
            if bot_user_id and getattr(getattr(message, "author", None), "id", None) != bot_user_id:
                continue
            content = (message.content or "").strip()
            if "Voting poll:" in content or "Awaiting vote options" in content:
                return message
    except Exception:
        logger.exception("Failed to scan poll message for thread %s", thread.id)
    return None


async def reopen_discussion_thread(interaction: discord.Interaction) -> None:
    thread = interaction.channel
    if not isinstance(thread, discord.Thread):
        await interaction.followup.send(
            content="This command can only be used inside a discussion thread.",
            ephemeral=True,
        )
        return

    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if not member or not (has_mapcrew_role(member) or has_trial_mapcrew_role(member)):
        await interaction.followup.send(
            content="You need the Mapcrew or Trial Mapcrew role to reopen discussions.",
            ephemeral=True,
        )
        return

    original_category, disc_type = _parse_discussion_metadata(thread.name or "")
    if not original_category:
        await interaction.followup.send(
            content="Could not determine the original discussion category from the thread title.",
            ephemeral=True,
        )
        return

    parent = thread.parent
    applied_tags: Optional[list[discord.ForumTag]] = None
    if isinstance(parent, discord.ForumChannel):
        tag_ids = get_tag_ids(original_category, disc_type, "open") or []
        tag_id_set = set(tag_ids)
        applied_tags = [t for t in parent.available_tags if str(t.id) in tag_id_set]

    reason = f"Reopening discussion thread by {interaction.user}"
    try:
        edit_kwargs: dict = {"locked": False, "archived": False, "reason": reason}
        if applied_tags is not None:
            edit_kwargs["applied_tags"] = applied_tags
        await thread.edit(**edit_kwargs)
    except Exception:
        logger.exception("Failed to reopen discussion thread %s", thread.id)
        await interaction.followup.send(
            content="Failed to reopen this discussion thread.",
            ephemeral=True,
        )
        return

    try:
        from ui.close_discussion_view import CloseDiscussionView

        bot_user_id = getattr(getattr(interaction.client, "user", None), "id", None)
        poll_message = await _find_poll_message(thread, bot_user_id=bot_user_id)
        if poll_message is None:
            poll_emoji = str(EMOJI_LIST.get("_poll", "") or "").strip()
            await thread.send(
                f"{poll_emoji} Awaiting vote options.\nUse `/add_discussion_option` to add a new option.".strip()
            )
        controls_message = await _find_controls_message(thread, bot_user_id=bot_user_id)
        if controls_message:
            await controls_message.edit(view=CloseDiscussionView())
        else:
            await thread.send(
                content="Discussion controls: use **Close** or **Close with notification** to finish the thread.",
                view=CloseDiscussionView(),
            )
    except Exception:
        logger.exception("Failed to restore controls for reopened thread %s", thread.id)
        await interaction.followup.send(
            content="Thread reopened, but failed to restore discussion controls.",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        content="Discussion thread reopened and controls restored.",
        ephemeral=True,
    )
