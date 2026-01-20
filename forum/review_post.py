"""Review post updater (ported from legacy forum/review-post.js)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import unquote

import discord

from forum.forum_review import get_info
from forum.review_embed import create_review_embed, parse_datetime_br
from forum.review_messages import find_category_messages, find_verification_message
from resources.category_list import CATEGORY_LIST
from resources.channels import CHANNELS

logger = logging.getLogger(__name__)


DEFAULT_CATEGORY_CODES = ["P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10", "P11", "P17", "P18", "P24"]

_EDIT_THROTTLE_SECONDS = 0.7

def _find_category(code: str) -> dict:
    return next((c for c in CATEGORY_LIST if c.get("name") == code), {"name": code, "description": code, "emoji": "🗺️"})


async def update_reviews(client: discord.Client, *, category_codes: list[str] | None = None) -> None:
    """Updates per-category review embeds and the 'Latest Reviews' summary message."""
    codes = category_codes or DEFAULT_CATEGORY_CODES
    channel_id = CHANNELS.get("review_info")
    if not channel_id:
        logger.error("review_info channel is not configured in resources/channels.py")
        return

    review_channel = await client.fetch_channel(int(channel_id))
    if not isinstance(review_channel, discord.abc.Messageable):
        logger.error("review_info channel is not messageable")
        return

    existing = await find_category_messages(review_channel, codes)
    category_info: list[dict] = []

    for code in codes:
        cat = _find_category(code)
        try:
            info = await get_info(code)
            if not info:
                continue
            embed = create_review_embed(info)

            if code in existing:
                msg = existing[code]
                try:
                    # Avoid PATCHing if nothing changed (reduces rate limits).
                    current = msg.embeds[0].to_dict() if msg.embeds else None
                    if current != embed.to_dict():
                        await msg.edit(embed=embed, view=None)
                except Exception:
                    logger.exception("Failed to edit review message for %s, sending a new one", code)
                    msg = await review_channel.send(embed=embed)
            else:
                msg = await review_channel.send(embed=embed)

            # Gentle throttling to avoid burst edits on startup.
            await asyncio.sleep(_EDIT_THROTTLE_SECONDS)

            parsed = parse_datetime_br(info["dateTime"])
            unix_ts = int(parsed.timestamp())
            category_info.append(
                {
                    "name": cat.get("description", code),
                    "emoji": cat.get("emoji", "🗺️"),
                    "url": msg.jump_url,
                    "timestamp": unix_ts,
                    "author": info.get("author", ""),
                }
            )
        except Exception:
            logger.exception("Error processing review category %s", code)

    # Build verification embed
    now_ts = int(datetime.now(timezone.utc).timestamp())
    verification = discord.Embed(
        color=int("0x00ff00", 16),
        title="📝 Latest Reviews",
        description=f"Last Update: <t:{now_ts}:R>",
    )

    # 3 columns
    columns: list[list[dict]] = [[], [], []]
    items_per_column = (len(category_info) + 2) // 3 if category_info else 1
    for idx, info in enumerate(category_info):
        col_idx = min(2, idx // items_per_column)
        columns[col_idx].append(info)

    for col in columns:
        value = "\n\n".join(
            f'{i["emoji"]} [{i["name"]}]({i["url"]})\n└ by {unquote(str(i["author"])).title()} - <t:{i["timestamp"]}:R>'
            for i in col
        ) or "\u200b"
        verification.add_field(name="\u200b", value=value, inline=True)

    existing_verification = await find_verification_message(review_channel, client.user.id)  # type: ignore[union-attr]
    if existing_verification:
        try:
            current = existing_verification.embeds[0].to_dict() if existing_verification.embeds else None
            if current != verification.to_dict():
                await existing_verification.edit(embed=verification, view=None)
        except Exception:
            logger.exception("Failed to edit verification message, sending a new one")
            await review_channel.send(embed=verification)
    else:
        await review_channel.send(embed=verification)

    await asyncio.sleep(_EDIT_THROTTLE_SECONDS)

