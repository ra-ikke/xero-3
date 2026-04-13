"""Slash command deployment helpers."""

from __future__ import annotations

import logging
from typing import Any

import discord

from config import PRIVATE_SERVER_IDS

logger = logging.getLogger(__name__)


async def sync_commands(bot: discord.Client) -> dict[str, Any]:
    """Sync public (global) commands and private (guild-only) commands.

    Returns a small summary dict useful for logging.
    """
    summary: dict[str, Any] = {"global": 0, "private": {}}

    # Public global commands
    synced_global = await bot.tree.sync()  # type: ignore[attr-defined]
    summary["global"] = len(synced_global)
    logger.info("Synced %d public global command(s)", len(synced_global))

    # Private guild-only commands
    if not PRIVATE_SERVER_IDS:
        logger.info("No private command guilds configured")
        return summary

    for server_id in PRIVATE_SERVER_IDS:
        guild = discord.Object(id=server_id)
        try:
            # Mirror public global commands into the private guild for immediate availability,
            # while keeping guild-only commands synced there as well.
            bot.tree.copy_global_to(guild=guild)  # type: ignore[attr-defined]
            synced_private = await bot.tree.sync(guild=guild)  # type: ignore[attr-defined]
            summary["private"][str(server_id)] = len(synced_private)
            logger.info("Synced %d private command(s) for guild %s", len(synced_private), server_id)
        except discord.Forbidden as exc:
            # Typically: 403 / 50001 Missing Access (bot not in the guild / wrong ID)
            logger.warning("Skipping private sync for guild %s (missing access): %s", server_id, exc)
            summary["private"][str(server_id)] = "forbidden"
        except discord.HTTPException as exc:
            logger.error("Failed to sync private commands in guild %s: %s", server_id, exc)
            summary["private"][str(server_id)] = f"error:{getattr(exc, 'code', 'http')}"
        except Exception as exc:
            logger.exception("Failed to sync private commands in guild %s: %s", server_id, exc)
            summary["private"][str(server_id)] = "error:exception"

    return summary

