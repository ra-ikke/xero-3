"""Persistent UI controls for discussion threads (close + update actions)."""

from __future__ import annotations

import logging
import re

import discord

from helpers.discussion_facade import (
    add_poll_option,
    close_discussion,
    refresh_info,
    update_category,
    update_map_code,
)
from resources.category_list import CATEGORY_LIST
from resources.get_tag import CATEGORY_TO_GROUP
from helpers.validation_utils import has_mapcrew_role, has_trial_mapcrew_role

logger = logging.getLogger(__name__)


def _build_public_review_embed(*, review_text: str, author_name: str) -> discord.Embed:
    embed = discord.Embed(
        title="Public review",
        description=review_text.strip(),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Author", value=author_name, inline=True)
    embed.set_footer(text="public_review")
    return embed


def _extract_public_review_text(message: discord.Message) -> str:
    if not message.embeds:
        return ""
    return str(message.embeds[0].description or "").strip()


def _can_manage_public_review(interaction: discord.Interaction) -> bool:
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    return bool(member and (has_mapcrew_role(member) or has_trial_mapcrew_role(member)))

class CloseModalBase(discord.ui.Modal):
    """Base modal that collects the closing option and optional description."""

    option = discord.ui.TextInput(
        label="Option",
        placeholder="Force a closing option when tied (e.g., 1️⃣). Leave empty to auto-pick.",
        required=False,
        max_length=32,
    )

    def __init__(self, *, title: str, notify: bool):
        super().__init__(title=title)
        self._notify = notify

    async def on_submit(self, interaction: discord.Interaction) -> None:
        option_value = str(self.option.value).strip() if self.option.value else None
        await interaction.response.defer(ephemeral=True)
        await close_discussion(interaction, notify=self._notify, option=option_value or None, description=None)


class CloseWithNotificationModal(CloseModalBase):
    """Modal used when closing with public notification."""

    description = discord.ui.TextInput(
        label="Description",
        placeholder="Optional description of the decision made in the discussion.",
        required=False,
        max_length=400,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self):
        super().__init__(title="Close discussion (with notification)", notify=True)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        option_value = str(self.option.value).strip() if self.option.value else None
        description_value = str(self.description.value).strip() if self.description.value else None
        await interaction.response.defer(ephemeral=True)
        await close_discussion(
            interaction,
            notify=True,
            option=option_value or None,
            description=description_value or None,
        )


class CloseWithoutNotificationModal(CloseModalBase):
    """Modal used when closing without public notification (option only)."""

    def __init__(self):
        super().__init__(title="Close discussion", notify=False)


class AddPublicReviewModal(discord.ui.Modal):
    """Modal used to collect a public review for discussion threads."""

    review = discord.ui.TextInput(
        label="Public review",
        placeholder="Write the public review to be shared on close.",
        required=True,
        max_length=1500,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self):
        super().__init__(title="Add public review")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.followup.send(
                content="This action can only be used inside a discussion thread.",
                ephemeral=True,
            )
            return

        author_name = interaction.user.display_name if isinstance(interaction.user, discord.Member) else str(interaction.user)
        embed = _build_public_review_embed(
            review_text=str(self.review.value),
            author_name=author_name,
        )

        try:
            await thread.send(embed=embed, view=PublicReviewActionsView())
        except Exception:
            logger.exception("Failed to post public review embed in thread %s", thread.id)
            await interaction.followup.send("Failed to post the public review.", ephemeral=True)
            return

        await interaction.followup.send("Public review added.", ephemeral=True)


class EditPublicReviewModal(discord.ui.Modal):
    """Modal used to rewrite an existing public review message."""

    def __init__(self, *, current_text: str):
        super().__init__(title="Edit public review")
        self.review = discord.ui.TextInput(
            label="Public review",
            placeholder="Rewrite the public review to be shared on close.",
            required=True,
            max_length=1500,
            style=discord.TextStyle.paragraph,
            default=current_text[:1500],
        )
        self.add_item(self.review)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if not _can_manage_public_review(interaction):
            await interaction.followup.send(
                content="You need the Mapcrew or Trial Mapcrew role to edit public reviews.",
                ephemeral=True,
            )
            return
        message = interaction.message
        if not message:
            await interaction.followup.send("Could not locate the public review message.", ephemeral=True)
            return
        author_name = interaction.user.display_name if isinstance(interaction.user, discord.Member) else str(interaction.user)
        embed = _build_public_review_embed(
            review_text=str(self.review.value),
            author_name=author_name,
        )
        try:
            await message.edit(embed=embed, view=PublicReviewActionsView())
        except Exception:
            logger.exception("Failed to edit public review message %s", getattr(message, "id", None))
            await interaction.followup.send("Failed to edit the public review.", ephemeral=True)
            return
        await interaction.followup.send("Public review updated.", ephemeral=True)


class PublicReviewActionsView(discord.ui.View):
    """Persistent actions attached to public review messages."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Edit",
        style=discord.ButtonStyle.secondary,
        custom_id="public_review:edit",
    )
    async def edit_review(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _can_manage_public_review(interaction):
            await interaction.response.send_message(
                content="You need the Mapcrew or Trial Mapcrew role to edit public reviews.",
                ephemeral=True,
            )
            return
        message = interaction.message
        if not message:
            await interaction.response.send_message(
                content="Could not locate the public review message.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            EditPublicReviewModal(current_text=_extract_public_review_text(message))
        )

    @discord.ui.button(
        label="Delete",
        style=discord.ButtonStyle.danger,
        custom_id="public_review:delete",
    )
    async def delete_review(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _can_manage_public_review(interaction):
            await interaction.response.send_message(
                content="You need the Mapcrew or Trial Mapcrew role to delete public reviews.",
                ephemeral=True,
            )
            return
        message = interaction.message
        if not message:
            await interaction.response.send_message(
                content="Could not locate the public review message.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await message.delete()
        except Exception:
            logger.exception("Failed to delete public review message %s", getattr(message, "id", None))
            await interaction.followup.send("Failed to delete the public review.", ephemeral=True)
            return
        await interaction.followup.send("Public review deleted.", ephemeral=True)


class CloseDiscussionView(discord.ui.View):
    """
    Persistent view attached to a controls message inside the discussion thread.

    It provides:
    - a Close button (no notification) -> modal with Option only
    - a Close with notification button -> modal with Option + Description
    - update actions (category, information refresh, map code)
    """

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close",
        style=discord.ButtonStyle.danger,
        custom_id="close_discussion:close",
    )
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CloseWithoutNotificationModal())

    @discord.ui.button(
        label="Close with notification",
        style=discord.ButtonStyle.danger,
        custom_id="close_discussion:close_notify",
    )
    async def close_with_notification_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CloseWithNotificationModal())

    @discord.ui.button(
        label="Refresh information",
        style=discord.ButtonStyle.secondary,
        custom_id="discussion_controls:refresh_info",
    )
    async def refresh_information(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await refresh_info(interaction)
        try:
            if interaction.message:
                await interaction.message.edit(view=CloseDiscussionView())
        except Exception:
            logger.exception(
                "Failed to refresh discussion controls view for %s",
                getattr(interaction.channel, "id", None),
            )

    @discord.ui.button(
        label="Add public review",
        style=discord.ButtonStyle.secondary,
        custom_id="discussion_controls:add_public_review",
    )
    async def add_public_review(self, interaction: discord.Interaction, button: discord.ui.Button):
        thread = interaction.channel
        thread_name = thread.name if isinstance(thread, discord.Thread) else ""
        if not thread_name:
            await interaction.response.send_message(
                content="Could not determine the discussion category.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(AddPublicReviewModal())

    @discord.ui.button(
        label="Update map code",
        style=discord.ButtonStyle.secondary,
        custom_id="discussion_controls:update_mapcode",
    )
    async def update_map_code(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(UpdateMapCodeModal())

    @discord.ui.button(
        label="Update category",
        style=discord.ButtonStyle.secondary,
        custom_id="discussion_controls:update_category",
    )
    async def update_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            content="Select the new category code for this discussion:",
            view=UpdateCategorySelectView(),
            ephemeral=True,
        )

    @discord.ui.button(
        label="Add poll option",
        style=discord.ButtonStyle.primary,
        custom_id="discussion_controls:add_poll_option",
    )
    async def add_poll_option(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            content="Choose the option type to add:",
            view=AddPollOptionView(),
            ephemeral=True,
        )


class UpdateMapCodeModal(discord.ui.Modal):
    """Modal that collects the new map code to apply to the discussion."""

    new_map_code = discord.ui.TextInput(
        label="New map code",
        placeholder="Example: @12345",
        required=True,
        max_length=32,
    )

    def __init__(self):
        super().__init__(title="Update map code")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await update_map_code(interaction, new_map_code=str(self.new_map_code.value))


class UpdateCategorySelectView(discord.ui.View):
    """Ephemeral view used to choose the new category code."""

    def __init__(self):
        super().__init__(timeout=120)

        options: list[discord.SelectOption] = []
        # Limit to categories that have discussion groups (fits in a single select).
        for code in sorted(CATEGORY_TO_GROUP.keys()):
            options.append(discord.SelectOption(label=code, value=code))

        self.select = discord.ui.Select(
            placeholder="Select category (P-code)",
            min_values=1,
            max_values=1,
            options=options[:25],
        )
        self.select.callback = self._on_select  # type: ignore
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        code = self.select.values[0]
        await update_category(interaction, new_category_code=code)


class AddPollDescriptionModal(discord.ui.Modal):
    """Modal used to collect a description for a new poll option."""

    description = discord.ui.TextInput(
        label="Description",
        placeholder="Example: Keep with edits",
        required=True,
        max_length=120,
    )

    def __init__(self, *, option_type: str):
        super().__init__(title="Add poll option")
        self._option_type = option_type

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await add_poll_option(
            interaction,
            option_type=self._option_type,
            description=str(self.description.value),
        )


class AddPollMoveCategoryView(discord.ui.View):
    """Ephemeral view used to pick the target category for MOVE options."""

    def __init__(self):
        super().__init__(timeout=180)

        options: list[discord.SelectOption] = []
        for code in sorted(CATEGORY_TO_GROUP.keys()):
            cat = next((c for c in CATEGORY_LIST if c.get("name") == code), None)
            label = cat.get("description", code) if cat else code
            options.append(discord.SelectOption(label=label, value=code))

        self.select = discord.ui.Select(
            placeholder="Select target category",
            min_values=1,
            max_values=1,
            options=options[:25],
        )
        self.select.callback = self._on_select  # type: ignore
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        code = self.select.values[0]
        await add_poll_option(interaction, option_type="MOVE", target_category_code=code)


class AddPollOptionView(discord.ui.View):
    """Ephemeral view used to choose the option type to add to the poll."""

    def __init__(self):
        super().__init__(timeout=180)

        self.select = discord.ui.Select(
            placeholder="Select option type",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Perm map", value="PERM"),
                discord.SelectOption(label="Edit map", value="EDIT"),
                discord.SelectOption(label="Deperm map", value="DEPERM"),
                discord.SelectOption(label="Keep as is", value="KEEP"),
                discord.SelectOption(label="Move map to another category", value="MOVE"),
                discord.SelectOption(label="Reject map", value="REJECT"),
            ],
        )
        self.select.callback = self._on_select  # type: ignore
        self.add_item(self.select)

    async def _on_select(self, interaction: discord.Interaction):
        option_type = self.select.values[0]
        if option_type == "MOVE":
            await interaction.response.send_message(
                content="Select the target category:",
                view=AddPollMoveCategoryView(),
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(AddPollDescriptionModal(option_type=option_type))

