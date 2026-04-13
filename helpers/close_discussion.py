"""Close discussion helper logic (ported from the legacy slash command implementation)."""

from __future__ import annotations

import json
import logging
import re
from io import BytesIO
from typing import Optional

import aiohttp
import discord

from resources.category_list import CATEGORY_LIST
from resources.channels import CHANNELS
from resources.emoji import EMOJI_LIST
from resources.get_tag import CATEGORY_TO_GROUP, get_tag_ids
from resources.status_list import STATUSES_BY_NAME
from helpers.validation_utils import has_mapcrew_role, has_trial_mapcrew_role

logger = logging.getLogger(__name__)

_USERNAME_RE = re.compile(r"by\s+([^\]]+)$")


def _find_category(code: str) -> Optional[dict]:
    return next((c for c in CATEGORY_LIST if c.get("name") == code), None)


def _category_to_group(code: str) -> Optional[str]:
    # Legacy mapping doesn't include P13, but it behaves as bootcamp for notifications.
    if code == "P13":
        return "bootcamp"
    return CATEGORY_TO_GROUP.get(code)


def _extract_author_from_title(title: str) -> Optional[str]:
    match = _USERNAME_RE.search(title or "")
    if not match:
        return None
    return match.group(1).strip() or None


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


async def _fetch_discussion_messages(
    thread: discord.Thread, *, limit: int = 50
) -> tuple[Optional[discord.Message], Optional[discord.Message]]:
    """
    Returns (disc_info_message, poll_message) using an oldest-first history scan.

    The "disc_info_message" is the bot-created starter message containing the embed
    and the text "New map discussion". We do not assume it is always the first
    message returned by history() (Discord/forum threads can be inconsistent).
    """
    messages = [m async for m in thread.history(limit=limit, oldest_first=True)]
    if not messages:
        return None, None

    # Prefer the application/bot user id from the connection state when available.
    # Fallback to None (no author filter) if not accessible.
    bot_id = None
    try:
        bot_user = getattr(thread._state, "user", None)  # type: ignore[attr-defined]
        bot_id = bot_user.id if bot_user else None
    except Exception:
        bot_id = None

    disc_info = next(
        (
            m
            for m in messages
            if (not bot_id or m.author.id == bot_id)
            and m.embeds
            and m.content
            and "New map discussion" in m.content
        ),
        None,
    )

    poll = next(
        (
            m
            for m in messages
            if m.content
            and ("Voting poll:" in m.content or "Awaiting vote options" in m.content)
        ),
        None,
    )

    return disc_info, poll


async def _collect_public_reviews(thread: discord.Thread, *, limit: int = 200) -> list[discord.Embed]:
    reviews: list[discord.Embed] = []
    try:
        async for msg in thread.history(limit=limit, oldest_first=True):
            if not msg.embeds:
                continue
            for embed in msg.embeds:
                footer_text = getattr(embed.footer, "text", "") if embed.footer else ""
                if footer_text.strip().lower() in {"public_review:p3", "public_review"}:
                    reviews.append(discord.Embed.from_dict(embed.to_dict()))
    except Exception:
        logger.exception("Failed to collect public review embeds for thread %s", thread.id)
    return reviews


async def _refetch_thread(bot: discord.Client, thread_id: int) -> Optional[discord.Thread]:
    try:
        channel = await bot.fetch_channel(int(thread_id))
    except Exception:
        return None
    return channel if isinstance(channel, discord.Thread) else None


async def _close_thread_like_fiffy(
    bot: discord.Client,
    thread: discord.Thread,
    *,
    applied_tags: Optional[list[discord.ForumTag]] = None,
    reason: str,
) -> discord.Thread:
    active_thread = thread

    if applied_tags is not None:
        try:
            await active_thread.edit(applied_tags=applied_tags, reason=reason)
        except discord.HTTPException as exc:
            if getattr(exc, "code", None) == 50083:
                await active_thread.edit(archived=False, reason=reason)
                await active_thread.edit(applied_tags=applied_tags, reason=reason)
            else:
                raise
        refreshed = await _refetch_thread(bot, active_thread.id)
        if refreshed is not None:
            active_thread = refreshed

    try:
        await active_thread.edit(
            locked=True,
            archived=True,
            reason=reason,
        )
    except discord.HTTPException as exc:
        if getattr(exc, "code", None) == 50083:
            await active_thread.edit(archived=False, reason=reason)
            await active_thread.edit(
                locked=True,
                archived=True,
                reason=reason,
            )
        else:
            raise

    refreshed = await _refetch_thread(bot, active_thread.id)
    if refreshed is not None:
        active_thread = refreshed

    if not bool(getattr(active_thread, "locked", False)) or not bool(getattr(active_thread, "archived", False)):
        await active_thread.edit(archived=False, reason=reason)
        if applied_tags is not None:
            await active_thread.edit(applied_tags=applied_tags, reason=reason)
        await active_thread.edit(
            locked=True,
            archived=True,
            reason=reason,
        )
        refreshed = await _refetch_thread(bot, active_thread.id)
        if refreshed is not None:
            active_thread = refreshed

    return active_thread


def _count_votes_from_reactions(message: discord.Message) -> dict[str, int]:
    """
    Returns {emoji_string: vote_count} excluding the bot's own initial reaction.
    """
    counts: dict[str, int] = {}
    for reaction in message.reactions:
        emoji_key = str(reaction.emoji)
        count = reaction.count - (1 if reaction.me else 0)
        if count > 0:
            counts[emoji_key] = count
    return counts


def _parse_poll_options(poll_content: str) -> list[str]:
    lines = [l for l in (poll_content or "").splitlines() if l.strip()]  # noqa: E741
    if not lines:
        return []
    if "Voting poll:" in lines[0]:
        lines = lines[1:]
    return lines


async def close_discussion_thread(
    interaction: discord.Interaction,
    *,
    notify: bool,
    option: Optional[str] = None,
    description: Optional[str] = None,
) -> None:
    """
    Closes a discussion thread, updates status/tags, archives the thread, and optionally notifies the public server.

    This function sends user-facing errors via ephemeral follow-ups.
    """
    thread = interaction.channel
    if not isinstance(thread, discord.Thread):
        await interaction.followup.send(
            content="Oops! This action can only be used within a discussion thread.",
            ephemeral=True,
        )
        return

    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if not member or not (has_mapcrew_role(member) or has_trial_mapcrew_role(member)):
        await interaction.followup.send(
            content="You need the Mapcrew or Trial Mapcrew role to close discussions.",
            ephemeral=True,
        )
        return

    disc_info, poll = await _fetch_discussion_messages(thread)
    if not disc_info or not disc_info.embeds:
        await interaction.followup.send(
            content="Could not retrieve discussion information from the thread.",
            ephemeral=True,
        )
        return

    discuss_emoji = EMOJI_LIST.get("_discuss", "")
    if not disc_info.content or "New map discussion" not in disc_info.content:
        await interaction.followup.send(
            content="This action can only be used on discussion threads started by the bot.",
            ephemeral=True,
        )
        return
    if discuss_emoji and not disc_info.content.__contains__("New map discussion"):
        await interaction.followup.send(
            content="This action can only be used on discussion threads started by the bot.",
            ephemeral=True,
        )
        return

    embed = disc_info.embeds[0]
    title = embed.title
    if not title:
        await interaction.followup.send(
            content="Could not read discussion title.",
            ephemeral=True,
        )
        return

    category_match = re.search(r"\[(P\d+)\]", title)
    if not category_match:
        await interaction.followup.send(
            content=f"Could not determine original category code from title: {title}",
            ephemeral=True,
        )
        return

    original_category_code = category_match.group(1)
    original_category = _find_category(original_category_code)
    if not original_category:
        await interaction.followup.send(
            content=f"Invalid or unknown original category code found in title: {original_category_code}",
            ephemeral=True,
        )
        return

    remainder = title.replace(category_match.group(0), "", 1)
    disc_type_match = re.search(r"\[([^\]]+)\]", remainder)
    allowed_types = {"PERM", "DEPERM", "EDIT", "MOVE"}
    disc_type = "OTHER"
    if disc_type_match:
        candidate = disc_type_match.group(1).strip().upper()
        if candidate in allowed_types:
            disc_type = candidate

    map_match = re.search(r"@\d+", title)
    map_code = map_match.group(0) if map_match else None
    if not map_code:
        await interaction.followup.send(
            content="Could not extract map code from title.",
            ephemeral=True,
        )
        return

    map_author = _extract_author_from_title(title) or "Unknown"
    map_image_url = embed.image.url if embed.image else None

    if not poll or not poll.content:
        await interaction.followup.send(
            content="Could not retrieve poll options.",
            ephemeral=True,
        )
        return

    reaction_source = poll if poll.reactions else disc_info
    vote_counts = _count_votes_from_reactions(reaction_source)

    # Allow forcing an option even if there are no votes (admin override behavior).
    # If there are no votes and no option was provided, block closing.
    if not vote_counts and not option:
        await interaction.followup.send(
            content="You can't close this thread without it being voted on (provide an Option to force-close).",
            ephemeral=True,
        )
        return

    highest = 0
    winning_emojis: list[str] = []
    for emoji_key, cnt in vote_counts.items():
        if cnt > highest:
            highest = cnt
            winning_emojis = [emoji_key]
        elif cnt == highest and cnt > 0:
            winning_emojis.append(emoji_key)

    tie = len(winning_emojis) > 1
    poll_lines = _parse_poll_options(poll.content)

    if tie and not option:
        await interaction.followup.send(
            content="The poll is tied. Please specify the closing option (e.g., 1️⃣).",
            ephemeral=True,
        )
        return

    chosen_line: Optional[str] = None

    if option:
        option = option.strip()
        chosen_line = next((l for l in poll_lines if l.startswith(option)), None)  # noqa: E741
        if not chosen_line:
            await interaction.followup.send(
                content=f"The option '{option}' was not found in the poll.",
                ephemeral=True,
            )
            return
        # If an option is provided, we allow forcing it even if it isn't the most voted.
        # (Legacy behavior was restricted to P3, but we allow it globally.)
    else:
        if not winning_emojis:
            await interaction.followup.send(
                content="No votes were cast or an error occurred determining the winner.",
                ephemeral=True,
            )
            return
        chosen_emoji = winning_emojis[0]
        chosen_line = next((l for l in poll_lines if l.startswith(chosen_emoji)), None)  # noqa: E741

    if not chosen_line:
        await interaction.followup.send(
            content="Could not determine the final chosen option text.",
            ephemeral=True,
        )
        return

    status_match = re.search(r"\[([^\]]+)\]", chosen_line)
    if not status_match:
        await interaction.followup.send(
            content=f"Could not extract status from chosen option: {chosen_line}",
            ephemeral=True,
        )
        return

    final_status = status_match.group(1).strip().upper()
    status_obj = STATUSES_BY_NAME.get(final_status)

    new_embed = embed.copy()
    new_embed.description = f"Status: [{final_status}]"
    if status_obj:
        new_embed.color = int(status_obj["color"].replace("#", "0x"), 16)
    await disc_info.edit(embeds=[new_embed])

    # Remove discussion controls (buttons) before locking/archiving.
    # Discord does not allow editing messages inside archived threads (50083).
    try:
        bot_user = interaction.client.user
        if bot_user:
            candidates: list[discord.Message] = []
            try:
                candidates.extend(await thread.pins())
            except Exception:
                # Pin fetch can fail due to missing permissions; fall back to recent history.
                pass

            if not candidates:
                candidates = [m async for m in thread.history(limit=50, oldest_first=False)]

            for message in candidates:
                if message.author.id != bot_user.id:
                    continue
                if not message.components:
                    continue
                # Match our controls view by custom_id prefix.
                has_controls = any(
                    str(getattr(component, "custom_id", "")).startswith(("close_discussion:", "discussion_controls:", "poll_controls:"))
                    for row in message.components
                    for component in getattr(row, "children", [])
                )
                if not has_controls:
                    continue
                try:
                    await message.edit(view=None)
                except discord.HTTPException as exc:
                    # 50083 = Thread is archived
                    if getattr(exc, "code", None) == 50083:
                        try:
                            await thread.edit(archived=False)
                            await message.edit(view=None)
                        except Exception:
                            raise
                    else:
                        raise
    except Exception:
        logger.exception("Failed to remove discussion controls for thread %s", thread.id)

    p1_emoji = EMOJI_LIST.get("_P1", "")
    decision_text = chosen_line.split("-", 1)[-1].strip()
    await interaction.followup.send(
        content=f"The thread has been closed with option: {decision_text}. {p1_emoji}",
        ephemeral=False,
    )

    parent = thread.parent
    applied_tags: Optional[list[discord.ForumTag]] = None
    if isinstance(parent, discord.ForumChannel):
        tag_ids = get_tag_ids(original_category, disc_type, "closed") or []
        tag_id_set = set(tag_ids)
        applied_tags = [t for t in parent.available_tags if str(t.id) in tag_id_set]
    close_reason = f"Closing map discussion {map_code} with status {final_status}"
    try:
        thread = await _close_thread_like_fiffy(
            interaction.client,
            thread,
            applied_tags=applied_tags,
            reason=close_reason,
        )
    except Exception:
        logger.exception("Failed to lock/archive thread %s", thread.id)

    changelog_payload: dict = {
        "code": map_code,
        "author": map_author,
        "disc_status": final_status,
        "notify": notify,
    }

    final_status_display = f"**{final_status}**"
    if status_obj and status_obj.get("description") and final_status != "MOVE":
        final_status_display = f'**{status_obj["description"]}**'
    # If the winning choice indicates "with edits", reflect that in the public-facing status text.
    # This mirrors the intent of the legacy UX where users cared about the "Perm with edits" nuance.
    if notify and final_status == "PERM" and "with edits" in chosen_line.lower():
        base = status_obj["description"] if status_obj and status_obj.get("description") else "Permed"
        final_status_display = f"**{base} (with edits)**"

    original_group = _category_to_group(original_category_code)
    notification_group = original_group
    notification_channel_id: Optional[str] = CHANNELS.get(original_group) if original_group else None
    notification_content = ""

    if final_status == "MOVE":
        final_status_display = "**Moved to another category**"
        move_text = chosen_line.split("Move to", 1)[-1].strip() if "Move to" in chosen_line else ""
        target_match = re.search(r"P\d+", move_text)
        target_code = target_match.group(0) if target_match else None
        target_category = _find_category(target_code) if target_code else None

        if target_category:
            notification_content = (
                f'{original_category["emoji"]} ({original_category["name"]}) → '
                f'{target_category["emoji"]} ({target_category["name"]}) — '
                f'{map_author} - {map_code} - {final_status_display}'
            )
            target_group = _category_to_group(target_category["name"])
            notification_group = target_group or notification_group
            notification_channel_id = CHANNELS.get(notification_group) if notification_group else notification_channel_id
            changelog_payload["original_category"] = original_category["name"]
            changelog_payload["target_category"] = target_category["name"]
        else:
            notification_content = (
                f'{original_category["emoji"]} (**{original_category["name"]}**) — '
                f'{map_author} - {map_code} - {final_status_display}'
            )
            changelog_payload["category"] = original_category["name"]
    else:
        effective_category = original_category
        effective_code = original_category_code
        if "P13" in chosen_line and original_category_code == "P3":
            p13 = _find_category("P13")
            if p13:
                effective_category = p13
                effective_code = "P13"

        notification_content = (
            f'{effective_category["emoji"]} (**{effective_category["name"]}**) — '
            f'{map_author} - {map_code} - {final_status_display}'
        )
        notification_group = _category_to_group(effective_code) or notification_group
        notification_channel_id = CHANNELS.get(notification_group) if notification_group else notification_channel_id
        changelog_payload["category"] = effective_category["name"]

    if description and description.strip():
        notification_content += f"\n*{description.strip()}*"

    review_embeds: list[discord.Embed] = []
    if notify:
        review_embeds = await _collect_public_reviews(thread)
        # Sanitize embeds for public posting (no author/title/footer/fields).
        sanitized: list[discord.Embed] = []
        for emb in review_embeds:
            clean = discord.Embed(description=emb.description or "")
            sanitized.append(clean)
        review_embeds = sanitized

    if notify and notification_channel_id and map_image_url:
        try:
            notify_channel = await interaction.client.fetch_channel(int(notification_channel_id))
            if isinstance(notify_channel, discord.abc.Messageable):
                if review_embeds:
                    image_file = await _download_url_as_file(map_image_url, f"{map_code}.png")
                    files = [image_file] if image_file else []
                    await notify_channel.send(content=notification_content, files=files, embeds=review_embeds)
                else:
                    image_file = await _download_url_as_file(map_image_url, f"{map_code}.png")
                    files = [image_file] if image_file else []
                    await notify_channel.send(content=notification_content, files=files)
        except Exception:
            logger.exception("Failed to send notification for %s", map_code)

    changelog_id = CHANNELS.get("mc_changelog")
    if changelog_id:
        try:
            changelog_channel = await interaction.client.fetch_channel(int(changelog_id))
            if isinstance(changelog_channel, discord.abc.Messageable):
                await changelog_channel.send(content=json.dumps(changelog_payload))
        except Exception:
            logger.exception("Failed to send changelog payload for %s", map_code)

