"""Discussion helpers (ported from the legacy JavaScript implementation)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Optional

import aiohttp
import discord

from helpers.validation_utils import validate_map_code
from resources.category_list import CATEGORY_LIST
from resources.channels import CHANNELS
from resources.emoji import EMOJI_LIST
from resources.get_tag import CATEGORY_TO_GROUP, get_tag_ids
from resources.poll_list import get_poll_options
from resources.status_list import STATUSES_BY_NAME
from service.map_service import draw_map_url, fetch_map

logger = logging.getLogger(__name__)


CATEGORY_MAPPING: dict[str, str] = dict(CATEGORY_TO_GROUP)

CHANNEL_LIST_MAPPING: dict[str, str] = {
    "rotation": CHANNELS["mc_rotation"],
    "survivor": CHANNELS["mc_survivor"],
    "racing": CHANNELS["mc_racing"],
    "defilante": CHANNELS["mc_defilante"],
    "bootcamp": CHANNELS["mc_bootcamp"],
}


async def _download_url_as_file(url: str, filename: str) -> Optional[discord.File]:
    if not url or not url.startswith("http"):
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


def _hex_to_int_color(hex_color: str) -> int:
    return int(hex_color.replace("#", "0x"), 16)


def _find_category(code: str) -> Optional[dict[str, Any]]:
    return next((c for c in CATEGORY_LIST if c.get("name") == code), None)


def _build_discussion_embed(
    *,
    user: discord.abc.User,
    map_code: str,
    map_author: str,
    map_image_url: str,
    category: dict[str, Any],
    disc_description: str,
    disc_status: str,
) -> discord.Embed:
    status = STATUSES_BY_NAME.get(disc_status, STATUSES_BY_NAME.get("IN DISCUSSION"))
    color = _hex_to_int_color(status["color"]) if status else discord.Color.blue().value

    embed = discord.Embed(
        title=f'[{category["name"]}] [{disc_description}] {map_code} by {map_author}',
        description=f"Status: [{disc_status}]",
        color=color,
        timestamp=datetime.now(timezone.utc),
    )

    display_name = getattr(user, "global_name", None) or getattr(user, "display_name", None) or str(user)
    embed.set_author(name=display_name, icon_url=user.display_avatar.url)
    embed.set_thumbnail(url=category.get("picture"))
    embed.set_image(url=map_image_url)
    return embed


async def create_discussion(
    *,
    client: discord.Client,
    map_code: str,
    category_code: str,
    disc_type: str,
    notify: bool,
    user: discord.abc.User,
    interaction: Optional[discord.Interaction] = None,
    disc_status: str = "IN DISCUSSION",
) -> dict[str, Any]:
    """
    Creates a new map discussion thread in the internal forum channels.

    Returns a result dict:
    - success: bool
    - thread: discord.Thread | None
    - map_data: {code, author, image_url} | None
    - error: str | None
    """
    try:
        validation = validate_map_code(map_code, min_digits=4)
        code = validation.formatted_code
        if not validation.is_valid:
            raise ValueError("Please provide a valid map code (e.g., @12345).")

        map_data = await fetch_map(code)
        if not map_data:
            raise ValueError("Could not fetch map data. Please verify if the code is correct.")

        payload = {"code": code, "xml": map_data.xml, "raw": False}
        map_image_url = await draw_map_url(payload)
        if not map_image_url:
            raise ValueError("Could not generate an image for the map.")

        cat = _find_category(category_code)
        if not cat:
            raise ValueError("Invalid category selected.")

        # Tags + internal forum channel mapping
        group = CATEGORY_MAPPING.get(category_code, "")
        forum_channel_id = CHANNEL_LIST_MAPPING.get(group)
        if not forum_channel_id:
            raise ValueError("Could not determine the discussion channel for this category.")

        forum_channel = await client.fetch_channel(int(forum_channel_id))
        if not isinstance(forum_channel, discord.ForumChannel):
            raise ValueError("The configured discussion channel is not a forum channel.")

        tag_ids = get_tag_ids(cat, disc_type, "open") or []
        applied_tags = [t for t in forum_channel.available_tags if str(t.id) in set(tag_ids)]

        embed = _build_discussion_embed(
            user=user,
            map_code=code,
            map_author=map_data.maker or "Unknown Author",
            map_image_url=map_image_url,
            category=cat,
            disc_description=disc_type,
            disc_status=disc_status,
        )

        thread_name = f'[{cat["name"]}] [{disc_type}] {code} by {map_data.maker}'
        discuss_emoji = EMOJI_LIST.get("_discuss", "")

        created = await forum_channel.create_thread(
            name=thread_name,
            content=f"{discuss_emoji} New map discussion",
            embeds=[embed],
            applied_tags=applied_tags,
        )

        # discord.py may return either:
        # - a Thread
        # - a (Thread, Message) tuple
        # - a ThreadWithMessage-like object with .thread and .message
        if isinstance(created, tuple) and len(created) == 2:
            thread = created[0]
        elif hasattr(created, "thread") and hasattr(created, "message"):
            thread = created.thread
        else:
            thread = created

        # Poll message in thread
        poll_emoji = EMOJI_LIST.get("_poll", "")
        emoji_list = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        poll_options = get_poll_options(cat["name"], disc_type)

        if poll_options:
            poll_text = f"{poll_emoji} **Voting poll:**"
            for i, opt in enumerate(poll_options[: len(emoji_list)]):
                poll_text += f'\n{emoji_list[i]} - [{opt["type"]}] {opt["option"]}'

            poll_message = await thread.send(poll_text)
            for i in range(min(len(poll_options), len(emoji_list))):
                try:
                    await poll_message.add_reaction(emoji_list[i])
                except Exception:
                    logger.exception("Failed to add reaction %s on poll message %s", emoji_list[i], poll_message.id)
        else:
            await thread.send(
                f"{poll_emoji} Awaiting vote options.\nUse `/add_discussion_option` to add a new option."
            )

        # Post close controls (persistent UI)
        try:
            from ui.close_discussion_view import CloseDiscussionView

            controls_message = await thread.send(
                content="Discussion controls: use **Close** or **Close with notification** to finish the thread.",
                view=CloseDiscussionView(),
            )
            try:
                await controls_message.pin()
            except Exception:
                # Pinning is optional and may fail without permissions.
                pass
        except Exception:
            logger.exception("Failed to post close discussion controls for thread %s", thread.id)

        # Changelog
        changelog_channel_id = CHANNELS.get("mc_changelog")
        if changelog_channel_id:
            changelog = await client.fetch_channel(int(changelog_channel_id))
            if isinstance(changelog, discord.abc.Messageable):
                await changelog.send(
                    content=json.dumps(
                        {
                            "code": code,
                            "author": map_data.maker,
                            "category": cat.get("name"),
                            "disc_status": disc_status,
                            "notify": notify,
                        }
                    )
                )

        # Public notification (PERM only)
        if notify and disc_type == "PERM":
            public_channel_id = CHANNELS.get(group)
            if public_channel_id:
                public_channel = await client.fetch_channel(int(public_channel_id))
                if isinstance(public_channel, discord.abc.Messageable):
                    image_file = await _download_url_as_file(map_image_url, f"{code}.png")
                    files = [image_file] if image_file else []
                    await public_channel.send(
                        content=f'{cat["emoji"]} (**{cat["name"]}**) — {map_data.maker} - {code} - **In Discussion**\n',
                        files=files,
                    )

        return {
            "success": True,
            "thread": thread,
            "map_data": {"code": code, "author": map_data.maker, "image_url": map_image_url},
            "error": None,
        }
    except Exception as exc:
        logger.exception("Error in create_discussion")
        if interaction:
            message = str(exc) or "An unexpected error occurred."
            try:
                await interaction.followup.send(content=message, ephemeral=True)
            except Exception:
                # If followup fails, there is nothing else we can safely do here.
                pass
        return {"success": False, "thread": None, "map_data": None, "error": str(exc)}

