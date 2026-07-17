"""In-Discord session review flow.

Provides an alternative to the JSON-upload review: mapcrews open a temporary
(private) thread from the session-manager panel where the bot posts one message
per submitted map. Each map has a decision select + an editable comment, similar
to the "Add public review" flow in discussions. When finished, the results are
posted into the category thread and the session is closed (reusing the existing
submission review helpers).

State is stored entirely inside the review-thread messages (no DB), matching the
rest of the project.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

import discord

from helpers.interaction_utils import safe_reply
from helpers.session_export import collect_session_maps, get_session_marker_state
from helpers.submission_facade import (
    AUTO_CREATE_NEXT_SESSION,
    _empty_review_parts,
    _get_panel_message,
    _start_new_session_for_panel,
    build_end_marker_message,
    get_category_thread,
    post_review_results_and_close_thread,
)
from helpers.submission_panel import build_submission_panel_embed, parse_panel_footer
from helpers.validation_utils import get_display_name
from resources.category_list import CATEGORY_LIST

logger = logging.getLogger(__name__)


ITEM_FOOTER_PREFIX = "session_review_item"
CONTROL_FOOTER_PREFIX = "session_review_control"

DECISION_LABELS: dict[str, str] = {
    "left_as_is": "Left as is",
    "p1ed": "P1'ed",
    "will_be_discussed": "Will be discussed",
    "ignored": "Ignored",
}
DECISION_VALUE_TO_TITLE: dict[str, str] = {
    "left_as_is": "Left as is",
    "p1ed": "P1'ed",
    "will_be_discussed": "Will be discussed",
    "ignored": "Ignored",
}
_DECISION_ORDER = ["left_as_is", "p1ed", "will_be_discussed", "ignored"]
_NO_COMMENT_PLACEHOLDER = "*No comment yet.*"


def _find_category(code: str) -> Optional[dict[str, Any]]:
    return next((c for c in CATEGORY_LIST if c.get("name") == code), None)


def available_decision_values(category_code: str) -> list[str]:
    """Decision values offered for a category (respects category_list decisions)."""
    cat = _find_category(category_code) or {}
    raw = cat.get("decisions")
    if not isinstance(raw, list) or not raw:
        raw = list(_DECISION_ORDER)
    values = [d for d in _DECISION_ORDER if d in raw]
    if "left_as_is" not in values:
        values.insert(0, "left_as_is")
    if "ignored" not in values:
        values.append("ignored")
    return values


def _default_decision(category_code: str) -> str:
    values = available_decision_values(category_code)
    return values[0] if values else "left_as_is"


def _has_manage_permission(interaction: discord.Interaction) -> bool:
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    channel = interaction.channel
    if not member or not channel:
        return False
    try:
        perms = channel.permissions_for(member)
    except Exception:
        return False
    return bool(perms.manage_threads or perms.manage_messages)


def _review_thread_name(category_code: str, session_no: int) -> str:
    cat = _find_category(category_code) or {}
    description = str(cat.get("description", category_code))
    name = f"Review — {description} #{int(session_no)}"
    return name[:100]


def build_control_embed(*, category_code: str, session_no: int, total: int) -> discord.Embed:
    cat = _find_category(category_code) or {}
    color = int(str(cat.get("color", "#2B2D31")).replace("#", "0x"), 16)
    embed = discord.Embed(
        title=f"{cat.get('emoji', '')} Review — {cat.get('description', category_code)} (Session #{int(session_no)})".strip(),
        description=(
            "Write a review for each map below.\n\n"
            "- **Set decision**: choose the outcome for each map.\n"
            "- **Edit comment**: add or update the public comment (optional).\n"
            "- **Finish & post review**: posts the results into the category thread and closes the session.\n"
            "- **Cancel**: discards this review chat without posting.\n\n"
            f"Maps to review: **{int(total)}** (duplicates / over-limit maps are auto-ignored on finish)."
        ),
        color=color,
    )
    embed.set_footer(text=f"{CONTROL_FOOTER_PREFIX}:{category_code}:{int(session_no)}")
    return embed


def build_item_embed(
    *,
    category_code: str,
    code: str,
    submitter: str,
    decision_value: str,
    comment: str,
) -> discord.Embed:
    cat = _find_category(category_code) or {}
    color = int(str(cat.get("color", "#2B2D31")).replace("#", "0x"), 16)
    decision_value = decision_value if decision_value in DECISION_LABELS else _default_decision(category_code)
    embed = discord.Embed(title=str(code), color=color)
    embed.add_field(name="Submitter", value=str(submitter or "Unknown"), inline=True)
    embed.add_field(name="Decision", value=DECISION_LABELS.get(decision_value, "Left as is"), inline=True)
    embed.description = (comment or "").strip() or _NO_COMMENT_PLACEHOLDER
    embed.set_footer(text=f"{ITEM_FOOTER_PREFIX}:{category_code}:{code}:{decision_value}")
    return embed


def _parse_item_footer(text: str) -> Optional[tuple[str, str, str]]:
    raw = (text or "").strip()
    if not raw.startswith(f"{ITEM_FOOTER_PREFIX}:"):
        return None
    parts = raw.split(":")
    # session_review_item : category : code : decision
    if len(parts) != 4:
        return None
    _, category, code, decision = parts
    return category.strip(), code.strip(), decision.strip()


def _read_item_from_message(message: discord.Message) -> Optional[dict[str, str]]:
    if not message or not message.embeds:
        return None
    embed = message.embeds[0]
    footer_text = embed.footer.text if embed.footer else ""
    parsed = _parse_item_footer(footer_text or "")
    if not parsed:
        return None
    category, code, decision = parsed
    submitter = "Unknown"
    for field in embed.fields:
        if field.name == "Submitter":
            submitter = str(field.value or "Unknown")
            break
    comment = str(embed.description or "").strip()
    if comment == _NO_COMMENT_PLACEHOLDER:
        comment = ""
    if decision not in DECISION_LABELS:
        decision = _default_decision(category)
    return {"category": category, "code": code, "decision": decision, "comment": comment, "submitter": submitter}


def _find_existing_review_thread(
    parent: discord.TextChannel, *, category_code: str, session_no: int
) -> Optional[discord.Thread]:
    target = _review_thread_name(category_code, session_no)
    for thread in list(getattr(parent, "threads", []) or []):
        if getattr(thread, "name", "") == target and not getattr(thread, "archived", False):
            return thread
    return None


async def start_session_review(interaction: discord.Interaction, *, category_code: str) -> None:
    """Creates the temporary review thread and posts one message per map."""
    client = interaction.client
    parent = interaction.channel
    if not isinstance(parent, discord.TextChannel):
        await safe_reply(interaction, "This action must be used from the session manager channel.", ephemeral=True)
        return

    panel_msg = await _get_panel_message(interaction, category_code)
    if not panel_msg or not panel_msg.embeds:
        await safe_reply(interaction, "Could not read the panel message.", ephemeral=True)
        return

    footer_text = getattr(panel_msg.embeds[0].footer, "text", "") if panel_msg.embeds[0].footer else ""
    meta = parse_panel_footer(footer_text)
    current_no = meta.get("current_no")
    if not current_no:
        await safe_reply(interaction, "There is no active session for this category.", ephemeral=True)
        return

    try:
        category_thread = await get_category_thread(client, category_code=category_code)
    except Exception:
        category_thread = None
    if not isinstance(category_thread, discord.Thread):
        await safe_reply(interaction, "I couldn't access the category thread.", ephemeral=True)
        return

    bot_user_id = getattr(getattr(client, "user", None), "id", None)
    state = await get_session_marker_state(thread=category_thread, history_limit=5000, bot_user_id=bot_user_id)
    if not state.get("is_active"):
        await safe_reply(interaction, "There is no active session for this category.", ephemeral=True)
        return

    existing = _find_existing_review_thread(parent, category_code=category_code, session_no=int(current_no))
    if existing:
        await safe_reply(interaction, f"A review chat already exists: <#{existing.id}>.", ephemeral=True)
        return

    try:
        data = await collect_session_maps(
            thread=category_thread,
            category_code=category_code,
            history_limit=5000,
            bot_user_id=bot_user_id,
        )
    except Exception:
        logger.exception("Failed to collect session maps for review (%s)", category_code)
        await safe_reply(interaction, "Failed to scan the session thread history.", ephemeral=True)
        return

    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in data.get("maps", []) or []:
        code = str(entry.get("mapCode") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        if entry.get("ignored"):
            continue
        items.append((code, str(entry.get("submitter") or "Unknown")))

    if not items:
        await safe_reply(interaction, "No maps to review in the current session.", ephemeral=True)
        return

    name = _review_thread_name(category_code, int(current_no))
    review_thread: Optional[discord.Thread] = None
    try:
        review_thread = await parent.create_thread(
            name=name,
            type=discord.ChannelType.private_thread,
            invitable=False,
        )
    except Exception:
        try:
            review_thread = await parent.create_thread(name=name, type=discord.ChannelType.public_thread)
        except Exception:
            logger.exception("Failed to create review thread for %s", category_code)
    if not isinstance(review_thread, discord.Thread):
        await safe_reply(interaction, "Failed to create the review chat.", ephemeral=True)
        return

    try:
        await review_thread.add_user(interaction.user)
    except Exception:
        pass

    from ui.session_review_view import SessionReviewControlView, SessionReviewItemView

    try:
        await review_thread.send(
            embed=build_control_embed(category_code=category_code, session_no=int(current_no), total=len(items)),
            view=SessionReviewControlView(category_code),
        )
    except Exception:
        logger.exception("Failed to post review control message for %s", category_code)

    default_decision = _default_decision(category_code)
    for code, submitter in items:
        try:
            await review_thread.send(
                embed=build_item_embed(
                    category_code=category_code,
                    code=code,
                    submitter=submitter,
                    decision_value=default_decision,
                    comment="",
                ),
                view=SessionReviewItemView(category_code, decision_value=default_decision),
            )
        except Exception:
            logger.exception("Failed to post review item %s in %s", code, category_code)

    await safe_reply(interaction, f"📝 Review chat created: <#{review_thread.id}>", ephemeral=True)


async def set_item_decision(
    interaction: discord.Interaction, *, category_code: str, decision_value: str
) -> None:
    item = _read_item_from_message(interaction.message) if interaction.message else None
    if not item:
        await safe_reply(interaction, "Could not read this review item.", ephemeral=True)
        return

    from ui.session_review_view import SessionReviewItemView

    embed = build_item_embed(
        category_code=category_code,
        code=item["code"],
        submitter=item["submitter"],
        decision_value=decision_value,
        comment=item["comment"],
    )
    try:
        await interaction.response.edit_message(
            embed=embed,
            view=SessionReviewItemView(category_code, decision_value=decision_value),
        )
    except Exception:
        logger.exception("Failed to update decision for %s", item.get("code"))
        await safe_reply(interaction, "Failed to update the decision.", ephemeral=True)


async def set_item_comment(interaction: discord.Interaction, *, category_code: str, comment: str) -> None:
    item = _read_item_from_message(interaction.message) if interaction.message else None
    if not item:
        await safe_reply(interaction, "Could not read this review item.", ephemeral=True)
        return

    from ui.session_review_view import SessionReviewItemView

    decision_value = item["decision"]
    embed = build_item_embed(
        category_code=category_code,
        code=item["code"],
        submitter=item["submitter"],
        decision_value=decision_value,
        comment=comment,
    )
    try:
        await interaction.response.edit_message(
            embed=embed,
            view=SessionReviewItemView(category_code, decision_value=decision_value),
        )
    except Exception:
        logger.exception("Failed to update comment for %s", item.get("code"))
        await safe_reply(interaction, "Failed to update the comment.", ephemeral=True)


def _read_control_session_no(thread: discord.Thread) -> Optional[int]:
    match = re.search(r"#(\d+)\s*$", thread.name or "")
    if match:
        try:
            return int(match.group(1))
        except Exception:
            return None
    return None


async def _read_review_items(thread: discord.Thread) -> dict[str, dict[str, str]]:
    items_by_code: dict[str, dict[str, str]] = {}
    async for msg in thread.history(limit=1000, oldest_first=True):
        item = _read_item_from_message(msg)
        if item:
            items_by_code[item["code"]] = item
    return items_by_code


async def finish_session_review(interaction: discord.Interaction, *, category_code: str) -> None:
    await interaction.response.defer(ephemeral=True)
    if not _has_manage_permission(interaction):
        await safe_reply(
            interaction,
            "Missing permission (requires `manage_threads` or `manage_messages`).",
            ephemeral=True,
        )
        return

    client = interaction.client
    review_thread = interaction.channel
    if not isinstance(review_thread, discord.Thread):
        await safe_reply(interaction, "This action can only be used inside a review chat.", ephemeral=True)
        return

    session_no = _read_control_session_no(review_thread)

    try:
        category_thread = await get_category_thread(client, category_code=category_code)
    except Exception:
        category_thread = None
    if not isinstance(category_thread, discord.Thread):
        await safe_reply(interaction, "I couldn't access the category thread.", ephemeral=True)
        return

    bot_user_id = getattr(getattr(client, "user", None), "id", None)
    state = await get_session_marker_state(thread=category_thread, history_limit=5000, bot_user_id=bot_user_id)
    if not state.get("is_active"):
        await safe_reply(interaction, "There is no active session for this category anymore.", ephemeral=True)
        return

    if not session_no:
        session_no = state.get("last_session_no")
    if not session_no:
        await safe_reply(interaction, "Could not determine the session number.", ephemeral=True)
        return

    try:
        collected = await collect_session_maps(
            thread=category_thread,
            category_code=category_code,
            history_limit=5000,
            bot_user_id=bot_user_id,
        )
    except Exception:
        logger.exception("Failed to collect session maps on finish (%s)", category_code)
        await safe_reply(interaction, "Failed to scan the session thread history.", ephemeral=True)
        return

    items_by_code = await _read_review_items(review_thread)

    parts = _empty_review_parts(category_code)
    by_title = {str(p.get("title")): p for p in parts}
    seen: set[str] = set()
    for entry in collected.get("maps", []) or []:
        code = str(entry.get("mapCode") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        submitter = str(entry.get("submitter") or "Unknown")
        if entry.get("ignored"):
            by_title["Ignored"]["content"].append(
                {"code": code, "author": submitter, "comment": str(entry.get("reason") or "")}
            )
            continue
        item = items_by_code.get(code)
        if item:
            title = DECISION_VALUE_TO_TITLE.get(item["decision"], "Left as is")
            comment = item["comment"]
            submitter = item.get("submitter") or submitter
        else:
            title = "Left as is"
            comment = ""
        by_title.setdefault(title, {"title": title, "category": category_code, "content": []})
        by_title[title]["content"].append({"code": code, "author": submitter, "comment": comment})

    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    reviewer_name = get_display_name(member) if member else str(interaction.user)

    try:
        posted = await post_review_results_and_close_thread(
            bot=client,
            category_code=category_code,
            thread_id=int(category_thread.id),
            session_no=int(session_no),
            parts=parts,
            reviewer_name=reviewer_name or "Maps Reviewer",
        )
    except Exception:
        logger.exception("Failed to post review results (%s)", category_code)
        await safe_reply(interaction, "Failed to post the review results.", ephemeral=True)
        return

    last_finished_ts: Optional[int] = None
    try:
        end_msg = await category_thread.send(
            build_end_marker_message(category_code=category_code, session_no=int(session_no))
        )
        if end_msg and end_msg.created_at:
            last_finished_ts = int(end_msg.created_at.timestamp())
    except Exception:
        logger.exception("Failed to send session end marker (%s)", category_code)

    await _finalize_panel_after_review(
        interaction,
        category_code=category_code,
        category_thread=category_thread,
        session_no=int(session_no),
        last_finished_ts=last_finished_ts,
    )

    parent = review_thread.parent
    if isinstance(parent, discord.abc.Messageable):
        try:
            note = await parent.send(f"✅ {category_code} review posted: {posted.jump_url}")
            await note.delete(delay=120)
        except Exception:
            pass

    try:
        await review_thread.delete()
    except Exception:
        try:
            await review_thread.edit(archived=True, locked=True)
        except Exception:
            logger.exception("Failed to clean up review thread for %s", category_code)


async def _finalize_panel_after_review(
    interaction: discord.Interaction,
    *,
    category_code: str,
    category_thread: discord.Thread,
    session_no: int,
    last_finished_ts: Optional[int],
) -> None:
    panel_msg = await _get_panel_message(interaction, category_code)
    if not panel_msg or not panel_msg.embeds:
        return
    footer_text = getattr(panel_msg.embeds[0].footer, "text", "") if panel_msg.embeds[0].footer else ""
    if not footer_text.startswith(f"map_submission_panel:{category_code}"):
        return

    meta = parse_panel_footer(footer_text)
    last_no = int(meta.get("last") or 0)
    cleared_embed = build_submission_panel_embed(
        category_code,
        last_session_no=max(last_no, int(session_no)),
        is_locked=bool(getattr(category_thread, "locked", False)),
        last_finished_ts=last_finished_ts,
    )
    try:
        await panel_msg.edit(embeds=[cleared_embed])
    except Exception:
        logger.exception("Failed to clear current session in panel embed for %s", category_code)

    if AUTO_CREATE_NEXT_SESSION:
        try:
            await _start_new_session_for_panel(
                client=interaction.client,
                panel_msg=panel_msg,
                category_code=category_code,
                last_finished_ts=last_finished_ts,
            )
        except Exception:
            logger.exception("Failed to auto-create next session after review (%s)", category_code)


async def cancel_session_review(interaction: discord.Interaction, *, category_code: str) -> None:
    await interaction.response.defer(ephemeral=True)
    if not _has_manage_permission(interaction):
        await safe_reply(
            interaction,
            "Missing permission (requires `manage_threads` or `manage_messages`).",
            ephemeral=True,
        )
        return

    review_thread = interaction.channel
    if not isinstance(review_thread, discord.Thread):
        await safe_reply(interaction, "This action can only be used inside a review chat.", ephemeral=True)
        return

    parent = review_thread.parent
    if isinstance(parent, discord.abc.Messageable):
        try:
            note = await parent.send(f"🗑️ {category_code} review chat canceled.")
            await note.delete(delay=60)
        except Exception:
            pass

    try:
        await review_thread.delete()
    except Exception:
        try:
            await review_thread.edit(archived=True, locked=True)
        except Exception:
            logger.exception("Failed to cancel review thread for %s", category_code)
