"""Thin facade for UI-layer actions.

Views should import from here instead of depending on multiple helper modules.
This keeps UI wiring stable even if helper modules are reorganized.
"""

from __future__ import annotations

from typing import Optional

import discord

from helpers.close_discussion import close_discussion_thread
from helpers.discussion_update import (
    refresh_discussion_information,
    update_discussion_category,
    update_discussion_map_code,
)
from helpers.poll_update import add_discussion_option


async def close_discussion(
    interaction: discord.Interaction,
    *,
    notify: bool,
    option: Optional[str] = None,
    description: Optional[str] = None,
) -> None:
    await close_discussion_thread(interaction, notify=notify, option=option, description=description)


async def refresh_info(interaction: discord.Interaction) -> None:
    await refresh_discussion_information(interaction)


async def update_category(
    interaction: discord.Interaction, *, new_category_code: str
) -> None:
    await update_discussion_category(interaction, new_category_code=new_category_code)


async def update_map_code(
    interaction: discord.Interaction, *, new_map_code: str
) -> None:
    await update_discussion_map_code(interaction, new_map_code=new_map_code)


async def add_poll_option(
    interaction: discord.Interaction,
    *,
    option_type: str,
    description: Optional[str] = None,
    target_category_code: Optional[str] = None,
) -> None:
    await add_discussion_option(
        interaction,
        option_type=option_type,
        description=description,
        target_category_code=target_category_code,
    )
