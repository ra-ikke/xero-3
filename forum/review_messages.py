"""Utilities to locate existing review messages in a Discord channel."""

from __future__ import annotations

import logging
from typing import Optional

import discord

logger = logging.getLogger(__name__)


async def find_category_messages(
    channel: discord.abc.Messageable, category_codes: list[str]
) -> dict[str, discord.Message]:
    """
    Scans recent messages and tries to find one message per category.

    Matching rule: message has an embed and embed.title contains the category code.
    """
    found: dict[str, discord.Message] = {}
    try:
        messages = [m async for m in channel.history(limit=100, oldest_first=False)]
    except Exception:
        logger.exception("Failed to fetch channel history for review messages")
        return found

    for code in category_codes:
        for msg in messages:
            if not msg.embeds:
                continue
            title = msg.embeds[0].title or ""
            if code in title:
                found[code] = msg
                break
    return found


async def find_verification_message(channel: discord.abc.Messageable, bot_user_id: int) -> Optional[discord.Message]:
    """Finds an existing 'Latest Reviews' embed message posted by the bot."""
    try:
        messages = [m async for m in channel.history(limit=100, oldest_first=False)]
    except Exception:
        logger.exception("Failed to fetch channel history for verification message")
        return None

    for msg in messages:
        if msg.author.id != bot_user_id:
            continue
        if not msg.embeds:
            continue
        if msg.embeds[0].title == "📝 Latest Reviews":
            return msg
    return None

