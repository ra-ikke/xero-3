"""Thin facade for submission UI actions (threads / export / review)."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from io import BytesIO
from typing import Any, Optional

import discord

from helpers.interaction_utils import safe_reply
from helpers.validation_utils import get_display_name, has_public_role, has_mapcrew_role, validate_map_code
from resources.category_list import CATEGORY_LIST
from resources.channels import SUBMISSION_CHANNELS
from resources.emoji import EMOJI_LIST
from helpers.submission_panel import parse_panel_footer, build_submission_panel_embed
from helpers.session_export import collect_session_maps, get_session_marker_state

logger = logging.getLogger(__name__)

AUTO_CREATE_NEXT_SESSION = True


def _find_category(code: str) -> Optional[dict[str, Any]]:
    return next((c for c in CATEGORY_LIST if c.get("name") == code), None)


def _br_now_str() -> str:
    # review_embed.parse_datetime_br expects "DD/MM/YYYY, HH:MM:SS"
    return datetime.now().strftime("%d/%m/%Y, %H:%M:%S")


def _normalize_section(section: str) -> str:
    s = (section or "").strip()
    s_low = s.lower().rstrip(":")
    mapping = {
        "left as is": "Left as is",
        # Legacy name "Ped" is treated as "P1'ed" in this flow.
        "ped": "P1'ed",
        "p1'ed": "P1'ed",
        "p1ed": "P1'ed",
        "will be discussed": "Will be discussed",
        "ignored": "Ignored",
    }
    return mapping.get(s_low, s.strip())


def _empty_review_parts(category_code: str) -> list[dict[str, Any]]:
    # Always include all known parts to keep parsing deterministic.
    return [
        {"title": "Left as is", "category": category_code, "content": []},
        {"title": "P1'ed", "category": category_code, "content": []},
        {"title": "Will be discussed", "category": category_code, "content": []},
        {"title": "Ignored", "category": category_code, "content": []},
    ]


def _parts_index(parts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(p.get("title")): p for p in parts}


def parse_review_file_bytes(*, category_code: str, filename: str, data: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parses TXT into review parts for the session results embed.

    Returns (parts, meta) where meta may include:
    - cat: str | None (from CAT:Pxx)
    - pos: int | None (from POS:...)
    """
    parts = _empty_review_parts(category_code)
    by_title = _parts_index(parts)

    text = (data or b"").decode("utf-8-sig", errors="replace")
    meta: dict[str, Any] = {"cat": None, "pos": None}

    # "Saved session" format used by the external tooling.
    # Example:
    # ####SAVED SESSION####
    # GOOD:14013|User#0000|@123|++++++|2|Comment...
    # BAD:14020|User#0000|@123|++++++|0|Reason...
    if "####SAVED SESSION####" in text or any(
        line.strip().startswith(("GOOD:", "BAD:")) for line in text.splitlines() if line.strip()
    ):
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("####"):
                continue
            if line.startswith("CAT:"):
                meta["cat"] = (line.split("CAT:", 1)[-1] or "").strip() or None
                continue
            if line.startswith("POS:"):
                try:
                    meta["pos"] = int((line.split("POS:", 1)[-1] or "").strip())
                except Exception:
                    meta["pos"] = None
                continue

            if ":" not in line:
                continue
            kind, payload = line.split(":", 1)
            kind = kind.strip().upper()
            if kind not in ("GOOD", "BAD"):
                continue

            fields = payload.split("|")
            if len(fields) < 3:
                continue
            # Format (best-effort):
            # 0 pos, 1 author, 2 code, 3 extra, 4 decision, 5 comment...
            author = (fields[1] or "").strip() if len(fields) > 1 else "Unknown"
            code_raw = (fields[2] or "").strip() if len(fields) > 2 else ""
            decision_raw = (fields[4] or "").strip() if len(fields) > 4 else ""
            comment = "|".join(fields[5:]).strip() if len(fields) > 5 else ""

            v = validate_map_code(code_raw, min_digits=1)
            if not v.is_valid:
                continue

            if kind == "BAD":
                by_title["Ignored"]["content"].append(
                    {"code": v.formatted_code, "author": author or "Unknown", "comment": comment or ""}
                )
                continue

            # GOOD decisions:
            # 0 = Left as is, 1 = P1'ed, 2 = Will be discussed
            try:
                d = int(decision_raw) if decision_raw != "" else 0
            except Exception:
                d = 0
            if d == 1:
                title = "P1'ed"
            elif d == 2:
                title = "Will be discussed"
            else:
                title = "Left as is"

            by_title[title]["content"].append(
                {"code": v.formatted_code, "author": author or "Unknown", "comment": comment or ""}
            )
        return parts, meta

    # TXT format
    current_section: Optional[str] = None
    known = {"Left as is", "P1'ed", "Will be discussed", "Ignored"}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("CAT:"):
            meta["cat"] = (line.split("CAT:", 1)[-1] or "").strip() or None
            continue
        if line.startswith("POS:"):
            try:
                meta["pos"] = int((line.split("POS:", 1)[-1] or "").strip())
            except Exception:
                meta["pos"] = None
            continue
        # Section header
        candidate = _normalize_section(line)
        if candidate in known:
            current_section = candidate
            continue
        if candidate.lower().startswith("bad maps"):
            # Ignore export-only section if users upload our exported TXT.
            current_section = None
            continue
        if not current_section:
            continue
        # Line: "@123456 - Player#0000"
        if "-" in line:
            left, right = line.split("-", 1)
            code_raw = left.strip()
            author = right.strip()
        else:
            code_raw = line.strip()
            author = "Unknown"
        v = validate_map_code(code_raw, min_digits=1)
        if not v.is_valid:
            continue
        by_title[current_section]["content"].append({"code": v.formatted_code, "author": author or "Unknown", "comment": ""})
    return parts, meta


def _build_submission_results_embed(
    *,
    category_code: str,
    parts: list[dict[str, Any]],
    user: discord.abc.User,
) -> discord.Embed:
    """Builds a compact results embed for session threads (no extra Review-Info fields)."""
    cat = _find_category(category_code) or {}
    color = int(str(cat.get("color", "#2B2D31")).replace("#", "0x"), 16)

    embed = discord.Embed(
        title=f"{cat.get('description', category_code)} — Session review results",
        color=color,
    )
    member = user if isinstance(user, discord.Member) else None
    if member and not has_public_role(member):
        # Privacy: do not expose the Mapcrew's discord name and avatar.
        display_name = "Private Mapcrew"
        embed.set_author(name=f"Reviewed by {display_name}")
    else:
        display_name = get_display_name(member) if member else str(user)
        embed.set_author(name=f"Reviewed by {display_name}", icon_url=user.display_avatar.url)

    if cat.get("picture"):
        embed.set_thumbnail(url=str(cat.get("picture")))

    def _add_section(title: str) -> None:
        part = next((p for p in parts if p.get("title") == title), None)
        items = (part or {}).get("content") or []
        lines = []
        for it in items:
            code = it.get("code")
            author = it.get("author")
            comment = (it.get("comment") or "").strip()
            if comment:
                lines.append(f"{code} - {author} — {comment}")
            else:
                lines.append(f"{code} - {author}")
        value = "\n".join(lines) if lines else "None"
        embed.add_field(name=f"{title} — {len(items)} map(s)", value=value, inline=False)

    # Match the existing semantics but keep it compact.
    _add_section("Left as is")
    _add_section("P1'ed")
    _add_section("Will be discussed")
    _add_section("Ignored")
    return embed


def _build_submission_results_embed_for_reviewer_name(
    *,
    category_code: str,
    parts: list[dict[str, Any]],
    reviewer_name: str,
) -> discord.Embed:
    """Builds a results embed when we don't have a Discord User (e.g. API)."""
    cat = _find_category(category_code) or {}
    color = int(str(cat.get("color", "#2B2D31")).replace("#", "0x"), 16)

    embed = discord.Embed(
        title=f"{cat.get('description', category_code)} — Session review results",
        color=color,
    )
    embed.set_author(name=f"Reviewed by {reviewer_name}")

    if cat.get("picture"):
        embed.set_thumbnail(url=str(cat.get("picture")))

    def _add_section(title: str) -> None:
        part = next((p for p in parts if p.get("title") == title), None)
        items = (part or {}).get("content") or []
        lines = []
        for it in items:
            code = it.get("code")
            author = it.get("author")
            comment = (it.get("comment") or "").strip()
            if comment:
                lines.append(f"{code} - {author} — {comment}")
            else:
                lines.append(f"{code} - {author}")
        value = "\n".join(lines) if lines else "None"
        embed.add_field(name=f"{title} — {len(items)} map(s)", value=value, inline=False)

    _add_section("Left as is")
    _add_section("P1'ed")
    _add_section("Will be discussed")
    _add_section("Ignored")
    return embed


def build_review_parts_from_export_payload_v1(*, category_code: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Converts Maps Reviewer export JSON (schemaVersion=1) items into 'parts'."""
    parts = _empty_review_parts(category_code)
    by_title = _parts_index(parts)

    def _title_from_decision(decision: Optional[str], *, imported_ignored: bool) -> str:
        d = (decision or "").strip().lower()
        if imported_ignored or d == "ignored":
            return "Ignored"
        if d in ("left_as_is", "left as is"):
            return "Left as is"
        if d in ("p1ed", "p1'ed", "p1ed'"):
            return "P1'ed"
        if d in ("will_be_discussed", "will be discussed"):
            return "Will be discussed"
        return "Ignored" if imported_ignored else "Left as is"

    for it in items or []:
        raw_code = str(it.get("mapcode") or "").strip()
        v = validate_map_code(raw_code, min_digits=1)
        if not v.is_valid:
            continue

        # Prefer the *map author*; fallback to submitter if author is missing.
        author = (str(it.get("author") or "").strip() or str(it.get("submitter") or "").strip() or "Unknown")
        imported_ignored = bool(it.get("importedIgnored"))
        decision = it.get("decision")
        title = _title_from_decision(str(decision) if decision is not None else None, imported_ignored=imported_ignored)

        review_text = str(it.get("review") or "").strip()
        imported_reason = str(it.get("importedReason") or "").strip()
        comment = review_text
        if title == "Ignored" and not comment:
            comment = imported_reason

        by_title[title]["content"].append({"code": v.formatted_code, "author": author, "comment": comment})

    return parts


def build_review_messages_from_parts(
    *,
    category_code: str,
    session_no: int,
    parts: list[dict[str, Any]],
    reviewer_name: str,
    max_chars: int = 1900,
) -> list[str]:
    """Builds the text messages for a session review result."""
    # Prefer text messages over embeds for API reviews, to avoid embed size limits (6000 chars).
    def _safe_one_line(text: str, *, max_len: int = 800) -> str:
        s = (text or "").replace("\r", " ").replace("\n", " ").strip()
        if len(s) <= max_len:
            return s
        return s[: max_len - 1].rstrip() + "…"

    def _truncate(text: str, *, max_len: int) -> str:
        s = (text or "").replace("\r", " ").replace("\n", "\n").strip()
        if len(s) <= max_len:
            return s
        if max_len <= 1:
            return "…"
        return s[: max_len - 1].rstrip() + "…"

    def _split_comment(comment: str, *, max_total: int = 2000, chunk: int = 850) -> list[str]:
        """Splits a comment into multiple lines, preserving up to max_total chars."""
        c = _truncate((comment or "").strip(), max_len=max_total)
        if not c:
            return []
        # Keep user newlines, but also wrap long lines.
        out: list[str] = []
        for raw in c.splitlines() or []:
            line = raw.strip()
            if not line:
                continue
            while len(line) > chunk:
                out.append(line[:chunk].rstrip())
                line = line[chunk:].lstrip()
            if line:
                out.append(line)
        # If there were no newlines, ensure we still chunk.
        if not out:
            line = c
            while len(line) > chunk:
                out.append(line[:chunk].rstrip())
                line = line[chunk:].lstrip()
            if line:
                out.append(line)
        return out

    header = (
        f"# {category_code} — Session review results (Session #{int(session_no)})\n"
        f"Reviewed by **{_safe_one_line(reviewer_name, max_len=80)}**"
    )
    messages: list[str] = []
    current = header

    def flush() -> None:
        nonlocal current
        if current.strip():
            messages.append(current.strip())
        # Next messages should start directly with content (no prefix).
        current = ""

    def append_block(block: str) -> None:
        nonlocal current
        block = block.rstrip()
        if not block:
            return
        # First message carries the full header, subsequent ones just say "(continuação)".
        sep = "\n\n" if current.strip() else ""
        candidate = (current + sep + block).strip()
        if len(candidate) <= max_chars:
            current = candidate
            return
        if current.strip() != header.strip():
            flush()
            sep2 = "\n\n" if current.strip() else ""
            candidate = (current + sep2 + block).strip()
            if len(candidate) <= max_chars:
                current = candidate
                return
        # Split by lines if still too large.
        buf = ""
        for ln in block.splitlines():
            ln = ln.rstrip()
            if not ln:
                continue
            add = (buf + "\n" + ln).strip() if buf else ln
            sep3 = "\n\n" if current.strip() else ""
            if len((current + sep3 + add).strip()) <= max_chars:
                buf = add
                continue
            if buf:
                if current.strip() != header.strip():
                    flush()
                sep4 = "\n\n" if current.strip() else ""
                current = (current + sep4 + buf).strip()
                flush()
                buf = ln
            else:
                truncated = _safe_one_line(ln, max_len=max_chars - 50)
                if current.strip() != header.strip():
                    flush()
                sep5 = "\n\n" if current.strip() else ""
                current = (current + sep5 + truncated).strip()
                flush()
                buf = ""
        if buf:
            sep6 = "\n\n" if current.strip() else ""
            if len((current + sep6 + buf).strip()) <= max_chars:
                current = (current + sep6 + buf).strip()
            else:
                flush()
                sep7 = "\n\n" if current.strip() else ""
                current = (current + sep7 + buf).strip()

    by_title = {str(p.get("title")): p for p in (parts or [])}
    decision_order = (("left_as_is", "Left as is"), ("p1ed", "P1'ed"), ("will_be_discussed", "Will be discussed"), ("ignored", "Ignored"))
    cat = _find_category(category_code) or {}
    decisions = cat.get("decisions") or []
    if not isinstance(decisions, list) or not decisions:
        decisions = [d[0] for d in decision_order]
    order = [label for key, label in decision_order if key in decisions]
    cat_emoji = str((_find_category(category_code) or {}).get("emoji") or "").strip()
    left_emoji = str(EMOJI_LIST.get("_P22", "") or "").strip()
    p1_emoji = str(EMOJI_LIST.get("_P1", "") or "").strip()
    ignored_emoji = str(EMOJI_LIST.get("_crane", "") or "").strip()

    def _count(title: str) -> int:
        part = by_title.get(title) or {}
        items = part.get("content") or []
        return len(items) if isinstance(items, list) else 0

    count_left = _count("Left as is") if "Left as is" in order else 0
    count_p1 = _count("P1'ed") if "P1'ed" in order else 0
    count_discuss = _count("Will be discussed") if "Will be discussed" in order else 0
    count_ignored = _count("Ignored") if "Ignored" in order else 0
    count_total = count_left + count_p1 + count_discuss + count_ignored
    summary_parts: list[str] = [f"Total: {count_total}"]
    if "Left as is" in order:
        summary_parts.append(f"Left: {count_left}")
    if "P1'ed" in order:
        summary_parts.append(f"P1: {count_p1}")
    if "Will be discussed" in order:
        summary_parts.append(f"Discuss: {count_discuss}")
    if "Ignored" in order:
        summary_parts.append(f"Ignored: {count_ignored}")
    summary = f"-# _{' | '.join(summary_parts)}_"
    append_block(summary)
    for title in order:
        part = by_title.get(title) or {}
        items = part.get("content") or []
        items = items if isinstance(items, list) else []

        if title == "Left as is":
            prefix = f"{left_emoji} " if left_emoji else ""
        elif title == "P1'ed":
            prefix = f"{p1_emoji} " if p1_emoji else ""
        elif title == "Will be discussed":
            prefix = f"{cat_emoji} " if cat_emoji else ""
        elif title == "Ignored":
            prefix = f"{ignored_emoji} " if ignored_emoji else ""
        else:
            prefix = ""

        lines: list[str] = [f"### __{prefix}{title}__"]
        if not items:
            lines.append("-# None")
        else:
            for it in items:
                if not isinstance(it, dict):
                    continue
                code = str(it.get("code") or "").strip() or "?"
                author = _safe_one_line(str(it.get("author") or "Unknown"), max_len=120)
                raw_comment = str(it.get("comment") or "").strip()
                comment_lines = _split_comment(raw_comment, max_total=2000, chunk=850)
                if comment_lines:
                    # First comment line goes on the same line as the author.
                    first_line = comment_lines[0]
                    lines.append(f"-# **{code} - {author}** — {first_line}")
                    for cl in comment_lines[1:]:
                        lines.append(f"-#   {cl}")
                else:
                    lines.append(f"-# **{code} - {author}**")

        append_block("\n".join(lines))

    flush()
    return [m for m in messages if m.strip() and m.strip() != header.strip()]


async def post_review_results_and_close_thread(
    *,
    bot: discord.Client,
    category_code: str,
    thread_id: int,
    session_no: int,
    parts: list[dict[str, Any]],
    reviewer_name: str = "Session API",
) -> discord.Message:
    """Posts the review results into the session thread and closes it."""
    try:
        channel = await bot.fetch_channel(int(thread_id))
    except Exception:
        channel = None
    if not isinstance(channel, discord.Thread):
        raise ValueError("I couldn't access the active session thread.")

    chunks = build_review_messages_from_parts(
        category_code=category_code,
        session_no=session_no,
        parts=parts,
        reviewer_name=reviewer_name,
    )
    if not chunks:
        chunks = [f"**{category_code} — Session review results (Session #{int(session_no)})**\n(no content)"]

    posted: Optional[discord.Message] = None
    for idx, content in enumerate(chunks):
        msg = await channel.send(content=content)
        if idx == 0:
            posted = msg

    try:
        pass
    except Exception:
        pass

    assert posted is not None
    return posted


async def _wait_for_review_json_upload(
    interaction: discord.Interaction,
    *,
    category_code: str,
    manager_channel: discord.TextChannel,
) -> tuple[list[dict[str, Any]], str, Optional[discord.Message]]:
    await safe_reply(
        interaction,
        "Upload a **JSON** review file in this channel (I'll wait for up to 3 minutes).",
        ephemeral=True,
    )

    def _check(msg: discord.Message) -> bool:
        if msg.author.id != interaction.user.id:
            return False
        if not msg.attachments:
            return False
        if getattr(msg.channel, "id", None) != manager_channel.id:
            return False
        filename = (msg.attachments[0].filename or "").lower()
        return filename.endswith(".json")

    deadline = time.monotonic() + 180
    parts: Optional[list[dict[str, Any]]] = None
    reviewer_name: Optional[str] = None
    upload_msg: Optional[discord.Message] = None
    while True:
        timeout = deadline - time.monotonic()
        if timeout <= 0:
            await safe_reply(interaction, "Timed out while waiting for the JSON upload.", ephemeral=True)
            raise TimeoutError("Timed out while waiting for review upload")
        try:
            upload_msg = await interaction.client.wait_for("message", check=_check, timeout=timeout)  # type: ignore[arg-type]
        except Exception as exc:
            await safe_reply(interaction, "Timed out while waiting for the JSON upload.", ephemeral=True)
            raise TimeoutError("Timed out while waiting for review upload") from exc

        attachment = upload_msg.attachments[0]
        try:
            data = await attachment.read()
        except Exception:
            await safe_reply(interaction, "Failed to download the attached file.", ephemeral=True)
            continue

        try:
            payload = json.loads((data or b"").decode("utf-8-sig", errors="replace"))
        except Exception:
            await safe_reply(interaction, "Invalid JSON file. Please upload a valid review payload.", ephemeral=True)
            continue

        if not isinstance(payload, dict):
            await safe_reply(interaction, "Invalid JSON payload (expected an object).", ephemeral=True)
            continue

        schema_version = payload.get("schemaVersion")
        if schema_version != 1:
            await safe_reply(
                interaction,
                f"Unsupported schemaVersion: {schema_version}. Expected 1.",
                ephemeral=True,
            )
            continue

        session_obj = payload.get("session") or {}
        if not isinstance(session_obj, dict):
            session_obj = {}

        payload_category = (
            session_obj.get("category")
            or payload.get("category")
            or payload.get("categoryType")
            or ""
        )
        if payload_category and str(payload_category).strip().upper() != category_code.upper():
            await safe_reply(
                interaction,
                f"Wrong file category: {payload_category} (expected {category_code}). Please upload the correct JSON.",
                ephemeral=True,
            )
            continue

        items = payload.get("items")
        if not isinstance(items, list):
            await safe_reply(interaction, "Invalid JSON payload (missing items list).", ephemeral=True)
            continue

        parts = build_review_parts_from_export_payload_v1(
            category_code=category_code,
            items=[it for it in items if isinstance(it, dict)],
        )

        reviewer_name = str(payload.get("reviewer") or payload.get("reviewerName") or "").strip()
        reviewer_user_id = session_obj.get("reviewerUserId")
        if isinstance(reviewer_user_id, str) and reviewer_user_id.isdigit():
            reviewer_user_id = int(reviewer_user_id)
        if not isinstance(reviewer_user_id, int):
            reviewer_user_id = None

        if reviewer_user_id and interaction.guild:
            member = interaction.guild.get_member(reviewer_user_id)
            if not member:
                try:
                    member = await interaction.guild.fetch_member(reviewer_user_id)  # type: ignore[attr-defined]
                except Exception:
                    member = None
            if member and has_public_role(member) and has_mapcrew_role(member):
                reviewer_name = member.display_name
            else:
                reviewer_name = "Private Member"

        if not reviewer_name:
            if isinstance(interaction.user, discord.Member):
                reviewer_name = get_display_name(interaction.user)
            else:
                reviewer_name = "Maps Reviewer"

        return parts, reviewer_name, upload_msg


async def _find_session_review_block(
    thread: discord.Thread,
    *,
    category_code: str,
    session_no: int,
    bot_user_id: int | None,
    history_limit: int = 5000,
) -> tuple[list[discord.Message], Optional[discord.Message]]:
    review_messages: list[discord.Message] = []
    end_marker: Optional[discord.Message] = None
    header_token = f"Session review results (Session #{int(session_no)})"
    expected_end_marker = build_end_marker_message(category_code=category_code, session_no=int(session_no))
    capture = False

    async for msg in thread.history(limit=history_limit, oldest_first=True):
        if bot_user_id and getattr(getattr(msg, "author", None), "id", None) != bot_user_id:
            continue
        content = (msg.content or "").strip()
        if not capture:
            if header_token in content:
                capture = True
                review_messages.append(msg)
            continue
        if content == expected_end_marker:
            end_marker = msg
            break
        review_messages.append(msg)

    return review_messages, end_marker


async def edit_last_session_review(interaction: discord.Interaction, *, category_code: str) -> None:
    msg = await _get_panel_message(interaction, category_code)
    if not msg or not msg.embeds:
        await safe_reply(interaction, "Could not read the panel message embed.", ephemeral=True)
        return

    meta = parse_panel_footer(getattr(msg.embeds[0].footer, "text", "") if msg.embeds[0].footer else "")
    last_session_no = meta.get("last")
    if not last_session_no:
        await safe_reply(interaction, "There is no finished session to edit for this category.", ephemeral=True)
        return

    try:
        channel = await get_category_thread(interaction.client, category_code=category_code)
    except Exception:
        channel = None
    if not isinstance(channel, discord.Thread):
        await safe_reply(interaction, "I couldn't access the category thread.", ephemeral=True)
        return

    manager_channel = interaction.channel
    if not isinstance(manager_channel, discord.TextChannel):
        await safe_reply(interaction, "This action must be used from the session manager text channel.", ephemeral=True)
        return

    try:
        parts, reviewer_name, upload_msg = await _wait_for_review_json_upload(
            interaction,
            category_code=category_code,
            manager_channel=manager_channel,
        )
    except TimeoutError:
        return

    bot_user_id = getattr(getattr(interaction.client, "user", None), "id", None)
    review_messages, end_marker = await _find_session_review_block(
        channel,
        category_code=category_code,
        session_no=int(last_session_no),
        bot_user_id=bot_user_id,
    )
    if not review_messages:
        await safe_reply(
            interaction,
            f"Could not find the review messages for Session #{int(last_session_no)}.",
            ephemeral=True,
        )
        return

    chunks = build_review_messages_from_parts(
        category_code=category_code,
        session_no=int(last_session_no),
        parts=parts,
        reviewer_name=reviewer_name or "Maps Reviewer",
    )
    if not chunks:
        chunks = [f"**{category_code} — Session review results (Session #{int(last_session_no)})**\n(no content)"]

    shared_count = min(len(review_messages), len(chunks))
    for index in range(shared_count):
        await review_messages[index].edit(content=chunks[index])

    if len(review_messages) > len(chunks):
        for extra_message in review_messages[len(chunks) :]:
            try:
                await extra_message.delete()
            except Exception:
                logger.exception("Failed to delete stale review continuation message %s", extra_message.id)
    elif len(chunks) > len(review_messages):
        extra_chunks = chunks[len(review_messages) :]
        if end_marker is not None and extra_chunks:
            await end_marker.edit(content=extra_chunks[0])
            for chunk in extra_chunks[1:]:
                await channel.send(content=chunk)
            await channel.send(build_end_marker_message(category_code=category_code, session_no=int(last_session_no)))
        else:
            for chunk in extra_chunks:
                await channel.send(content=chunk)

    try:
        if upload_msg:
            await upload_msg.delete()
    except Exception:
        logger.exception("Failed to delete the edited review upload message (channel=%s)", getattr(manager_channel, "id", None))

    await safe_reply(
        interaction,
        f"✏️ Session #{int(last_session_no)} review updated: {review_messages[0].jump_url}",
        ephemeral=True,
    )

def _build_review_results_txt(*, category_code: str, session_no: int, parts: list[dict[str, Any]]) -> tuple[str, bytes]:
    def _section(title: str) -> list[str]:
        part = next((p for p in parts if p.get("title") == title), None)
        items = (part or {}).get("content") or []
        out: list[str] = []
        for it in items:
            code = it.get("code")
            author = it.get("author")
            comment = (it.get("comment") or "").strip()
            if comment:
                out.append(f"{code} - {author} - {comment}")
            else:
                out.append(f"{code} - {author}")
        return out

    lines: list[str] = []
    lines.append("####REVIEW RESULTS####")
    lines.append(f"CAT:{category_code}")
    lines.append(f"SESSION:{session_no}")
    lines.append("")
    for title in ("Left as is", "P1'ed", "Will be discussed", "Ignored"):
        lines.append(f"[{title}]")
        sec = _section(title)
        lines.extend(sec if sec else ["None"])
        lines.append("")
    filename = f"review_{category_code}_session_{session_no}.txt"
    return filename, "\n".join(lines).encode("utf-8")


def _embeds_fit(parts: list[dict[str, Any]]) -> bool:
    # Conservative heuristic: if any section field would exceed 1024, or too many lines overall,
    # we fall back to attachment to avoid Discord embed limits.
    total_lines = 0
    for title in ("Left as is", "P1'ed", "Will be discussed", "Ignored"):
        part = next((p for p in parts if p.get("title") == title), None)
        items = (part or {}).get("content") or []
        lines = []
        for it in items:
            code = it.get("code")
            author = it.get("author")
            comment = (it.get("comment") or "").strip()
            lines.append(f"{code} - {author} — {comment}" if comment else f"{code} - {author}")
        total_lines += len(lines)
        if len("\n".join(lines)) > 950:
            return False
    # If it's a lot of maps/comments, prefer attachment.
    return total_lines <= 60


def _category_submission_limit(category_code: str) -> int:
    cat = _find_category(category_code) or {}
    raw = cat.get("submissionlimit", None)
    try:
        value = int(raw) if raw is not None else 3
        if value <= 0:
            return 0
        return value
    except Exception:
        return 3


def _extract_map_codes(content: str) -> list[str]:
    codes = set(re.findall(r"@\d+", content or ""))
    return sorted(codes)


def _server_alias(author: discord.abc.User) -> str:
    """Prefer the guild alias (nickname/display name) over username#discriminator."""
    # In guild contexts, message.author is typically a Member (has .display_name).
    name = getattr(author, "display_name", None) or getattr(author, "global_name", None) or getattr(author, "name", None)
    return str(name or author)


async def _fresh_panel_message(interaction: discord.Interaction) -> Optional[discord.Message]:
    """Returns the latest version of the panel message (avoids stale interaction.message)."""
    msg = interaction.message
    if not msg:
        return None
    ch = getattr(msg, "channel", None)
    if not isinstance(ch, discord.abc.Messageable):
        return msg
    try:
        return await ch.fetch_message(msg.id)  # type: ignore[attr-defined]
    except Exception:
        return msg


async def _get_panel_message(interaction: discord.Interaction, category_code: str) -> Optional[discord.Message]:
    """Finds the bot-authored panel message for a category inside session_manager.

    We avoid relying solely on interaction.message because it can be stale or not bot-authored.
    """
    # 1) Best case: the interaction message is already the correct panel and bot-authored.
    msg = await _fresh_panel_message(interaction)
    bot_user = interaction.client.user
    bot_id = getattr(bot_user, "id", None)
    if msg and msg.embeds and bot_id and getattr(getattr(msg, "author", None), "id", None) == bot_id:
        footer = getattr(msg.embeds[0].footer, "text", "") if msg.embeds[0].footer else ""
        if footer.startswith(f"map_submission_panel:{category_code}"):
            return msg

    # 2) Fallback: scan session_manager for the latest bot-authored panel with our footer.
    try:
        from resources.channels import CHANNELS
    except Exception:
        CHANNELS = {}

    session_manager_id = (CHANNELS.get("session_manager") if isinstance(CHANNELS, dict) else None) or ""
    if not str(session_manager_id).isdigit():
        return msg

    try:
        session_manager = await interaction.client.fetch_channel(int(session_manager_id))
    except Exception:
        session_manager = None

    if not isinstance(session_manager, discord.TextChannel):
        return msg

    candidates: list[discord.Message] = []
    try:
        candidates = await session_manager.pins()
    except Exception:
        candidates = []
    if not candidates:
        try:
            candidates = [m async for m in session_manager.history(limit=200, oldest_first=False)]
        except Exception:
            candidates = []

    for m in candidates:
        if not bot_id or getattr(getattr(m, "author", None), "id", None) != bot_id:
            continue
        if not m.embeds:
            continue
        footer = getattr(m.embeds[0].footer, "text", "") if m.embeds[0].footer else ""
        if footer.startswith(f"map_submission_panel:{category_code}"):
            return m
    return msg


def _category_thread_name(category_code: str) -> str:
    cat = _find_category(category_code) or {}
    description = str(cat.get("description", category_code))
    return f"{description}"


def _thread_matches_category(thread_name: str, *, category_code: str) -> bool:
    expected = _category_thread_name(category_code).strip()
    actual = (thread_name or "").strip()
    if not expected or not actual:
        return False
    if actual == expected:
        return True

    # Accept legacy/shared thread titles like "Mechanism (P6|P12)".
    actual_upper = actual.upper()
    expected_upper = expected.upper()
    category_upper = (category_code or "").strip().upper()
    if not actual_upper.startswith(expected_upper):
        return False
    category_tokens = re.findall(r"P\d+", actual_upper)
    return category_upper in category_tokens


def _category_emoji(category_code: str) -> str:
    cat = _find_category(category_code) or {}
    return str(cat.get("emoji") or "").strip()


def build_start_marker_message(*, category_code: str, session_no: int) -> str:
    parchment = str(EMOJI_LIST.get("_parchment", "") or "").strip()
    cat_emoji = _category_emoji(category_code)
    return f"-# {parchment} Session #{int(session_no)} Started {cat_emoji}".strip()


def build_end_marker_message(*, category_code: str, session_no: int) -> str:
    megaphone = str(EMOJI_LIST.get("_megaphone", "") or "").strip()
    cat_emoji = _category_emoji(category_code)
    return f"-# {megaphone} Session #{int(session_no)} Finished {cat_emoji}".strip()


async def _ensure_category_thread_embed(
    thread: discord.Thread, *, category_code: str, history_limit: int = 200
) -> None:
    """Ensures the initial category embed exists (updates if already present)."""
    bot_id = getattr(getattr(thread, "guild", None), "me", None)
    bot_user_id = getattr(getattr(thread, "client", None), "user", None)
    bot_id = getattr(bot_user_id, "id", None) or getattr(bot_id, "id", None)
    target_title_prefix = "Official Rotation"
    embeds = _build_category_thread_embeds(category_code)

    if not bot_id:
        msg = await thread.send(embeds=embeds)
        try:
            await msg.pin()
        except Exception:
            pass
        return

    try:
        async for msg in thread.history(limit=history_limit, oldest_first=False):
            if getattr(getattr(msg, "author", None), "id", None) != bot_id:
                continue
            if not msg.embeds:
                continue
            title = str(getattr(msg.embeds[0], "title", "") or "")
            if not title.startswith(target_title_prefix):
                continue
            try:
                await msg.edit(embeds=embeds)
            except Exception:
                pass
            try:
                if not msg.pinned:
                    await msg.pin()
            except Exception:
                pass
            return
    except Exception:
        pass

    msg = await thread.send(embeds=embeds)
    try:
        await msg.pin()
    except Exception:
        pass


def _build_category_thread_embeds(category_code: str) -> list[discord.Embed]:
    cat = _find_category(category_code) or {"name": category_code, "description": category_code, "color": "#2B2D31"}
    color = int(str(cat.get("color", "#2B2D31")).replace("#", "0x"), 16)
    description = str(cat.get("description", category_code))
    submission_limit = int(cat.get("submissionlimit", 3))
    icon_url = str(cat.get("picture", ""))
    rules = cat.get("submissionRules") or []

    intro_lines: list[str] = []
    about_lines: list[str] = []
    if str(cat.get("name")) == "P66":
        intro_lines.append(
            "_Submit your maps for the Thematic (P66) category here. Please read the following rules before submitting any maps, "
            "as your maps will be ignored if they are not followed._"
        )
        intro_lines.append("")
        intro_lines.append("- Map Crew members will reply to this thread periodically after reviewing the maps that have been posted.")
        intro_lines.append("- You may submit as many maps as you want, there is not any limit.")
        intro_lines.append("- You may submit other people's themed maps here.")
        intro_lines.append("- You may submit already (Art) permed maps here.")
        intro_lines.append("- Please specify which theme you are submitting your map to when posting, example: @xxxxxxx - Halloween.")
        intro_lines.append("- Screenshots and XMLs of your maps are not neccessary.")
        intro_lines.extend(
            [
                "- The maps must be themed as the following, otherwise they will be straight denied:",
                "  - Oldschool art",
                "  - Valentine's Day",
                "  - Easter",
                "  - Halloween",
                "  - Christmas/Winter",
                "  - Spring/Nature",
                "  - Summer",
                "  - Autumn",
                "  - Animals",
                "  - Cartoons",
                "  - Food/Fruits",
                "  - Landscape",
                "  - Pokemon/Nintendo",
                "  - Space",
                "  - Mouse",
            ]
        )
        intro_lines.append(
            "- You may submit maps for these themes anytime here. We will save and add them to the game when appropriate."
        )
        intro_lines.append("- Once you have submitted a map, do not post it again at a later date if it has not been edited in any way.")
        intro_lines.append("- Review decisions are final. Do not use this thread to argue with whatever decision was made about your map.")
        intro_lines.append("- We will not comment on the maps, we will just make it stated through a post that a review was done.")
        intro_lines.append("- Please make sure you understand the criteria for the category you are submitting your maps to.")
        #about_lines.append("")
        #about_lines.append(f"**About {category_code}**")
        about_lines.append("Please make sure your map meets the following criteria before submitting.")
        if rules:
            about_lines.extend([f"- {rule}" for rule in rules])
        else:
            about_lines.append("- (No category-specific rules configured yet.)")
    else:
        intro_lines.append(
            "_Submit your maps for the "
            f"{description} category here. Please read the following rules before submitting any maps, "
            "as your maps will be ignored if they are not followed._"
        )
        intro_lines.append("")
        intro_lines.append(
            "- Map crew members will reply to the thread periodically with their review of the maps that have been posted."
        )
        if submission_limit <= 0:
            intro_lines.append("- You may submit as many maps as you want, there is not any limit.")
        else:
            intro_lines.append(
                f"- You may submit a total of **{submission_limit}** maps per session. In other words, once you have posted {submission_limit} maps, "
                "do not post any more until a map crew member replies to the thread with a review of your maps. "
                f"If you post more than {submission_limit} maps, they will be automatically ignored."
            )
        intro_lines.append("- Simply post the map code (@123456). Screenshots and XML are not necessary.")
        intro_lines.append(
            "- Once you have submitted a map, do not post it again at a later date if it has not been edited in any way."
        )
        intro_lines.append(
            "- Do not post maps for multiple categories. For example, if you have already posted your map in the "
            f"{category_code} thread, you may not submit that map to any other category."
        )
        intro_lines.append("- Review decisions are final. Do not use this thread to argue with whatever decision was made about your map.")
        intro_lines.append("- Please make sure you understand the criteria for the category you are submitting your maps to.")
        #intro_lines.append("")
        #about_lines.append(f"**About {category_code}**")
        about_lines.append("Please make sure your map meets the following criteria before submitting.")
        if rules:
            about_lines.extend([f"- {rule}" for rule in rules])
        else:
            about_lines.append("- (No category-specific rules configured yet.)")

    intro_embed = discord.Embed(
        title=f"Official Rotation — {description}",
        description="\n".join(intro_lines),
        color=color
    )
    if icon_url:
        intro_embed.set_thumbnail(url=icon_url)

    about_embed = discord.Embed(
        title=f"About {category_code}",
        description="\n".join(about_lines),
        color=color,
    )

    return [intro_embed, about_embed]


async def _get_forum_channel(client: discord.Client, *, category_code: str) -> discord.ForumChannel:
    forum_id = SUBMISSION_CHANNELS.get(category_code, "")
    if not forum_id or not str(forum_id).isdigit():
        raise ValueError(f"Submission forum channel is not configured for {category_code}.")
    forum_channel = await client.fetch_channel(int(forum_id))
    if not isinstance(forum_channel, discord.ForumChannel):
        raise ValueError(f"Configured submission channel for {category_code} is not a forum channel.")
    return forum_channel


async def _find_category_thread(
    forum_channel: discord.ForumChannel, *, category_code: str
) -> Optional[discord.Thread]:
    for thread in list(getattr(forum_channel, "threads", []) or []):
        if _thread_matches_category(getattr(thread, "name", ""), category_code=category_code):
            return thread
    try:
        async for thread in forum_channel.archived_threads(limit=100):
            if _thread_matches_category(getattr(thread, "name", ""), category_code=category_code):
                try:
                    await thread.edit(archived=False, locked=False)
                except Exception:
                    pass
                return thread
    except Exception:
        pass
    return None


async def get_category_thread(
    client: discord.Client, *, category_code: str
) -> Optional[discord.Thread]:
    forum_channel = await _get_forum_channel(client, category_code=category_code)
    return await _find_category_thread(forum_channel, category_code=category_code)


async def get_or_create_category_thread(
    client: discord.Client, *, category_code: str
) -> discord.Thread:
    forum_channel = await _get_forum_channel(client, category_code=category_code)
    thread = await _find_category_thread(forum_channel, category_code=category_code)
    if thread:
        return thread

    logger.info("Creating category thread for %s in forum %s", category_code, getattr(forum_channel, "id", None))
    created = await forum_channel.create_thread(
        name=_category_thread_name(category_code),
        content="",
        embeds=_build_category_thread_embeds(category_code),
    )

    if isinstance(created, tuple) and len(created) == 2:
        thread = created[0]
    elif hasattr(created, "thread") and hasattr(created, "message"):
        thread = created.thread
    else:
        thread = created

    if not isinstance(thread, discord.Thread):
        raise ValueError("Failed to create the category thread.")

    return thread


async def start_new_session(
    interaction: discord.Interaction,
    *,
    category_code: str,
    last_finished_ts: Optional[int] = None,
) -> None:
    msg = await _get_panel_message(interaction, category_code)
    if not msg or not msg.embeds:
        await safe_reply(interaction, "Could not read the panel message embed.", ephemeral=True)
        return
    try:
        result = await _start_new_session_for_panel(
            client=interaction.client,
            panel_msg=msg,
            category_code=category_code,
            last_finished_ts=last_finished_ts,
        )
    except Exception as exc:
        await safe_reply(interaction, f"Could not access the category thread: {exc}", ephemeral=True)
        return

    if result.get("is_active"):
        await safe_reply(
            interaction,
            f"An active session already exists: <#{int(result['thread_id'])}>.",
            ephemeral=True,
        )
        return

    await safe_reply(
        interaction,
        f"✅ Session started in <#{int(result['thread_id'])}> (Session #{int(result['session_no'])})",
        ephemeral=True,
    )


async def _start_new_session_for_panel(
    *,
    client: discord.Client,
    panel_msg: discord.Message,
    category_code: str,
    last_finished_ts: Optional[int] = None,
) -> dict[str, Any]:
    meta = parse_panel_footer(getattr(panel_msg.embeds[0].footer, "text", "") if panel_msg.embeds[0].footer else "")
    last_no = int(meta.get("last") or 0)
    session_no = last_no + 1
    thread = await get_or_create_category_thread(client, category_code=category_code)

    try:
        await _ensure_category_thread_embed(thread, category_code=category_code)
    except Exception:
        logger.exception("Failed to update initial embed for %s", category_code)

    bot_user_id = getattr(getattr(client, "user", None), "id", None)
    state = await get_session_marker_state(thread=thread, history_limit=5000, bot_user_id=bot_user_id)
    if state.get("is_active"):
        computed_last_finished_ts: int | None = None
        last_end_message_id = state.get("last_end_message_id")
        if last_end_message_id:
            try:
                msg = await thread.fetch_message(int(last_end_message_id))
                if msg and msg.created_at:
                    computed_last_finished_ts = int(msg.created_at.timestamp())
            except Exception:
                computed_last_finished_ts = None
        meta_last_end = meta.get("last_end")
        if not isinstance(meta_last_end, int):
            meta_last_end = None
        resolved_last_finished_ts = last_finished_ts or computed_last_finished_ts or meta_last_end
        active_no = state.get("last_session_no") or meta.get("current_no")
        try:
            active_no = int(active_no) if active_no is not None else None
        except Exception:
            active_no = None
        new_embed = build_submission_panel_embed(
            category_code,
            last_session_no=max(last_no, int(active_no or 0)),
            current_thread_id=int(thread.id),
            current_session_no=int(active_no) if active_no else None,
            is_locked=bool(thread.locked),
            last_finished_ts=resolved_last_finished_ts,
        )
        try:
            from ui.map_submission_view import MapSubmissionPanelView

            await panel_msg.edit(
                embeds=[new_embed],
                view=MapSubmissionPanelView(category_code, show_start=False, is_locked=bool(thread.locked)),
            )
        except Exception:
            logger.exception("Failed to update panel embed for %s", category_code)
        return {"is_active": True, "thread_id": int(thread.id), "session_no": active_no}

    await thread.send(build_start_marker_message(category_code=category_code, session_no=session_no))

    meta_last_end = meta.get("last_end")
    if isinstance(meta_last_end, str) and str(meta_last_end).isdigit():
        meta_last_end = int(meta_last_end)
    if not isinstance(meta_last_end, int):
        meta_last_end = None
    resolved_last_finished_ts = last_finished_ts or meta_last_end
    new_embed = build_submission_panel_embed(
        category_code,
        last_session_no=session_no,
        current_thread_id=int(thread.id),
        current_session_no=int(session_no),
        is_locked=bool(thread.locked),
        last_finished_ts=resolved_last_finished_ts,
    )
    try:
        from ui.map_submission_view import MapSubmissionPanelView

        await panel_msg.edit(
            embeds=[new_embed],
            view=MapSubmissionPanelView(category_code, show_start=False, is_locked=bool(thread.locked)),
        )
    except Exception:
        logger.exception("Failed to update panel embed for %s", category_code)

    return {"is_active": False, "thread_id": int(thread.id), "session_no": int(session_no)}


async def download_session_export(interaction: discord.Interaction, *, category_code: str) -> None:
    msg = await _get_panel_message(interaction, category_code)
    if not msg or not msg.embeds:
        await safe_reply(interaction, "Could not read the panel message embed.", ephemeral=True)
        return

    try:
        thread = await get_category_thread(interaction.client, category_code=category_code)
    except Exception:
        thread = None
    if not isinstance(thread, discord.Thread):
        await safe_reply(interaction, "I couldn't access the category thread.", ephemeral=True)
        return

    bot_user_id = getattr(getattr(interaction.client, "user", None), "id", None)
    state = await get_session_marker_state(thread=thread, history_limit=5000, bot_user_id=bot_user_id)
    if not state.get("is_active"):
        await safe_reply(interaction, "There is no active session for this category.", ephemeral=True)
        return

    try:
        bot_user_id = getattr(getattr(interaction.client, "user", None), "id", None)
        data = await collect_session_maps(
            thread=thread,
            category_code=category_code,
            history_limit=5000,
            bot_user_id=bot_user_id,
        )
    except Exception:
        logger.exception("Failed to scan thread history for export (%s)", thread.id)
        await safe_reply(interaction, "Failed to scan the session thread history.", ephemeral=True)
        return

    today = datetime.utcnow().date().isoformat()
    filename = f"session_{category_code}_{today}.json"
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    file = discord.File(BytesIO(payload), filename=filename)
    await safe_reply(interaction, "📦 Session export:", ephemeral=True, files=[file])


async def submit_review_and_close_session(interaction: discord.Interaction, *, category_code: str) -> None:
    msg = await _get_panel_message(interaction, category_code)
    if not msg or not msg.embeds:
        await safe_reply(interaction, "Could not read the panel message embed.", ephemeral=True)
        return

    meta = parse_panel_footer(getattr(msg.embeds[0].footer, "text", "") if msg.embeds[0].footer else "")
    current_session_no = meta.get("current_no")
    last_no = int(meta.get("last") or 0)
    if not current_session_no:
        await safe_reply(interaction, "There is no active session for this category.", ephemeral=True)
        return

    try:
        channel = await get_category_thread(interaction.client, category_code=category_code)
    except Exception:
        channel = None
    if not isinstance(channel, discord.Thread):
        await safe_reply(interaction, "I couldn't access the category thread.", ephemeral=True)
        return

    bot_user_id = getattr(getattr(interaction.client, "user", None), "id", None)
    state = await get_session_marker_state(thread=channel, history_limit=5000, bot_user_id=bot_user_id)
    if not state.get("is_active"):
        await safe_reply(interaction, "There is no active session for this category.", ephemeral=True)
        return

    # Panels live in session_manager; the file should be uploaded there.
    manager_channel = interaction.channel
    if not isinstance(manager_channel, discord.TextChannel):
        await safe_reply(interaction, "This action must be used from the session manager text channel.", ephemeral=True)
        return

    try:
        parts, reviewer_name, upload_msg = await _wait_for_review_json_upload(
            interaction,
            category_code=category_code,
            manager_channel=manager_channel,
        )
    except TimeoutError:
        return

    posted = await post_review_results_and_close_thread(
        bot=interaction.client,
        category_code=category_code,
        thread_id=int(channel.id),
        session_no=int(current_session_no),
        parts=parts,
        reviewer_name=reviewer_name or "Maps Reviewer",
    )

    # Clean up the upload message in session_manager to avoid clutter and to keep the panel lookup stable.
    try:
        if upload_msg:
            await upload_msg.delete()
    except Exception:
        logger.exception("Failed to delete the review upload message (channel=%s)", getattr(manager_channel, "id", None))

    # Close out session (marker only)
    last_finished_ts: int | None = None
    try:
        end_msg = await channel.send(build_end_marker_message(category_code=category_code, session_no=int(current_session_no)))
        if end_msg and end_msg.created_at:
            last_finished_ts = int(end_msg.created_at.timestamp())
    except Exception:
        pass

    # Clear current session in panel embed (no watcher / no DB).
    cleared_embed = build_submission_panel_embed(
        category_code,
        last_session_no=max(last_no, int(current_session_no)),
        is_locked=bool(getattr(channel, "locked", False)),
        last_finished_ts=last_finished_ts,
    )
    try:
        await msg.edit(embeds=[cleared_embed])
    except Exception:
        logger.exception("Failed to clear current session in panel embed for %s", category_code)

    await safe_reply(interaction, f"📣 Results posted in the session thread: {posted.jump_url}", ephemeral=True)

    if AUTO_CREATE_NEXT_SESSION:
        try:
            await start_new_session(interaction, category_code=category_code, last_finished_ts=last_finished_ts)
        except Exception:
            logger.exception("Failed to auto-create next session (%s)", category_code)

