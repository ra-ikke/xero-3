"""Session export helpers (TXT/JSON share the same scan logic).

This module centralizes how we read the current session thread and derive:
- unique map codes per submitter (first-seen order)
- which entries are ignored (currently: user exceeded per-category limit)
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

import discord

from helpers.validation_utils import validate_map_code
from resources.category_list import CATEGORY_LIST


def normalize_category_code(value: Optional[str]) -> str:
    s = (value or "").strip()
    if not s:
        return ""
    s = s.upper()
    # Accept "4" -> "P4"
    if s.isdigit():
        s = f"P{s}"
    if not s.startswith("P") and s[0].isdigit():
        s = f"P{s}"
    return s


def _find_category(code: str) -> dict[str, Any]:
    return next((c for c in CATEGORY_LIST if str(c.get("name", "")).upper() == str(code).upper()), {})  # type: ignore[return-value]


def submission_limit_for_category(category_code: str, *, default: int = 3) -> int:
    cat = _find_category(category_code)
    raw = cat.get("submissionlimit", None)
    try:
        value = int(raw) if raw is not None else int(default)
        # Treat <= 0 as unlimited.
        if value <= 0:
            return 0
        return value
    except Exception:
        return int(default)


_MAP_CODE_RE = re.compile(r"@\d+")
_MARKER_START_RE = re.compile(r"session\s*#\d+\s*started", re.IGNORECASE)
_MARKER_END_RE = re.compile(r"session\s*#\d+\s*finished", re.IGNORECASE)
_MARKER_START_NUM_RE = re.compile(r"session\s*#(\d+)\s*started", re.IGNORECASE)


def extract_map_codes(content: str) -> list[str]:
    # Keep the same semantics as the TXT export: accept any @<digits> and validate later.
    codes = set(_MAP_CODE_RE.findall(content or ""))
    return sorted(codes)


def server_alias(author: discord.abc.User) -> str:
    """Prefer guild nickname/display name over username#discriminator."""
    name = getattr(author, "display_name", None) or getattr(author, "global_name", None) or getattr(author, "name", None)
    return str(name or author)


def _has_marker_line(content: str, pattern: re.Pattern[str]) -> bool:
    for line in (content or "").splitlines():
        candidate = line.strip()
        if candidate.lower().startswith("-#"):
            candidate = candidate[2:].lstrip()
        # Remove leading italics marker if present.
        if candidate.startswith("*"):
            candidate = candidate.lstrip("*").lstrip()
        candidate = re.sub(r"<:[^>]+>", "", candidate).strip()
        if pattern.search(candidate):
            return True
    return False


def _extract_marker_session_no(content: str) -> Optional[int]:
    for line in (content or "").splitlines():
        candidate = line.strip()
        if candidate.lower().startswith("-#"):
            candidate = candidate[2:].lstrip()
        if candidate.startswith("*"):
            candidate = candidate.lstrip("*").lstrip()
        candidate = re.sub(r"<:[^>]+>", "", candidate).strip()
        match = _MARKER_START_NUM_RE.search(candidate)
        if match:
            try:
                return int(match.group(1))
            except Exception:
                return None
    return None


def is_start_marker(content: str) -> bool:
    return _has_marker_line(content, _MARKER_START_RE)


def is_end_marker(content: str) -> bool:
    return _has_marker_line(content, _MARKER_END_RE)


def _is_marker_message(
    message: discord.Message,
    *,
    pattern: re.Pattern[str],
    bot_user_id: Optional[int],
) -> bool:
    if not bot_user_id:
        return False
    author_id = getattr(getattr(message, "author", None), "id", None)
    if author_id != bot_user_id:
        return False
    return _has_marker_line(message.content or "", pattern)


async def get_session_marker_state(
    *,
    thread: discord.Thread,
    history_limit: int = 5000,
    bot_user_id: Optional[int] = None,
) -> dict[str, Any]:
    """Returns marker state for the latest session in the thread."""
    found_start = False
    active = False
    last_start_message_id: int | None = None
    last_end_message_id: int | None = None
    last_session_no: int | None = None

    async for m in thread.history(limit=history_limit, oldest_first=True):
        if not m:
            continue
        if _is_marker_message(m, pattern=_MARKER_START_RE, bot_user_id=bot_user_id):
            found_start = True
            active = True
            last_start_message_id = int(m.id)
            last_end_message_id = None
            last_session_no = _extract_marker_session_no(m.content or "") or last_session_no
            continue
        if _is_marker_message(m, pattern=_MARKER_END_RE, bot_user_id=bot_user_id) and found_start:
            active = False
            last_end_message_id = int(m.id)

    return {
        "has_start": bool(found_start),
        "is_active": bool(active),
        "last_start_message_id": last_start_message_id,
        "last_end_message_id": last_end_message_id,
        "last_session_no": last_session_no,
    }


async def collect_session_maps(
    *,
    thread: discord.Thread,
    category_code: str,
    history_limit: int = 5000,
    bot_user_id: Optional[int] = None,
) -> dict[str, Any]:
    """Scans a session thread and returns a JSON-serializable structure.

    Result keys:
    - category: str
    - threadId: int
    - collectedAt: str (UTC ISO)
    - limitPerUser: int
    - maps: list[{submitter, mapCode, ignored, reason}]
    """
    limit_per_user = submission_limit_for_category(category_code)

    seen_user_code: set[tuple[int, str]] = set()
    first_submitter_by_code: dict[str, int] = {}
    first_message_id_by_code: dict[str, int] = {}
    user_codes: dict[int, list[str]] = {}
    user_tags: dict[int, str] = {}
    ordered_entries: list[tuple[int, str, bool, Optional[int]]] = []  # (user_id, map_code, is_duplicate, message_id)

    buffer_active: list[discord.Message] = []
    buffer_all: list[discord.Message] = []
    found_start = False
    collecting = False

    async for m in thread.history(limit=history_limit, oldest_first=True):
        if not m or not m.author:
            continue

        # Marker detection must run even for bot messages.
        if _is_marker_message(m, pattern=_MARKER_START_RE, bot_user_id=bot_user_id):
            found_start = True
            collecting = True
            buffer_active = []
            continue
        if _is_marker_message(m, pattern=_MARKER_END_RE, bot_user_id=bot_user_id) and collecting:
            collecting = False
            continue

        # Only collect user messages with map codes.
        if getattr(m.author, "bot", False):
            continue

        buffer_all.append(m)
        if collecting:
            buffer_active.append(m)

    selected = buffer_active if found_start else buffer_all
    for m in selected:
        codes = extract_map_codes(m.content or "")
        if not codes:
            continue
        for code in codes:
            v = validate_map_code(code, min_digits=1)
            if not v.is_valid:
                continue
            user_id = int(m.author.id)
            key = (user_id, v.formatted_code)
            if key in seen_user_code:
                continue
            seen_user_code.add(key)
            is_duplicate = v.formatted_code in first_submitter_by_code
            if not is_duplicate:
                first_submitter_by_code[v.formatted_code] = user_id
                first_message_id_by_code[v.formatted_code] = int(m.id)
            user_tag = server_alias(m.author)
            user_tags[user_id] = user_tag
            user_codes.setdefault(user_id, []).append(v.formatted_code)
            ordered_entries.append((user_id, v.formatted_code, is_duplicate, int(m.id)))

    if limit_per_user <= 0:
        users_over_limit: set[int] = set()
    else:
        users_over_limit = {uid for uid, codes in user_codes.items() if len(codes) > limit_per_user}

    maps: list[dict[str, Any]] = []
    for user_id, map_code, is_duplicate, message_id in ordered_entries:
        submitter = user_tags.get(user_id, "Unknown")
        if is_duplicate:
            ignored = True
            duplicate_msg_id = message_id
            guild_id = getattr(getattr(thread, "guild", None), "id", None)
            link = (
                f"https://discord.com/channels/{int(guild_id)}/{int(thread.id)}/{int(duplicate_msg_id)}"
                if guild_id and duplicate_msg_id
                else ""
            )
            reason = f"Duplicate submission ({link})" if link else "Duplicate submission"
        else:
            ignored = user_id in users_over_limit
            reason = f"Posted more than {limit_per_user} maps" if ignored else None
        maps.append(
            {
                "submitter": submitter,
                "mapCode": map_code,
                "ignored": bool(ignored),
                "reason": reason,
            }
        )

    collected_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    return {
        "category": str(category_code),
        "threadId": int(thread.id),
        "collectedAt": collected_at,
        "limitPerUser": int(limit_per_user),
        "maps": maps,
    }

