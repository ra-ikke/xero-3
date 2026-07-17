"""Persistent UI for the in-Discord session review flow.

Views are registered per submission category at startup (see bot.py), which lets
us embed the category code in the stable custom_ids while keeping them routable
after restarts.
"""

from __future__ import annotations

import logging

import discord

from helpers.session_review import (
    DECISION_LABELS,
    available_decision_values,
    cancel_session_review,
    finish_session_review,
    set_item_comment,
    set_item_decision,
)

logger = logging.getLogger(__name__)


class SessionReviewCommentModal(discord.ui.Modal):
    """Collects/edits the public comment for a single map."""

    def __init__(self, *, category_code: str, current_comment: str = ""):
        super().__init__(title="Edit map comment")
        self.category_code = category_code
        self.comment = discord.ui.TextInput(
            label="Comment",
            placeholder="Optional public comment shown on close. Leave empty to clear.",
            required=False,
            max_length=1500,
            style=discord.TextStyle.paragraph,
            default=(current_comment or "")[:1500],
        )
        self.add_item(self.comment)

    async def on_submit(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await set_item_comment(
            interaction,
            category_code=self.category_code,
            comment=str(self.comment.value or "").strip(),
        )


class _DecisionSelect(discord.ui.Select):
    def __init__(self, category_code: str, *, decision_value: str | None = None):
        self.category_code = category_code
        options = [
            discord.SelectOption(
                label=DECISION_LABELS.get(value, value),
                value=value,
                default=(value == decision_value),
            )
            for value in available_decision_values(category_code)
        ]
        super().__init__(
            placeholder="Set decision",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"session_review:{category_code}:decision",
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await set_item_decision(
            interaction,
            category_code=self.category_code,
            decision_value=self.values[0],
        )


class _EditCommentButton(discord.ui.Button):
    def __init__(self, category_code: str):
        super().__init__(
            label="Edit comment",
            style=discord.ButtonStyle.secondary,
            custom_id=f"session_review:{category_code}:comment",
        )
        self.category_code = category_code

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        current_comment = ""
        message = interaction.message
        if message and message.embeds:
            description = str(message.embeds[0].description or "").strip()
            if description and description != "*No comment yet.*":
                current_comment = description
        await interaction.response.send_modal(
            SessionReviewCommentModal(category_code=self.category_code, current_comment=current_comment)
        )


class SessionReviewItemView(discord.ui.View):
    """Per-map controls: decision select + comment button."""

    def __init__(self, category_code: str, *, decision_value: str | None = None):
        super().__init__(timeout=None)
        self.category_code = category_code
        self.add_item(_DecisionSelect(category_code, decision_value=decision_value))
        self.add_item(_EditCommentButton(category_code))


class _FinishReviewButton(discord.ui.Button):
    def __init__(self, category_code: str):
        super().__init__(
            label="Finish & post review",
            style=discord.ButtonStyle.success,
            custom_id=f"session_review:{category_code}:finish",
        )
        self.category_code = category_code

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await finish_session_review(interaction, category_code=self.category_code)


class _CancelReviewButton(discord.ui.Button):
    def __init__(self, category_code: str):
        super().__init__(
            label="Cancel",
            style=discord.ButtonStyle.danger,
            custom_id=f"session_review:{category_code}:cancel",
        )
        self.category_code = category_code

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await cancel_session_review(interaction, category_code=self.category_code)


class SessionReviewControlView(discord.ui.View):
    """Header controls for a review chat: finish / cancel."""

    def __init__(self, category_code: str):
        super().__init__(timeout=None)
        self.category_code = category_code
        self.add_item(_FinishReviewButton(category_code))
        self.add_item(_CancelReviewButton(category_code))
