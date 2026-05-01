"""Private command: post-perm-change (legacy port).

Reads JSON changelog payloads from mc_changelog (notify=true) for the last month and posts
a formatted list of PERM/MOVE updates into perm_changes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import PRIVATE_SERVER_IDS
from helpers.interaction_utils import safe_reply
from resources.category_list import CATEGORY_LIST
from resources.channels import CHANNELS
from resources.emoji import EMOJI_LIST

logger = logging.getLogger(__name__)

_guild_objects = [discord.Object(id=server_id) for server_id in PRIVATE_SERVER_IDS] if PRIVATE_SERVER_IDS else []


def _find_category(code: str) -> Optional[dict[str, Any]]:
    return next((c for c in CATEGORY_LIST if c.get("name") == code), None)


def _month_range_utc(now: Optional[datetime] = None) -> tuple[datetime, datetime, str]:
    """Returns (start_of_last_month_utc, start_of_this_month_utc, last_month_name)."""
    now = now or datetime.now(timezone.utc)
    start_this = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if now.month == 1:
        start_last = datetime(now.year - 1, 12, 1, tzinfo=timezone.utc)
    else:
        start_last = datetime(now.year, now.month - 1, 1, tzinfo=timezone.utc)

    month_names = [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ]
    month_name = month_names[start_last.month - 1]
    return start_last, start_this, month_name


def _parse_yyyy_mm_dd(value: str) -> Optional[date]:
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def _range_header(*, start_day: date, end_day_inclusive: date) -> str:
    return f"{start_day.isoformat()} → {end_day_inclusive.isoformat()}"


def _parse_json_payload(payload: str) -> Optional[dict[str, Any]]:
    try:
        value = json.loads(payload or "")
        return value if isinstance(value, dict) else None
    except Exception:
        return None


@dataclass(frozen=True)
class _PermChangeItem:
    code: str
    author: str
    disc_status: str  # "PERM" | "MOVE"
    timestamp: float
    category: Optional[str] = None
    original_category: Optional[str] = None
    target_category: Optional[str] = None


def _cat_sort_key(item: _PermChangeItem) -> tuple[int, str]:
    cat = item.target_category if item.disc_status == "MOVE" else item.category
    if cat and cat.upper().startswith("P"):
        try:
            return (int(cat[1:]), item.code)
        except Exception:
            pass
    return (999, item.code)


def _format_post(items: list[_PermChangeItem]) -> str:
    lines: list[str] = []
    for it in items:
        if it.disc_status == "MOVE":
            cat_orig = _find_category(it.original_category or "")
            cat_dest = _find_category(it.target_category or "")
            emoji_orig = (cat_orig or {}).get("emoji") or (it.original_category or "")
            emoji_dest = (cat_dest or {}).get("emoji") or (it.target_category or "")
            lines.append(f"{emoji_orig} -> {emoji_dest} {it.code} by {it.author}")
        else:
            cat = _find_category(it.category or "")
            emoji = (cat or {}).get("emoji") or (it.category or "")
            lines.append(f"{emoji} {it.code} by {it.author}")
    return "\n".join(lines)


def _split_for_discord(content: str, *, max_len: int = 1900) -> list[str]:
    """Splits large content into chunks <= max_len, preserving line boundaries when possible."""
    lines = (content or "").splitlines()
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for line in lines:
        # Single line longer than limit (rare): hard-split so Discord never rejects the payload.
        if len(line) > max_len:
            if buf:
                chunks.append("\n".join(buf))
                buf = []
                size = 0
            start = 0
            while start < len(line):
                chunks.append(line[start : start + max_len])
                start += max_len
            continue

        add = len(line) + (1 if buf else 0)
        if buf and size + add > max_len:
            chunks.append("\n".join(buf))
            buf = [line]
            size = len(line)
        else:
            buf.append(line)
            size += add
    if buf:
        chunks.append("\n".join(buf))
    return chunks


async def _retrieve_perm_changes(
    channel: discord.abc.Messageable,
    *,
    start: datetime,
    end: datetime,
) -> list[_PermChangeItem]:
    latest_by_code: dict[str, _PermChangeItem] = {}

    # Iterate messages between [start, end).
    async for message in channel.history(limit=None, after=start, before=end, oldest_first=False):
        payload = _parse_json_payload(message.content)
        if not payload:
            continue

        notify = payload.get("notify")
        code = payload.get("code")
        author = payload.get("author")
        disc_status = payload.get("disc_status")

        if notify is not True:
            continue
        if not code or not author or not disc_status or not isinstance(disc_status, str):
            continue

        status = disc_status.strip().upper()
        ts = message.created_at.replace(tzinfo=timezone.utc).timestamp()

        if status == "PERM":
            category = payload.get("category")
            if not category:
                continue
            item = _PermChangeItem(code=str(code), author=str(author), category=str(category), disc_status="PERM", timestamp=ts)
        elif status == "MOVE":
            orig = payload.get("original_category")
            dest = payload.get("target_category")
            if not orig or not dest:
                continue
            item = _PermChangeItem(
                code=str(code),
                author=str(author),
                original_category=str(orig),
                target_category=str(dest),
                disc_status="MOVE",
                timestamp=ts,
            )
        else:
            continue

        prev = latest_by_code.get(item.code)
        if not prev or item.timestamp > prev.timestamp:
            latest_by_code[item.code] = item

    items = list(latest_by_code.values())
    items.sort(key=_cat_sort_key)
    return items


class PermChanges(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    if _guild_objects:
        @app_commands.command(
            name="post-perm-change",
            description="Retrieves the map list permed in the last month.",
        )
        @app_commands.guilds(*_guild_objects)
        @app_commands.describe(
            start_date="Optional. Start date (YYYY-MM-DD). Inclusive.",
            end_date="Optional. End date (YYYY-MM-DD). Inclusive.",
        )
        async def post_perm_change(
            self,
            interaction: discord.Interaction,
            start_date: Optional[str] = None,
            end_date: Optional[str] = None,
        ) -> None:
            await interaction.response.defer(ephemeral=True)

            channel = interaction.channel
            if not channel or getattr(channel, "name", None) != "general":
                await safe_reply(interaction, "Oops! This command can't be used in this channel.", ephemeral=True)
                return

            changelog_id = CHANNELS.get("mc_changelog")
            perm_changes_id = CHANNELS.get("perm_changes")
            if not changelog_id or not perm_changes_id:
                await safe_reply(interaction, "mc_changelog or perm_changes is not configured.", ephemeral=True)
                return

            try:
                changelog_channel = await interaction.client.fetch_channel(int(changelog_id))
            except Exception:
                changelog_channel = None
            if not isinstance(changelog_channel, discord.abc.Messageable):
                await safe_reply(interaction, "mc_changelog channel is not messageable.", ephemeral=True)
                return

            try:
                target_channel = await interaction.client.fetch_channel(int(perm_changes_id))
            except Exception:
                target_channel = None
            if not isinstance(target_channel, discord.abc.Messageable):
                await safe_reply(interaction, "perm_changes channel is not messageable.", ephemeral=True)
                return

            parchment = EMOJI_LIST.get("_parchment", "")
            footer = (
                "*For further information regarding other maps' status, check out the map chat channels or contact a MapCrew member!*"
            )

            # Date range selection:
            # - If no dates provided: last month [start_last, start_this)
            # - If only start provided: [start, now)
            # - If only end provided: reject (ambiguous)
            # - If both provided: [start, end+1day)
            start_dt: datetime
            end_dt: datetime
            header: str

            if not start_date and not end_date:
                start_dt, end_dt, month_name = _month_range_utc()
                header = f"**{parchment} {month_name} Maps Perm Updates:**"
            else:
                if end_date and not start_date:
                    await safe_reply(interaction, "Please provide start_date when using end_date.", ephemeral=True)
                    return
                start_day = _parse_yyyy_mm_dd(start_date or "")
                if not start_day:
                    await safe_reply(interaction, "Invalid start_date. Use YYYY-MM-DD.", ephemeral=True)
                    return
                if end_date:
                    end_day = _parse_yyyy_mm_dd(end_date)
                    if not end_day:
                        await safe_reply(interaction, "Invalid end_date. Use YYYY-MM-DD.", ephemeral=True)
                        return
                else:
                    end_day = datetime.now(timezone.utc).date()

                if end_day < start_day:
                    await safe_reply(interaction, "end_date must be >= start_date.", ephemeral=True)
                    return

                start_dt = datetime(start_day.year, start_day.month, start_day.day, tzinfo=timezone.utc)
                # Inclusive end date: add 1 day and use before=end_dt_exclusive
                end_dt = datetime(end_day.year, end_day.month, end_day.day, tzinfo=timezone.utc) + timedelta(days=1)
                range_text = _range_header(start_day=start_day, end_day_inclusive=end_day)
                header = f"**{parchment} Perm Updates ({range_text}):**"

            items = await _retrieve_perm_changes(changelog_channel, start=start_dt, end=end_dt)
            body = _format_post(items)
            content = f"{header}\n\n{body}\n\n{footer}".strip()

            # Discord message limit is 2000 chars; split into several messages instead of a .txt attachment.
            chunks = _split_for_discord(content, max_len=1900)
            for chunk in chunks:
                await target_channel.send(content=chunk)

            n = len(chunks)
            msg_word = "mensagem" if n == 1 else "mensagens"
            await safe_reply(
                interaction,
                f"Posted! ({n} {msg_word} no canal perm_changes.)",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    """Registers the cog."""
    await bot.add_cog(PermChanges(bot))

