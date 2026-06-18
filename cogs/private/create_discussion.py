"""Cog that exposes the /create_discussion command."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import PRIVATE_SERVER_IDS
from helpers.discussion import create_discussion
from helpers.validation_utils import validate_map_code
from resources.category_list import CATEGORY_LIST
from resources.get_tag import (
    CATEGORY_TO_GROUP,
    RACING_DISCUSSION_CATEGORY_CODE,
    RACING_DISCUSSION_CODES,
    resolve_discussion_category_code,
)

logger = logging.getLogger(__name__)

_guild_objects = [discord.Object(id=server_id) for server_id in PRIVATE_SERVER_IDS] if PRIVATE_SERVER_IDS else []


def _category_choices() -> list[app_commands.Choice[str]]:
    choices: list[app_commands.Choice[str]] = []
    racing_added = False
    for cat in CATEGORY_LIST:
        code = cat.get("name")
        if not code or code not in CATEGORY_TO_GROUP:
            continue
        if code in RACING_DISCUSSION_CODES:
            if not racing_added:
                p17 = next((c for c in CATEGORY_LIST if c.get("name") == RACING_DISCUSSION_CATEGORY_CODE), None)
                label = (p17 or {}).get("description", "Racing (P17)")
                choices.append(app_commands.Choice(name=label, value=RACING_DISCUSSION_CATEGORY_CODE))
                racing_added = True
            continue
        choices.append(app_commands.Choice(name=cat.get("description", code), value=code))
    return choices


_CATEGORY_CHOICES = _category_choices()


class CreateDiscussion(commands.Cog):
    """Creates a new forum thread discussion for a given map."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    if _guild_objects:
        @app_commands.command(
            name="create_discussion",
            description="Creates a new discussion thread for the map."
        )
        @app_commands.guilds(*_guild_objects)
        @app_commands.describe(
            code="Map code. Example: @12345",
            category="In which category is the map currently being discussed?",
            disc_type="What type of discussion would you like to create for this map?",
            notify="Notifies the public server about the new discussion (PERM only).",
            disc_description="Only required when disc_type is OTHER (e.g., Move from P6 to P7).",
        )
        @app_commands.choices(category=_CATEGORY_CHOICES)
        @app_commands.choices(
            disc_type=[
                app_commands.Choice(name="Perm map", value="PERM"),
                app_commands.Choice(name="Edit map", value="EDIT"),
                app_commands.Choice(name="Deperm map", value="DEPERM"),
                app_commands.Choice(name="Other", value="OTHER"),
            ]
        )
        async def create_discussion_cmd(
            self,
            interaction: discord.Interaction,
            code: str,
            category: app_commands.Choice[str],
            disc_type: app_commands.Choice[str],
            notify: bool,
            disc_description: str | None = None,
        ):
            await interaction.response.defer()

            # Channel gate (legacy behavior)
            channel = interaction.channel
            if not channel or getattr(channel, "name", None) != "discussions":
                await interaction.followup.send(
                    content="Oops! This command can't be used in this channel.",
                    ephemeral=True,
                )
                return

            map_code = code.strip()
            validation = validate_map_code(map_code, min_digits=4)
            if not validation.is_valid:
                await interaction.followup.send(
                    content="Please provide a valid map code (e.g., @12345).",
                    ephemeral=True,
                )
                return
            map_code = validation.formatted_code

            category_code = resolve_discussion_category_code(category.value)
            if not category_code:
                await interaction.followup.send(
                    content="Invalid category selected.",
                    ephemeral=True,
                )
                return

            # Disc description logic (legacy behavior)
            if disc_type.value == "OTHER":
                if not disc_description or not disc_description.strip():
                    await interaction.followup.send(
                        content="Please provide a description for the 'OTHER' discussion type.",
                        ephemeral=True,
                    )
                    return
                disc_desc = disc_description.strip()
            else:
                disc_desc = disc_type.value

            result = await create_discussion(
                client=interaction.client,
                map_code=map_code,
                category_code=category_code,
                disc_type=disc_desc,
                notify=notify,
                user=interaction.user,
                interaction=interaction,
            )

            if not result.get("success"):
                # Errors are handled inside create_discussion via followup.
                return

            thread = result["thread"]
            await interaction.followup.send(
                content=f'New discussion thread created for {result["map_data"]["code"]} in channel <#{thread.id}>.',
                ephemeral=False,
            )


async def setup(bot: commands.Bot):
    """Registers the cog."""
    await bot.add_cog(CreateDiscussion(bot))
