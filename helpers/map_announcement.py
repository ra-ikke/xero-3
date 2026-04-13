"""Manual public announcement helpers for map decisions without discussion threads."""

from __future__ import annotations

import json
import logging
from io import BytesIO
from typing import Any, Optional

import aiohttp
import discord

from helpers.validation_utils import validate_map_code
from resources.category_list import CATEGORY_LIST
from resources.channels import CHANNELS
from resources.status_list import STATUSES_BY_NAME
from resources.get_tag import CATEGORY_TO_GROUP
from service.map_service import draw_map_url, fetch_map

logger = logging.getLogger(__name__)


def _find_category(code: str) -> Optional[dict[str, Any]]:
    return next((c for c in CATEGORY_LIST if c.get("name") == code), None)


def _category_to_group(code: str) -> Optional[str]:
    if code == "P13":
        return "bootcamp"
    return CATEGORY_TO_GROUP.get(code)


async def _download_url_as_file(url: str, filename: str) -> Optional[discord.File]:
    if not url or not isinstance(url, str) or not url.startswith("http"):
        return None
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning("Image download failed (%s): %s", resp.status, url)
                    return None
                data = await resp.read()
    except Exception:
        logger.exception("Failed to download image: %s", url)
        return None

    buffer = BytesIO(data)
    buffer.seek(0)
    return discord.File(buffer, filename=filename)


async def _resolve_map_announcement_data(map_code: str) -> tuple[str, str, str | None]:
    validation = validate_map_code(map_code, min_digits=4)
    normalized_code = validation.formatted_code
    if not validation.is_valid:
        raise ValueError("Please provide a valid map code (e.g., @12345).")

    map_data = await fetch_map(normalized_code)
    if not map_data:
        raise ValueError(f"Could not fetch map data for {normalized_code}.")

    image_url = await draw_map_url({"code": normalized_code, "xml": map_data.xml, "raw": False})
    return normalized_code, (map_data.maker or "Unknown Author"), image_url


async def _send_public_message(
    client: discord.Client,
    *,
    channel_id: str | None,
    content: str,
    map_code: str,
    image_url: str | None,
) -> Optional[discord.Message]:
    if not channel_id or not str(channel_id).isdigit():
        return None

    channel = await client.fetch_channel(int(channel_id))
    if not isinstance(channel, discord.abc.Messageable):
        raise ValueError("Configured public channel is not messageable.")

    image_file = await _download_url_as_file(image_url or "", f"{map_code}.png")
    files = [image_file] if image_file else []
    return await channel.send(content=content, files=files)


async def _send_changelog(client: discord.Client, payload: dict[str, Any]) -> None:
    changelog_id = CHANNELS.get("mc_changelog")
    if not changelog_id or not str(changelog_id).isdigit():
        return
    channel = await client.fetch_channel(int(changelog_id))
    if isinstance(channel, discord.abc.Messageable):
        await channel.send(content=json.dumps(payload))


async def announce_map_status(
    client: discord.Client,
    *,
    map_code: str,
    category_code: str,
    decision: str,
) -> dict[str, Any]:
    category = _find_category(category_code)
    if not category:
        raise ValueError("Invalid category selected.")

    final_status = str(decision or "").strip().upper()
    status_obj = STATUSES_BY_NAME.get(final_status)
    if not status_obj or final_status in {"MOVE", "IN DISCUSSION"}:
        raise ValueError("Invalid decision selected for announce_map.")

    normalized_code, map_author, image_url = await _resolve_map_announcement_data(map_code)

    group = _category_to_group(category_code)
    notification_channel_id = CHANNELS.get(group) if group else None
    if not notification_channel_id:
        raise ValueError("Could not determine the public channel for this category.")

    final_status_display = f'**{status_obj["description"]}**'
    content = (
        f'{category["emoji"]} (**{category["name"]}**) — '
        f"{map_author} - {normalized_code} - {final_status_display}"
    )
    posted = await _send_public_message(
        client,
        channel_id=notification_channel_id,
        content=content,
        map_code=normalized_code,
        image_url=image_url,
    )

    await _send_changelog(
        client,
        {
            "code": normalized_code,
            "author": map_author,
            "disc_status": final_status,
            "notify": True,
            "category": category["name"],
        },
    )

    return {
        "code": normalized_code,
        "author": map_author,
        "status": final_status,
        "channel_id": int(notification_channel_id),
        "jump_url": getattr(posted, "jump_url", None),
    }


async def announce_map_move(
    client: discord.Client,
    *,
    map_code: str,
    source_category_code: str,
    target_category_code: str,
) -> dict[str, Any]:
    source_category = _find_category(source_category_code)
    if not source_category:
        raise ValueError("Invalid source category selected.")

    target_category = _find_category(target_category_code)
    if not target_category:
        raise ValueError("Invalid target category selected.")

    normalized_code, map_author, image_url = await _resolve_map_announcement_data(map_code)

    target_group = _category_to_group(target_category_code)
    notification_channel_id = CHANNELS.get(target_group) if target_group else None
    if not notification_channel_id:
        raise ValueError("Could not determine the public channel for the target category.")

    content = (
        f'{source_category["emoji"]} ({source_category["name"]}) → '
        f'{target_category["emoji"]} ({target_category["name"]}) — '
        f"{map_author} - {normalized_code} - **Moved to another category**"
    )
    posted = await _send_public_message(
        client,
        channel_id=notification_channel_id,
        content=content,
        map_code=normalized_code,
        image_url=image_url,
    )

    await _send_changelog(
        client,
        {
            "code": normalized_code,
            "author": map_author,
            "disc_status": "MOVE",
            "notify": True,
            "original_category": source_category["name"],
            "target_category": target_category["name"],
        },
    )

    return {
        "code": normalized_code,
        "author": map_author,
        "status": "MOVE",
        "channel_id": int(notification_channel_id),
        "jump_url": getattr(posted, "jump_url", None),
    }
