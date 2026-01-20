"""Cog that exposes the /report_map command."""

from __future__ import annotations

import json
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from helpers.validation_utils import validate_map_code
from resources.category_list import CATEGORY_LIST
from resources.channels import CHANNELS
from resources.emoji import EMOJI_LIST
from service.map_service import draw_map_url, fetch_map
from ui.report_actions import ReportActionsViewDiscuss, ReportActionsViewHandle

logger = logging.getLogger(__name__)


REASON_CHOICES: list[app_commands.Choice[str]] = [
    app_commands.Choice(name="Off-screen cheese/hole/spawn/gameplay", value="OFF_SCREEN_CHEESE"),
    app_commands.Choice(name="Hidden hole/cheese/floor", value="HIDDEN_HOLE_CHEESE_FLOOR"),
    app_commands.Choice(name="Broken map", value="BROKEN_MAP"),
    app_commands.Choice(name="Crash map", value="CRASH_MAP"),
    app_commands.Choice(name="Instant win", value="INSTANT_WIN"),
    app_commands.Choice(name="Bad Gameplay", value="BAD_GAMEPLAY"),
    app_commands.Choice(name="Mass death", value="MASS_DEATH"),
    app_commands.Choice(name="AFK death", value="AFK_DEATH"),
    app_commands.Choice(name="Copy map", value="COPY_MAP"),
    app_commands.Choice(name="Fake/troll grounds", value="FAKE_TROLL_GROUNDS"),
    app_commands.Choice(name="Impossible", value="IMPOSSIBLE"),
    app_commands.Choice(name="Inappropriate", value="INAPPROPRIATE"),
    app_commands.Choice(name="Miscategorized", value="MISCATEGORIZED"),
    app_commands.Choice(name="Other", value="OTHER"),
]

_FRIENDLY_REASON = {c.value: c.name for c in REASON_CHOICES}


def _find_category(code: str) -> Optional[dict]:
    return next((c for c in CATEGORY_LIST if c.get("name") == code), None)


class ReportMap(commands.Cog):
    """Reports a map to the MapCrew reports channel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="report_map", description="Reports a map for MapCrew team review.")
    @app_commands.describe(
        mapcode="The map code (e.g., @1234567 or 1234567).",
        reason="Reason for the report.",
        details="Additional details about the report (optional).",
    )
    @app_commands.choices(reason=REASON_CHOICES)
    async def report_map(
        self,
        interaction: discord.Interaction,
        mapcode: str,
        reason: app_commands.Choice[str],
        details: str | None = None,
    ):
        await interaction.response.defer()

        validation = validate_map_code(mapcode, min_digits=4)
        if not validation.is_valid:
            await interaction.followup.send(
                content="❌ Please provide a valid map code (e.g., @1234567).",
                ephemeral=True,
            )
            return

        map_code = validation.formatted_code
        details_text = (details or "").strip() or "No additional details provided."

        map_data = await fetch_map(map_code)
        if not map_data:
            await interaction.followup.send(
                content=f"❌ Could not find map {map_code}. Please verify if the code is correct.",
                ephemeral=True,
            )
            return

        image_url = await draw_map_url({"code": map_code, "xml": map_data.xml, "raw": False})
        if not image_url:
            await interaction.followup.send(
                content=f"❌ Could not generate an image for map {map_code}.",
                ephemeral=True,
            )
            return

        category = _find_category(map_data.map_type or "")
        category_emoji = category["emoji"] if category else "🗺️"
        category_name = category["name"] if category else (map_data.map_type or "Unknown Type")

        reports_channel_id = CHANNELS.get("mc_reports")
        if not reports_channel_id:
            await interaction.followup.send(
                content="❌ Error: Reports channel not configured. Please contact an administrator.",
                ephemeral=True,
            )
            return

        # Acknowledge to the user (we store the link to this ack message as the "original report").
        ack_message = await interaction.followup.send(
            content=f"✨ Your report for map {map_code} has been sent successfully! The MapCrew team will review it soon.",
            ephemeral=False,
            wait=True,
        )

        reason_name = _FRIENDLY_REASON.get(reason.value, reason.value)
        report_embed = discord.Embed(
            title=f"[{category_name}] {map_code}",
            color=int("0xFF0000", 16),
            description="📝 A new map report has been submitted for review.",
        )
        report_embed.add_field(name="📋 Reason", value=reason_name, inline=False)
        report_embed.add_field(name="📝 Details", value=details_text, inline=False)
        report_embed.add_field(name="🗺️ Category", value=f"{category_emoji} {category_name}", inline=True)
        report_embed.add_field(name="👨‍💻 Map Author", value=map_data.maker or "Unknown", inline=True)
        report_embed.add_field(name="📊 Status", value="⏳ Awaiting decision", inline=True)
        report_embed.add_field(name="👤 Reported by", value=str(interaction.user), inline=True)
        report_embed.add_field(
            name="🔗 Original Report",
            value=f"[Click here to view](https://discord.com/channels/{interaction.guild_id}/{interaction.channel_id}/{ack_message.id})",
            inline=True,
        )
        report_embed.add_field(
            name="📝 Message Reference",
            value=json.dumps(
                {
                    "guildId": interaction.guild_id,
                    "channelId": interaction.channel_id,
                    "messageId": ack_message.id,
                }
            ),
            inline=False,
        )
        report_embed.set_image(url=image_url)

        valid_discuss_categories = {"P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10", "P11", "P12", "P13", "P17", "P18", "P24"}
        view = ReportActionsViewDiscuss() if (map_data.map_type in valid_discuss_categories) else ReportActionsViewHandle()

        reports_channel = await interaction.client.fetch_channel(int(reports_channel_id))
        if not isinstance(reports_channel, discord.abc.Messageable):
            await interaction.followup.send(content="❌ Reports channel is not messageable.", ephemeral=True)
            return

        await reports_channel.send(embed=report_embed, view=view)


async def setup(bot: commands.Bot):
    """Registers the cog."""
    await bot.add_cog(ReportMap(bot))

