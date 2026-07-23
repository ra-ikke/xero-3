"""Persistent report action buttons for map reports (discard / discuss / handle)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

import discord

from helpers.discussion import create_discussion
from helpers.interaction_utils import extract_report_info
from helpers.validation_utils import get_display_name, has_public_role
from resources.emoji import EMOJI_LIST

logger = logging.getLogger(__name__)


_FIELD_MESSAGE_REFERENCE = "📝 Message Reference"
_FIELD_STATUS = "📊 Status"
_FIELD_MAPCREW = "👥 MapCrew"
_FIELD_MAPCREW_NOTE = "📝 MapCrew note"


def _private_server_handler_label(user: discord.abc.User) -> str:
    """Label shown on the private reports channel.

    Public mapcrews keep their nick. Private mapcrews show
    ``Private Member (nick)`` so staff can identify who handled it without
    exposing that nick on the public reply.
    """
    member = user if isinstance(user, discord.Member) else None
    if not member:
        return str(user)
    if has_public_role(member):
        return get_display_name(member)
    nick = member.nick or getattr(member, "global_name", None) or member.name
    return f"Private Member ({nick})"


def _public_handler_label(user: discord.abc.User) -> str:
    """Label shown on the public original-report reply (never exposes private nick)."""
    member = user if isinstance(user, discord.Member) else None
    if not member:
        return "Private Member"
    return get_display_name(member)


@dataclass(frozen=True)
class MessageReference:
    guild_id: int
    channel_id: int
    message_id: int


def _parse_message_reference(embed: discord.Embed) -> Optional[MessageReference]:
    for field in embed.fields:
        if field.name == _FIELD_MESSAGE_REFERENCE:
            try:
                payload = json.loads(field.value)
                return MessageReference(
                    guild_id=int(payload["guildId"]),
                    channel_id=int(payload["channelId"]),
                    message_id=int(payload["messageId"]),
                )
            except Exception:
                return None
    return None


def _updated_report_embed(
    *,
    embed: discord.Embed,
    status_value: str,
    display_name: str,
    color_hex: str,
    optional_message: Optional[str],
) -> discord.Embed:
    # Preserve all existing fields (map details/image/etc.) and update/add MapCrew-related fields.
    updated = discord.Embed.from_dict(embed.to_dict())
    updated.clear_fields()

    has_mapcrew = False
    has_note = False
    for field in embed.fields:
        if field.name == _FIELD_STATUS:
            updated.add_field(name=field.name, value=status_value, inline=field.inline)
            continue
        if field.name == _FIELD_MAPCREW:
            has_mapcrew = True
            # Always overwrite with the handler's display name.
            updated.add_field(name=_FIELD_MAPCREW, value=display_name, inline=True)
            continue
        if field.name == _FIELD_MAPCREW_NOTE:
            has_note = True
            # Replace the existing note with the new one (if any)
            if optional_message:
                updated.add_field(name=_FIELD_MAPCREW_NOTE, value=optional_message, inline=False)
            # If there is no optional message, keep the old note text
            else:
                updated.add_field(name=_FIELD_MAPCREW_NOTE, value=field.value, inline=field.inline)
            continue
        updated.add_field(name=field.name, value=field.value, inline=field.inline)

    if not has_mapcrew:
        updated.add_field(name=_FIELD_MAPCREW, value=display_name, inline=True)

    if optional_message and not has_note:
        updated.add_field(name=_FIELD_MAPCREW_NOTE, value=optional_message, inline=False)

    updated.color = int(color_hex.replace("#", "0x"), 16)
    return updated


async def _reply_to_original_report(
    client: discord.Client,
    ref: MessageReference,
    *,
    display_name: str,
    action: str,
    color: str,
    emoji: str,
    optional_message: Optional[str],
) -> None:
    guild = await client.fetch_guild(ref.guild_id)
    channel = await guild.fetch_channel(ref.channel_id)
    if not isinstance(channel, discord.abc.Messageable):
        return
    original_message = await channel.fetch_message(ref.message_id)

    extra = f"\nReason: {optional_message}" if optional_message else ""
    embed = discord.Embed(
        description=f"{emoji} **Report handled by {display_name}**\nAction taken: {action}{extra}",
        color=int(color.replace('#', '0x'), 16),
    )
    await original_message.reply(embeds=[embed])


class _OptionalReasonModal(discord.ui.Modal):
    """Modal used by report action buttons to collect an optional reason message."""

    reason = discord.ui.TextInput(
        label="Reason (optional)",
        placeholder="This message will be sent as a reply to the original report...",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1000,
    )

    def __init__(self, *, title: str, action: str, custom_id: str):
        super().__init__(title=title, custom_id=custom_id)
        self._action = action

    @property
    def optional_message(self) -> str:
        return str(self.reason.value or "").strip()


async def _handle_report_action(
    interaction: discord.Interaction,
    *,
    action: str,
    status_text: str,
    status_color: str,
    status_emoji: str,
    create_discussion_on_discuss: bool = False,
    optional_message: Optional[str] = None,
) -> None:
    message = interaction.message
    if not message or not message.embeds:
        await interaction.followup.send(content="Could not read the report message embed.", ephemeral=True)
        return

    embed = message.embeds[0]
    ref = _parse_message_reference(embed)
    if not ref:
        await interaction.followup.send(content="Could not find a message reference in the report.", ephemeral=True)
        return

    private_label = _private_server_handler_label(interaction.user)
    public_label = _public_handler_label(interaction.user)

    updated_embed = _updated_report_embed(
        embed=embed,
        status_value=f"{status_emoji} {status_text}",
        display_name=private_label,
        color_hex=status_color,
        optional_message=optional_message,
    )

    try:
        await message.edit(embeds=[updated_embed], view=None)
    except Exception:
        logger.exception("Failed to update report message %s", message.id)

    await _reply_to_original_report(
        interaction.client,
        ref,
        display_name=public_label,
        action=action,
        color=status_color,
        emoji=status_emoji,
        optional_message=optional_message,
    )

    if create_discussion_on_discuss:
        info = extract_report_info(embed.title or "")
        if info:
            # Create a DEPERM discussion by default (legacy behavior), notify enabled.
            await create_discussion(
                client=interaction.client,
                map_code=info["mapCode"],
                category_code=info["category"],
                disc_type="DEPERM",
                notify=True,
                user=interaction.user,
                interaction=interaction,
            )


class DiscardReasonModal(_OptionalReasonModal):
    def __init__(self):
        super().__init__(title="Leave as is", action="discard", custom_id="report_actions:discard_modal")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await _handle_report_action(
            interaction,
            action="Left as is",
            status_text="Left as is",
            status_color="#808080",
            status_emoji=EMOJI_LIST.get("_parchment", ""),
            optional_message=self.optional_message or None,
        )


class HandleReasonModal(_OptionalReasonModal):
    def __init__(self):
        super().__init__(title="Handle", action="handle", custom_id="report_actions:handle_modal")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await _handle_report_action(
            interaction,
            action="Handled",
            status_text="Handled",
            status_color="#00FF00",
            status_emoji=EMOJI_LIST.get("_crane", ""),
            optional_message=self.optional_message or None,
        )


class DiscussReasonModal(_OptionalReasonModal):
    def __init__(self):
        super().__init__(title="Discuss", action="discuss", custom_id="report_actions:discuss_modal")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await _handle_report_action(
            interaction,
            action="In discussion",
            status_text="In discussion",
            status_color="#FFA500",
            status_emoji=EMOJI_LIST.get("_discuss", ""),
            create_discussion_on_discuss=True,
            optional_message=self.optional_message or None,
        )


class ReportActionsViewDiscuss(discord.ui.View):
    """Report actions view for categories that should be moved to discussion."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Leave as is",
        style=discord.ButtonStyle.danger,
        custom_id="report_actions:discard",
        emoji=EMOJI_LIST.get("_parchment"),
    )
    async def discard(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DiscardReasonModal())

    @discord.ui.button(
        label="Discuss",
        style=discord.ButtonStyle.success,
        custom_id="report_actions:discuss",
        emoji=EMOJI_LIST.get("_discuss"),
    )
    async def discuss(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DiscussReasonModal())


class ReportActionsViewHandle(discord.ui.View):
    """Report actions view for categories that should be handled without discussion."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Leave as is",
        style=discord.ButtonStyle.danger,
        custom_id="report_actions:discard",
        emoji=EMOJI_LIST.get("_parchment"),
    )
    async def discard(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DiscardReasonModal())

    @discord.ui.button(
        label="Handle",
        style=discord.ButtonStyle.primary,
        custom_id="report_actions:handle",
        emoji=EMOJI_LIST.get("_crane"),
    )
    async def handle(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(HandleReasonModal())

