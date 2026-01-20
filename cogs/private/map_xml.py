"""Cog that exposes the /mapxml command to download a map XML."""

import logging
from io import BytesIO

import discord
from discord import app_commands
from discord.ext import commands

from config import PRIVATE_SERVER_IDS
from helpers.validation_utils import validate_map_code
from service.map_service import fetch_map

logger = logging.getLogger(__name__)

_guild_objects = [discord.Object(id=server_id) for server_id in PRIVATE_SERVER_IDS] if PRIVATE_SERVER_IDS else []


class MapXml(commands.Cog):
    """Command that returns the XML from an external map provider."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    if _guild_objects:
        @app_commands.command(
            name='map_xml',
            description='Fetches the XML of a map and provides it for download.'
        )
        @app_commands.guilds(*_guild_objects)
        @app_commands.describe(mapcode='The map code (e.g., @1234567 or 1234567).')
        async def map_xml(
            self,
            interaction: discord.Interaction,
            mapcode: str
        ):
            """Fetches the XML and replies with it as a file attachment."""
            await interaction.response.defer()

            validation = validate_map_code(mapcode, min_digits=1)
            if not validation.is_valid:
                await interaction.followup.send(
                    content='Please submit a valid map code (e.g., @1234567).',
                    ephemeral=True
                )
                return
            normalized_code = validation.formatted_code

            try:
                map_data = await fetch_map(normalized_code)
            except Exception:
                logger.exception('Unexpected error while fetching map %s', normalized_code)
                await interaction.followup.send(
                    content='An unexpected error occurred while fetching the map. Please try again later.',
                    ephemeral=True
                )
                return

            if not map_data:
                logger.warning('Failed to fetch XML for map %s', normalized_code)
                await interaction.followup.send(
                    content=(
                        f'Unable to fetch the XML for map {normalized_code}. '
                        'Please confirm the code or try again later.'
                    ),
                    ephemeral=True
                )
                return

            xml_content = map_data.xml

            buffer = BytesIO(xml_content.encode('utf-8'))
            buffer.seek(0)
            file = discord.File(buffer, filename=f'{normalized_code}.xml')

            await interaction.followup.send(
                content=f'Here is the XML for map {normalized_code}:',
                files=[file]
            )


async def setup(bot: commands.Bot):
    """Registers the cog."""
    await bot.add_cog(MapXml(bot))
