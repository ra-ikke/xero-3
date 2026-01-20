"""Review embed builder (ported from legacy forum/review-embed.js)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import unquote

import discord


def parse_datetime_br(date_time_str: str) -> datetime:
    """Parses a BR datetime string in the format: DD/MM/YYYY, HH:MM:SS"""
    date_part, time_part = date_time_str.split(", ")
    day, month, year = [int(x) for x in date_part.split("/")]
    hours, minutes, seconds = [int(x) for x in time_part.split(":")]
    return datetime(year, month, day, hours, minutes, seconds)


def create_review_embed(review: dict[str, Any]) -> discord.Embed:
    """
    Creates a review embed. Expected review payload matches forum/forum_review.get_info().
    """
    author_raw = (review.get("author") or "unknown").strip()
    # Legacy JS used decodeURIComponent, so we replicate it here (%23 -> #, etc.).
    author_decoded = unquote(author_raw)
    author_safe = discord.utils.escape_markdown(author_decoded)
    author_display = author_safe[:1].upper() + author_safe[1:]

    review_parts: list[dict[str, Any]] = review.get("review") or []
    fields: list[dict[str, Any]] = []

    for part in review_parts:
        title = part.get("title")
        if title in ("Left as is", "Ignored"):
            continue

        content_lines = []
        for item in part.get("content") or []:
            content_lines.append(f'{item.get("code")} - {item.get("author")}')
        value = "\n".join(content_lines) if content_lines else "None"
        fields.append(
            {
                "name": f'{title} - {len(part.get("content") or [])} map(s)',
                "value": value,
                "inline": False,
            }
        )

    if review_parts and (review_parts[0].get("category") != "P3"):
        fields.append({"name": "Submissions", "value": f'{review.get("quantity", 0)} map(s)', "inline": True})

    fields.append({"name": "Page", "value": str(review.get("page", "N/A")), "inline": True})
    fields.append({"name": "Message", "value": str(review.get("message", "N/A")), "inline": True})

    dt = parse_datetime_br(review.get("dateTime") or "01/01/1970, 00:00:00")
    unix_ts = int(dt.timestamp())

    embed = discord.Embed(
        color=int(str(review.get("color", "#009D9D")).replace("#", "0x"), 16),
        title=f'{review.get("categoryDescription", review.get("category"))} 🔗',
        url=str(review.get("url", "")),
        description=f"<t:{unix_ts}:R>",
    )
    embed.set_author(
        name=f'by {author_display} ✉️',
        icon_url=str(review.get("avatar", "")),
        url=f'https://atelier801.com/new-dialog?ad={author_raw}',
    )
    embed.set_thumbnail(url=str(review.get("picture", "")))

    for f in fields:
        embed.add_field(**f)
    return embed

