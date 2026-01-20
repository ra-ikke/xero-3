"""Auth token helpers stored in MC_AUTH channel."""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any, Optional

import discord

from resources.channels import MC_AUTH

TOKEN_PREFIX = "AUTH_TOKEN_V1:"


def generate_auth_token() -> str:
    # URL-safe and long enough for API usage.
    return secrets.token_urlsafe(32)


def build_auth_record(
    *,
    token: str,
    user: discord.abc.User,
    guild: Optional[discord.Guild],
) -> dict[str, Any]:
    return {
        "token": token,
        "user_id": int(user.id),
        "user_tag": str(user),
        "guild_id": int(guild.id) if guild else None,
        "created_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    }


def serialize_auth_record(record: dict[str, Any]) -> str:
    return TOKEN_PREFIX + json.dumps(record, ensure_ascii=True, separators=(",", ":"))


def parse_auth_record(raw: str) -> Optional[dict[str, Any]]:
    if not raw or not raw.startswith(TOKEN_PREFIX):
        return None
    payload = raw[len(TOKEN_PREFIX) :].strip()
    try:
        data = json.loads(payload)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


async def get_auth_channel(bot: discord.Client) -> Optional[discord.abc.Messageable]:
    channel, _ = await resolve_auth_channel(bot)
    return channel


async def resolve_auth_channel(
    bot: discord.Client,
) -> tuple[Optional[discord.abc.Messageable], Optional[str]]:
    if not MC_AUTH:
        return None, "MC_AUTH is empty."
    if not str(MC_AUTH).isdigit():
        return None, f"MC_AUTH is not a valid channel id: {MC_AUTH!r}"
    try:
        channel = await bot.fetch_channel(int(MC_AUTH))
    except Exception as exc:
        return None, f"Failed to fetch channel {MC_AUTH}: {exc}"
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return None, f"MC_AUTH is not a TextChannel or Thread (got {type(channel).__name__})."
    return channel, None


async def find_auth_record_by_token(
    *,
    bot: discord.Client,
    token: str,
    history_limit: int = 2000,
) -> Optional[dict[str, Any]]:
    channel = await get_auth_channel(bot)
    if not channel:
        return None
    async for msg in channel.history(limit=history_limit, oldest_first=False):
        rec = parse_auth_record(msg.content or "")
        if not rec:
            continue
        if str(rec.get("token")) == str(token):
            return rec
    return None
