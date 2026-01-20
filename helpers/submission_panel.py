"""Submission panel helpers (no UI, no circular imports).

This module is intentionally UI-agnostic so both:
- ui.map_submission_view (View/buttons)
- helpers.submission_facade (business logic)
can import it safely without circular dependencies.
"""

from __future__ import annotations

from typing import Optional

import discord

from resources.category_list import CATEGORY_LIST


FIELD_CURRENT_SESSION = "Current session"


def _find_category(code: str) -> Optional[dict]:
    return next((c for c in CATEGORY_LIST if c.get("name") == code), None)


def build_panel_footer(
    category_code: str,
    *,
    last_session_no: int = 0,
    current_thread_id: Optional[int] = None,
    current_session_no: Optional[int] = None,
    last_finished_ts: Optional[int] = None,
) -> str:
    # Stable prefix used by /setup_submissions to find panel messages.
    # Extra metadata is stored as pipe-separated key-value pairs.
    parts = [f"map_submission_panel:{category_code}", f"last={int(last_session_no)}"]
    if current_thread_id and current_session_no:
        parts.append(f"current={int(current_thread_id)}")
        parts.append(f"current_no={int(current_session_no)}")
    if last_finished_ts:
        parts.append(f"last_end={int(last_finished_ts)}")
    return "|".join(parts)


def parse_panel_footer(text: str) -> dict[str, Optional[int] | str]:
    # Returns: category_code (str), last (int), current (int|None), current_no (int|None)
    out: dict[str, Optional[int] | str] = {
        "category_code": "",
        "last": 0,
        "current": None,
        "current_no": None,
        "last_end": None,
    }
    raw = (text or "").strip()
    if not raw.startswith("map_submission_panel:"):
        return out
    pieces = raw.split("|")
    out["category_code"] = pieces[0].split("map_submission_panel:", 1)[-1].strip()
    for p in pieces[1:]:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k in ("last", "current", "current_no", "last_end"):
            try:
                out[k] = int(v)
            except Exception:
                out[k] = None
    return out


def build_submission_panel_embed(
    category_code: str,
    *,
    last_session_no: int = 0,
    current_thread_id: Optional[int] = None,
    current_session_no: Optional[int] = None,
    is_locked: Optional[bool] = None,
    last_finished_ts: Optional[int] = None,
) -> discord.Embed:
    cat = _find_category(category_code) or {"name": category_code, "description": category_code, "color": "#2B2D31"}
    color = int(str(cat.get("color", "#2B2D31")).replace("#", "0x"), 16)
    try:
        submission_limit = int(cat.get("submissionlimit", 3))
    except Exception:
        submission_limit = 3

    embed = discord.Embed(
        title=f"{cat.get('emoji', '')} Submissions — {cat.get('description', category_code)}",
        description=(
            "Use the buttons below to manage the session.\n\n"
            "- **Start session**: creates or reuses the category thread and sets the active session.\n"
            "- **Update category**: refreshes the category thread embed and syncs panel info (limit/status).\n"
            "- **Download session**: downloads a JSON file with the session data.\n"
            "- **Submit review**: you have to upload the JSON file from the app to post results into the category thread and close the session.\n"
            "- **Lock/Unlock thread**: toggles the thread lock; lock asks for a reason and updates the status here.\n"
        ),
        color=color,
    )
    if submission_limit <= 0:
        limit_label = "No limit"
    else:
        limit_label = f"{submission_limit} map(s) per user"
    embed.add_field(name="Submission limit", value=limit_label, inline=True)

    if current_thread_id and current_session_no:
        embed.add_field(
            name=FIELD_CURRENT_SESSION,
            value=f"<#{int(current_thread_id)}> (Session #{int(current_session_no)})",
            inline=True,
        )
    else:
        embed.add_field(name=FIELD_CURRENT_SESSION, value="None", inline=True)

    if is_locked is True:
        lock_label = "Locked 🔒"
    elif is_locked is False:
        lock_label = "Unlocked 🔓"
    else:
        lock_label = "Unknown"
    embed.add_field(name="Thread status", value=lock_label, inline=True)

    if last_finished_ts:
        last_label = f"<t:{int(last_finished_ts)}:R>"
    else:
        last_label = "None"
    embed.add_field(name="Last session finished", value=last_label, inline=True)

    embed.set_footer(
        text=build_panel_footer(
            category_code,
            last_session_no=last_session_no,
            current_thread_id=current_thread_id,
            current_session_no=current_session_no,
            last_finished_ts=last_finished_ts,
        )
    )
    return embed

