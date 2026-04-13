"""Private command to reopen an existing closed discussion thread."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config import PRIVATE_SERVER_IDS
from helpers.reopen_discussion import reopen_discussion_thread

_guild_objects = [discord.Object(id=server_id) for server_id in PRIVATE_SERVER_IDS] if PRIVATE_SERVER_IDS else []


class ReopenDiscussion(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    if _guild_objects:
        @app_commands.command(
            name="reopen_discussion",
            description="Reopens the current closed discussion thread and restores controls.",
        )
        @app_commands.guilds(*_guild_objects)
        async def reopen_discussion_cmd(self, interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True)
            await reopen_discussion_thread(interaction)


async def setup(bot: commands.Bot):
    await bot.add_cog(ReopenDiscussion(bot))
