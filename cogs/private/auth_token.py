"""Private command to create auth tokens for external apps."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import PRIVATE_SERVER_IDS
from helpers.auth_token import build_auth_record, generate_auth_token, resolve_auth_channel, serialize_auth_record

logger = logging.getLogger(__name__)

_guild_objects = [discord.Object(id=server_id) for server_id in PRIVATE_SERVER_IDS] if PRIVATE_SERVER_IDS else []


class AuthToken(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    if _guild_objects:
        @app_commands.command(
            name="create_auth_token",
            description="Creates an auth token for external apps.",
        )
        @app_commands.guilds(*_guild_objects)
        async def create_auth_token(self, interaction: discord.Interaction) -> None:
            await interaction.response.defer(ephemeral=True)

            channel, err = await resolve_auth_channel(interaction.client)
            if not channel:
                await interaction.followup.send(
                    f"MC_AUTH channel is not configured or not accessible.\nReason: {err or 'unknown'}",
                    ephemeral=True,
                )
                return

            token = generate_auth_token()
            record = build_auth_record(token=token, user=interaction.user, guild=interaction.guild)
            payload = serialize_auth_record(record)

            try:
                await channel.send(payload)
            except Exception:
                logger.exception("Failed to store auth token in MC_AUTH")
                await interaction.followup.send("Failed to store auth token in MC_AUTH.", ephemeral=True)
                return

            await interaction.followup.send(
                f"Auth token created for **{interaction.user}**:\n`{token}`",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(AuthToken(bot))
