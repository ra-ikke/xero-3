"""Private commands to announce manual map decisions without discussion threads."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config import PRIVATE_SERVER_IDS
from helpers.map_announcement import announce_map_move, announce_map_status
from resources.category_list import CATEGORY_LIST
from resources.get_tag import CATEGORY_TO_GROUP
from resources.status_list import STATUS_LIST

_guild_objects = [discord.Object(id=server_id) for server_id in PRIVATE_SERVER_IDS] if PRIVATE_SERVER_IDS else []


def _category_choices() -> list[app_commands.Choice[str]]:
    choices: list[app_commands.Choice[str]] = []
    for cat in CATEGORY_LIST:
        code = cat.get("name")
        if not code or code not in CATEGORY_TO_GROUP:
            continue
        choices.append(app_commands.Choice(name=cat.get("description", code), value=code))
    return choices


def _decision_choices() -> list[app_commands.Choice[str]]:
    choices: list[app_commands.Choice[str]] = []
    for status in STATUS_LIST:
        name = str(status.get("name") or "").strip().upper()
        if not name or name in {"MOVE", "IN DISCUSSION"}:
            continue
        label = str(status.get("description") or name).strip()
        choices.append(app_commands.Choice(name=label, value=name))
    return choices


_CATEGORY_CHOICES = _category_choices()
_DECISION_CHOICES = _decision_choices()


class AnnounceMap(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    if _guild_objects:
        @app_commands.command(
            name="announce_map",
            description="Posts a public map announcement without creating a discussion thread.",
        )
        @app_commands.guilds(*_guild_objects)
        @app_commands.describe(
            map_code="Map code. Example: @12345",
            category="Public category where the announcement should be posted",
            decision="Final decision to announce publicly",
        )
        @app_commands.choices(category=_CATEGORY_CHOICES, decision=_DECISION_CHOICES)
        async def announce_map_cmd(
            self,
            interaction: discord.Interaction,
            map_code: str,
            category: app_commands.Choice[str],
            decision: app_commands.Choice[str],
        ) -> None:
            await interaction.response.defer(ephemeral=True)
            try:
                result = await announce_map_status(
                    interaction.client,
                    map_code=map_code,
                    category_code=category.value,
                    decision=decision.value,
                )
            except Exception as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return

            target = f"<#{int(result['channel_id'])}>"
            await interaction.followup.send(
                f"Announcement posted for {result['code']} in {target}.",
                ephemeral=True,
            )

        @app_commands.command(
            name="announce_map_move",
            description="Posts a public move announcement without creating a discussion thread.",
        )
        @app_commands.guilds(*_guild_objects)
        @app_commands.describe(
            map_code="Map code. Example: @12345",
            source_category="Original category of the map",
            target_category="Destination category of the map",
        )
        @app_commands.choices(source_category=_CATEGORY_CHOICES, target_category=_CATEGORY_CHOICES)
        async def announce_map_move_cmd(
            self,
            interaction: discord.Interaction,
            map_code: str,
            source_category: app_commands.Choice[str],
            target_category: app_commands.Choice[str],
        ) -> None:
            await interaction.response.defer(ephemeral=True)
            try:
                result = await announce_map_move(
                    interaction.client,
                    map_code=map_code,
                    source_category_code=source_category.value,
                    target_category_code=target_category.value,
                )
            except Exception as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return

            target = f"<#{int(result['channel_id'])}>"
            await interaction.followup.send(
                f"Move announcement posted for {result['code']} in {target}.",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(AnnounceMap(bot))
