"""Poll update helpers (ported from legacy add_discussion_option.js)."""

from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

import discord

from resources.category_list import CATEGORY_LIST

logger = logging.getLogger(__name__)

_EMOJI_OPTIONS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


def _find_category(code: str) -> Optional[dict]:
    return next((c for c in CATEGORY_LIST if c.get("name") == code), None)


async def _fetch_discussion_messages(
    thread: discord.Thread, *, limit: int = 60
) -> tuple[Optional[discord.Message], Optional[discord.Message]]:
    """
    Returns (disc_info_message, poll_message) using an oldest-first scan.
    """
    messages = [m async for m in thread.history(limit=limit, oldest_first=True)]
    if not messages:
        return None, None

    disc_info = messages[0]
    poll = next(
        (
            m
            for m in messages[1:]
            if m.content
            and ("Voting poll:" in m.content or "Awaiting vote options" in m.content)
        ),
        None,
    )
    return disc_info, poll


def _parse_current_poll_emojis(poll_content: str) -> list[str]:
    current: list[str] = []
    for line in (poll_content or "").splitlines():
        match = re.match(r"^(\S+?)\s+-", line.strip())
        if match:
            emoji = match.group(1)
            if emoji in _EMOJI_OPTIONS:
                current.append(emoji)
    return current


def _pick_next_emoji(poll_content: str) -> Optional[str]:
    if "Awaiting vote options" in (poll_content or ""):
        return _EMOJI_OPTIONS[0]
    used = set(_parse_current_poll_emojis(poll_content))
    return next((e for e in _EMOJI_OPTIONS if e not in used), None)


def _build_new_poll_content(poll_content: str, new_line: str) -> str:
    if "Awaiting vote options" in (poll_content or ""):
        return f"**Voting poll:**\n{new_line}"
    return f"{poll_content}\n{new_line}".strip()


async def add_discussion_option(
    interaction: discord.Interaction,
    *,
    option_type: str,
    description: Optional[str] = None,
    target_category_code: Optional[str] = None,
) -> None:
    """
    Adds a new option line to the poll message inside the current discussion thread.
    """
    thread = interaction.channel
    if not isinstance(thread, discord.Thread):
        await interaction.followup.send(
            content="This action can only be used within a discussion thread.",
            ephemeral=True,
        )
        return

    disc_info, poll = await _fetch_discussion_messages(thread)
    if not disc_info or not disc_info.content or "New map discussion" not in disc_info.content:
        await interaction.followup.send(
            content="This action can only be used on discussion threads started by the bot.",
            ephemeral=True,
        )
        return

    if not poll or not poll.content:
        await interaction.followup.send(
            content="Could not find the poll message in this thread, or it is empty.",
            ephemeral=True,
        )
        return

    option_type = (option_type or "").strip().upper()
    if option_type not in {"PERM", "EDIT", "DEPERM", "KEEP", "MOVE", "REJECT"}:
        await interaction.followup.send(
            content="Invalid option type.",
            ephemeral=True,
        )
        return

    if option_type == "MOVE":
        if not target_category_code:
            await interaction.followup.send(
                content="For the 'MOVE' option type, you must select a target category.",
                ephemeral=True,
            )
            return
        cat = _find_category(target_category_code.strip().upper())
        if not cat:
            await interaction.followup.send(
                content=f"Invalid target category selected: {target_category_code}.",
                ephemeral=True,
            )
            return
        dynamic_description = f"Move to {cat.get('description', cat.get('name', target_category_code))}"
    else:
        if not description or not description.strip():
            await interaction.followup.send(
                content="Please provide a description for this option type.",
                ephemeral=True,
            )
            return
        dynamic_description = description.strip()

    new_emoji = _pick_next_emoji(poll.content)
    if not new_emoji:
        await interaction.followup.send(
            content=f"It is not possible to create more than {len(_EMOJI_OPTIONS)} options.",
            ephemeral=True,
        )
        return

    new_option_line = f"{new_emoji} - [{option_type}] {dynamic_description}"
    new_poll_content = _build_new_poll_content(poll.content, new_option_line)

    await poll.edit(content=new_poll_content)

    # In the legacy bot, reactions are added to the starter message. In this Python bot, we react on the poll message.
    try:
        await poll.add_reaction(new_emoji)
    except Exception:
        logger.exception("Failed to react with %s on poll message %s", new_emoji, poll.id)

    await interaction.followup.send(
        content=f"A new option has been added to the poll: ([{option_type}] {dynamic_description})",
        ephemeral=True,
    )

