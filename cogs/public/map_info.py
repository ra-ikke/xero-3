"""Cog that exposes the /mapinfo command."""
import logging

import discord
from discord import app_commands
from discord.ext import commands

from helpers.validation_utils import validate_map_code
from resources.category_list import CATEGORY_LIST
from service.map_service import draw_map_url, fetch_map

logger = logging.getLogger(__name__)


class MapInfo(commands.Cog):
    """Handles requests for map metadata and renders a preview image."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name='map_info',
        description='Fetches map metadata and a preview image.'
    )
    @app_commands.describe(
        mapcode='The map code (e.g., @1234567 or 1234567).',
        private='Make the response private (only visible to you).'
    )
    async def map_info(
        self,
        interaction: discord.Interaction,
        mapcode: str,
        private: bool | None = None
    ):
        """Fetches the map and posts its metadata plus preview."""
        is_private = private if private is not None else False
        await interaction.response.defer(ephemeral=is_private)

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
                content='An unexpected error occurred while accessing the map data. Please try again later.',
                ephemeral=True
            )
            return

        if not map_data:
            logger.warning('Map data missing for %s', normalized_code)
            await interaction.followup.send(
                content=f'Unable to fetch the map data for {normalized_code}. Please confirm the code or retry later.',
                ephemeral=True
            )
            return

        payload = {
            'code': normalized_code,
            'xml': map_data.xml,
            'raw': False
        }

        image_url = await draw_map_url(payload)
        if not image_url or not isinstance(image_url, str) or not image_url.startswith("http"):
            logger.warning('Mapdraw failed for %s', normalized_code)
            image_url = None

        category = next((cat for cat in CATEGORY_LIST if cat['name'] == map_data.map_type), None)
        category_emoji = category['emoji'] if category else '🗺️'
        category_name = category['name'] if category else (map_data.map_type or 'Unknown Type')
        content = (
            f'{category_emoji} (**{category_name}**) — '
            f'{map_data.maker or "Unknown Author"} - {normalized_code}'
        )

        embed = discord.Embed()
        if image_url:
            embed.set_image(url=image_url)

        await interaction.followup.send(
            content=content,
            embed=embed if image_url else None,
            ephemeral=is_private
        )


async def setup(bot: commands.Bot):
    """Registers the cog."""
    await bot.add_cog(MapInfo(bot))
