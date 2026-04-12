"""Map submission system (sessions per category, thread ingestion, setup panel)."""

from __future__ import annotations

import logging
import re
from io import BytesIO
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import PRIVATE_SERVER_IDS
from helpers.interaction_utils import safe_reply
from resources.channels import CHANNELS, SUBMISSION_CHANNELS, SUPPORTED_SUBMISSION_CATEGORIES
from helpers.submission_panel import build_submission_panel_embed, parse_panel_footer
from helpers.session_export import get_session_marker_state
from ui.map_submission_view import MapSubmissionPanelView

logger = logging.getLogger(__name__)

_guild_objects = [discord.Object(id=server_id) for server_id in PRIVATE_SERVER_IDS] if PRIVATE_SERVER_IDS else []

def _is_configured_channel_id(value: Optional[str]) -> bool:
    if not value:
        return False
    s = str(value).strip()
    return s.isdigit() and int(s) > 0


class MapSubmissions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    if _guild_objects:
        @app_commands.command(
            name="setup_submissions",
            description="Creates/updates submission panels (one per category) inside session_manager.",
        )
        @app_commands.guilds(*_guild_objects)
        async def setup_submissions_cmd(self, interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True)

            created = 0
            updated = 0
            skipped = 0
            skipped_details: list[str] = []

            bot_user = interaction.client.user
            if not bot_user:
                await safe_reply(interaction, "Bot user is not available.", ephemeral=True)
                return

            session_manager_id = CHANNELS.get("session_manager")
            if not session_manager_id or not str(session_manager_id).isdigit():
                await safe_reply(
                    interaction,
                    "`session_manager` channel is not configured in resources/channels.py.",
                    ephemeral=True,
                )
                return

            try:
                session_manager = await interaction.client.fetch_channel(int(session_manager_id))
            except Exception:
                session_manager = None

            if not isinstance(session_manager, discord.TextChannel):
                await safe_reply(
                    interaction,
                    "`session_manager` channel must be a TextChannel.",
                    ephemeral=True,
                )
                return

            # Look up existing panel messages by pinned messages first (fast),
            # and fall back to recent history if pins are unavailable.
            existing_by_category: dict[str, discord.Message] = {}
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
                if m.author.id != bot_user.id:
                    continue
                if not m.embeds:
                    continue
                embed = m.embeds[0]
                footer = getattr(embed.footer, "text", "") if embed.footer else ""
                if not footer.startswith("map_submission_panel:"):
                    continue
                meta = parse_panel_footer(footer)
                category_code = str(meta.get("category_code") or "").strip()
                if not category_code:
                    continue
                # Guard against stale/incorrect panels by checking the title
                # contains the category code as a whole token (avoid P6 vs P66).
                title = str(embed.title or "")
                pattern = rf"\\b{re.escape(category_code)}\\b"
                if not re.search(pattern, title):
                    continue
                if category_code not in existing_by_category:
                    existing_by_category[category_code] = m

            for category in SUPPORTED_SUBMISSION_CATEGORIES:
                channel_id = SUBMISSION_CHANNELS.get(category, "")
                # Panels are always created in session_manager, but we still report misconfigured
                # category forums to help debugging when "Start session" is pressed.
                if not _is_configured_channel_id(channel_id):
                    skipped_details.append(f"{category}: submission forum not configured (empty/invalid channel id)")
                else:
                    try:
                        ch = await interaction.client.fetch_channel(int(channel_id))
                        if not isinstance(ch, discord.ForumChannel):
                            skipped_details.append(
                                f"{category}: submission channel is not a ForumChannel ({type(ch).__name__})"
                            )
                    except Exception:
                        skipped_details.append(f"{category}: failed to fetch submission channel ({channel_id})")

                # Preserve panel state (last/current session) when rebuilding.
                existing = existing_by_category.get(category)
                last_no = 0
                current_thread_id = None
                current_session_no = None
                last_finished_ts = None
                if existing and existing.embeds:
                    footer_text = getattr(existing.embeds[0].footer, "text", "") if existing.embeds[0].footer else ""
                    meta = parse_panel_footer(footer_text)
                    last_no = int(meta.get("last") or 0)
                    current_thread_id = meta.get("current")  # type: ignore[assignment]
                    current_session_no = meta.get("current_no")  # type: ignore[assignment]
                    last_finished_ts = meta.get("last_end")  # type: ignore[assignment]

                show_start = last_no <= 0 and not current_thread_id
                is_locked = None
                if not isinstance(last_finished_ts, int):
                    last_finished_ts = None
                try:
                    from helpers.submission_facade import get_category_thread

                    thread = await get_category_thread(interaction.client, category_code=category)
                    if thread:
                        is_locked = bool(getattr(thread, "locked", False))
                        bot_user_id = getattr(getattr(interaction.client, "user", None), "id", None)
                        state = await get_session_marker_state(thread=thread, history_limit=2000, bot_user_id=bot_user_id)
                        last_end_message_id = state.get("last_end_message_id")
                        if last_end_message_id:
                            try:
                                end_msg = await thread.fetch_message(int(last_end_message_id))
                                if end_msg and end_msg.created_at:
                                    last_finished_ts = int(end_msg.created_at.timestamp())
                            except Exception:
                                last_finished_ts = None
                except Exception:
                    is_locked = None

                embed = build_submission_panel_embed(
                    category,
                    last_session_no=last_no,
                    current_thread_id=int(current_thread_id) if current_thread_id else None,
                    current_session_no=int(current_session_no) if current_session_no else None,
                    is_locked=is_locked,
                    last_finished_ts=last_finished_ts,
                )

                view = MapSubmissionPanelView(category, show_start=show_start, is_locked=is_locked)

                try:
                    panel_msg = existing_by_category.get(category)
                    if panel_msg:
                        await panel_msg.edit(content="", embeds=[embed], view=view)
                        updated += 1
                    else:
                        panel_msg = await session_manager.send(content="", embeds=[embed], view=view)
                        created += 1
                        # NOTE: we intentionally do not pin panels by default to avoid consuming pin slots.
                except Exception:
                    logger.exception("Failed to publish submission panel %s", category)
                    skipped += 1
                    skipped_details.append(f"{category}: failed to publish/edit panel message in session_manager")

            content = f"✅ Submission panels: {created} created, {updated} updated, {skipped} skipped."
            files: list[discord.File] = []
            if skipped_details:
                details_text = "\n".join(skipped_details) + "\n"
                files.append(
                    discord.File(BytesIO(details_text.encode("utf-8")), filename="setup_submissions_skipped.txt")
                )
            await safe_reply(interaction, content, ephemeral=True, files=files)


async def setup(bot: commands.Bot):
    await bot.add_cog(MapSubmissions(bot))

