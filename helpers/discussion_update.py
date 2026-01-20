"""Discussion update helpers (ported from legacy JS commands)."""

from __future__ import annotations

import logging
import re
from typing import Any, Optional, Tuple

import discord

from helpers.validation_utils import validate_map_code
from resources.category_list import CATEGORY_LIST
from resources.get_tag import CATEGORY_TO_GROUP, get_tag_ids
from service.map_service import draw_map_url, fetch_map

logger = logging.getLogger(__name__)


_TITLE_WITH_INFO_RE = re.compile(r"^\[([P\d].*?)\]\s*\[(.*?)\]\s*(@\d+.*?)(?:\s+by\s+(.+))?$", re.I)
_TITLE_SIMPLE_RE = re.compile(r"^\[([P\d].*?)\]\s*(@\d+.*?)(?:\s+by\s+(.+))?$", re.I)


def _find_category_by_code(code: str) -> Optional[dict[str, Any]]:
    return next((c for c in CATEGORY_LIST if c.get("name") == code), None)


def parse_discussion_title(title: str) -> Tuple[Optional[str], str, Optional[str], str]:
    """
    Parses a discussion embed title.

    Returns:
    - category_token (string found inside the first [] - usually P-code)
    - discussion_info (string found inside the second [] or 'OTHER')
    - map_code (e.g. @12345)
    - map_author (defaults to 'Unknown Author')
    """
    if not title:
        return None, "OTHER", None, "Unknown Author"

    match = _TITLE_WITH_INFO_RE.match(title)
    if match:
        category_token = (match.group(1) or "").strip()
        disc_info = (match.group(2) or "").strip() or "OTHER"
        map_code = (match.group(3) or "").strip()
        map_author = (match.group(4) or "").strip() or "Unknown Author"
        return category_token, disc_info, map_code, map_author

    match = _TITLE_SIMPLE_RE.match(title)
    if match:
        category_token = (match.group(1) or "").strip()
        map_code = (match.group(2) or "").strip()
        map_author = (match.group(3) or "").strip() or "Unknown Author"
        return category_token, "OTHER", map_code, map_author

    # Fallback for weird titles.
    map_match = re.search(r"@\d{4,}", title)
    return None, "OTHER", map_match.group(0) if map_match else None, "Unknown Author"


def resolve_category_from_title_token(token: str) -> Optional[dict[str, Any]]:
    """
    Attempts to resolve category object from a token found in the title.
    Token might be a P-code or a description string.
    """
    if not token:
        return None
    token = token.strip()
    for cat in CATEGORY_LIST:
        if token == cat.get("name") or token == cat.get("description") or (cat.get("name") and cat.get("name") in token):
            return cat
    return None


async def fetch_discussion_main_message(thread: discord.Thread) -> Optional[discord.Message]:
    """Finds the main bot message in a thread (the oldest message with an embed)."""
    async for msg in thread.history(limit=30, oldest_first=True):
        if msg.embeds:
            return msg
    return None


def _trim_thread_name(name: str) -> str:
    return (name or "")[:100]


async def update_discussion_category(
    interaction: discord.Interaction,
    *,
    new_category_code: str,
) -> None:
    """
    Updates the discussion category to another of the same group/type.
    Mirrors legacy `update_discussion_category.js`.
    """
    thread = interaction.channel
    if not isinstance(thread, discord.Thread):
        await interaction.followup.send(content="This action can only be used within a discussion thread.", ephemeral=True)
        return

    new_category_code = (new_category_code or "").strip().upper()
    new_cat = _find_category_by_code(new_category_code)
    if not new_cat:
        await interaction.followup.send(content=f"Invalid new category code: {new_category_code}.", ephemeral=True)
        return

    main_message = await fetch_discussion_main_message(thread)
    if not main_message or not main_message.embeds:
        await interaction.followup.send(content="Could not retrieve the main discussion message or its embed.", ephemeral=True)
        return

    embed = main_message.embeds[0]
    title = embed.title or ""

    category_token, disc_info, map_code, map_author = parse_discussion_title(title)
    current_cat = resolve_category_from_title_token(category_token or "")
    if not current_cat:
        await interaction.followup.send(content=f"Could not identify the current category from: {category_token}", ephemeral=True)
        return

    current_code = current_cat.get("name")
    if current_code == new_category_code:
        await interaction.followup.send(
            content=f"The discussion is already in category {new_cat.get('description', new_category_code)}.",
            ephemeral=True,
        )
        return

    current_group = CATEGORY_TO_GROUP.get(current_code or "")
    new_group = CATEGORY_TO_GROUP.get(new_category_code)
    if not current_group or not new_group:
        await interaction.followup.send(content="Category type mapping is missing. Update failed.", ephemeral=True)
        return
    if current_group != new_group:
        await interaction.followup.send(
            content=f"Category type mismatch. Cannot move from '{current_group}' to '{new_group}'.",
            ephemeral=True,
        )
        return

    # Update embed title + thumbnail + color
    new_title = f'[{new_cat["name"]}] [{disc_info}] {map_code} by {map_author}'
    new_embed = embed.copy()
    new_embed.title = new_title
    if new_cat.get("picture"):
        new_embed.set_thumbnail(url=new_cat["picture"])
    if new_cat.get("color"):
        new_embed.color = int(str(new_cat["color"]).replace("#", "0x"), 16)

    await main_message.edit(embeds=[new_embed])

    # Update thread name (keep CLOSED prefix if present)
    prefix = "[CLOSED] " if thread.archived else ""
    await thread.edit(name=_trim_thread_name(prefix + new_title))

    # Update tags (open/closed)
    disc_type_for_tags = disc_info.upper() if disc_info.upper() in {"PERM", "EDIT", "DEPERM"} else disc_info
    status_for_tags = "closed" if thread.archived else "open"
    tag_ids = get_tag_ids(new_cat, disc_type_for_tags, status_for_tags) or []
    parent = thread.parent
    if isinstance(parent, discord.ForumChannel) and tag_ids:
        tag_id_set = set(tag_ids)
        applied_tags = [t for t in parent.available_tags if str(t.id) in tag_id_set]
        await thread.edit(applied_tags=applied_tags)

    await interaction.followup.send(
        content=f"Discussion category updated successfully to {new_cat.get('description', new_category_code)}.",
        ephemeral=False,
    )


async def refresh_discussion_information(interaction: discord.Interaction) -> None:
    """
    Refreshes map info in the discussion thread (image, author, title).
    Mirrors legacy `update_discussion_information.js`.
    """
    thread = interaction.channel
    if not isinstance(thread, discord.Thread):
        await interaction.followup.send(content="This action can only be used within a discussion thread.", ephemeral=True)
        return

    main_message = await fetch_discussion_main_message(thread)
    if not main_message or not main_message.embeds:
        await interaction.followup.send(content="Could not retrieve the main discussion message or its embed.", ephemeral=True)
        return

    embed = main_message.embeds[0]
    title = embed.title or ""
    _, disc_info, map_code, _old_author = parse_discussion_title(title)
    if not map_code:
        await interaction.followup.send(content=f"Could not extract map code from title: {title}", ephemeral=True)
        return

    # Fetch fresh data and image
    map_data = await fetch_map(map_code)
    if not map_data:
        await interaction.followup.send(content=f"Map '{map_code}' could not be fetched.", ephemeral=True)
        return

    image_url = await draw_map_url({"code": map_code, "xml": map_data.xml, "raw": False})
    if not image_url:
        await interaction.followup.send(content=f"Could not generate a new image for map '{map_code}'.", ephemeral=True)
        return

    # Keep category token from title; fallback to embed title parsing token.
    category_token, _, _, _ = parse_discussion_title(title)
    category_token = category_token or "Unknown"
    author = map_data.maker or "Unknown Author"
    new_title = f"[{category_token}] [{disc_info}] {map_code} by {author}"

    new_embed = embed.copy()
    new_embed.title = new_title
    new_embed.set_image(url=image_url)

    await main_message.edit(embeds=[new_embed])
    await thread.edit(name=_trim_thread_name(new_title))

    await interaction.followup.send(
        content=f"The map information for {map_code} in this thread has been updated successfully.",
        ephemeral=False,
    )


async def update_discussion_map_code(
    interaction: discord.Interaction,
    *,
    new_map_code: str,
) -> None:
    """
    Updates the discussion @mapcode and refreshes its image.
    Mirrors legacy `update_discussion_mapcode.js`.
    """
    thread = interaction.channel
    if not isinstance(thread, discord.Thread):
        await interaction.followup.send(content="This action can only be used within a discussion thread.", ephemeral=True)
        return

    validation = validate_map_code(new_map_code, min_digits=4)
    if not validation.is_valid:
        await interaction.followup.send(
            content="Invalid @mapcode format. Please use a format like @12345 (at least 4 digits).",
            ephemeral=True,
        )
        return
    new_code = validation.formatted_code

    main_message = await fetch_discussion_main_message(thread)
    if not main_message or not main_message.embeds:
        await interaction.followup.send(content="Could not retrieve the main discussion message or its embed.", ephemeral=True)
        return

    embed = main_message.embeds[0]
    title = embed.title or ""
    category_token, disc_info, _old_map_code, old_map_author = parse_discussion_title(title)

    category_obj = resolve_category_from_title_token(category_token or "")
    if not category_obj:
        await interaction.followup.send(content="Could not identify the original category from the title.", ephemeral=True)
        return

    map_data = await fetch_map(new_code)
    if not map_data:
        await interaction.followup.send(content=f"New map '{new_code}' could not be fetched.", ephemeral=True)
        return

    image_url = await draw_map_url({"code": new_code, "xml": map_data.xml, "raw": False})
    if not image_url:
        await interaction.followup.send(content=f"Could not generate a new image for map '{new_code}'.", ephemeral=True)
        return

    # Behave like refresh_discussion_information after map code change:
    # refresh author/title/image based on the newly fetched map data.
    map_author = map_data.maker or old_map_author or "Unknown Author"
    new_title = f'[{category_obj["name"]}] [{disc_info}] {new_code} by {map_author}'
    new_embed = embed.copy()
    new_embed.title = new_title
    new_embed.set_image(url=image_url)
    if category_obj.get("color"):
        new_embed.color = int(str(category_obj["color"]).replace("#", "0x"), 16)
    if category_obj.get("picture"):
        new_embed.set_thumbnail(url=category_obj["picture"])

    await main_message.edit(embeds=[new_embed])

    prefix = "[CLOSED] " if thread.archived else ""
    await thread.edit(name=_trim_thread_name(prefix + new_title))

    await interaction.followup.send(
        content=f"Discussion information updated successfully for {new_code}.",
        ephemeral=False,
    )

