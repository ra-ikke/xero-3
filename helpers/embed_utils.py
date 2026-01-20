"""Embed utilities (ported from the legacy JavaScript helpers)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import discord


def create_reply_embed(display_name: str, action: str, *, color: str = "#FFA500") -> discord.Embed:
    """Creates a small action embed used for standardized replies."""
    embed = discord.Embed(
        description=f"**Report handled by {display_name}**\nAction taken: {action}",
        color=int(color.replace("#", "0x"), 16),
        timestamp=datetime.now(timezone.utc),
    )
    return embed


def update_embed_fields(embed: discord.Embed, updates: list[dict[str, Any]]) -> discord.Embed:
    """
    Updates fields of an existing embed.

    - Existing fields with the same name are updated.
    - New fields are appended.
    """
    fields = list(getattr(embed, "fields", []))
    updated_fields: list[discord.EmbedField] = []
    for field in fields:
        update = next((u for u in updates if u.get("name") == field.name), None)
        if update:
            updated_fields.append(discord.EmbedField(name=field.name, value=update.get("value", field.value), inline=field.inline))
        else:
            updated_fields.append(field)

    for update in updates:
        if not any(f.name == update.get("name") for f in updated_fields):
            updated_fields.append(
                discord.EmbedField(name=update.get("name", ""), value=update.get("value", ""), inline=bool(update.get("inline", False)))
            )

    new_embed = embed.copy()
    new_embed.clear_fields()
    for f in updated_fields:
        new_embed.add_field(name=f.name, value=f.value, inline=f.inline)
    return new_embed

