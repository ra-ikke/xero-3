"""Interaction helpers (ported from the legacy JavaScript helpers)."""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

import discord

logger = logging.getLogger(__name__)


async def safe_reply(
    interaction: discord.Interaction,
    content: str,
    *,
    ephemeral: bool = False,
    **kwargs: Any,
) -> bool:
    """
    Replies to an interaction safely, using followup when the response is already done.

    Returns True on success, False otherwise.
    """
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content=content, ephemeral=ephemeral, **kwargs)
        else:
            await interaction.response.send_message(content=content, ephemeral=ephemeral, **kwargs)
        return True
    except Exception:
        logger.exception("Error in safe_reply")
        return False


def extract_report_info(title: str) -> Optional[dict[str, str]]:
    """Extracts report info from a title in the format: [Pxx] @12345"""
    if not title:
        return None
    match = re.search(r"\[(P\d+)\]\s*(@\d+)", title)
    if not match:
        return None
    return {"category": match.group(1), "mapCode": match.group(2)}


async def handle_interaction_error(interaction: discord.Interaction, error: Exception) -> None:
    """Logs the exception and replies ephemerally with a user-friendly message."""
    logger.exception("Interaction error: %s", error)
    message = getattr(error, "message", None) or str(error) or "An error occurred while processing your request."
    await safe_reply(interaction, message, ephemeral=True)

