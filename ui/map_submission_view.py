"""Persistent UI panel for map submissions per category."""

from __future__ import annotations

import logging

import discord

from helpers.interaction_utils import safe_reply
from helpers.submission_facade import (
    edit_last_session_review,
    download_session_export,
    get_category_thread,
    get_or_create_category_thread,
    start_new_session,
    submit_review_and_close_session,
    update_category_settings,
    _ensure_category_thread_embed,
)
from resources.emoji import EMOJI_LIST
from helpers.submission_panel import build_submission_panel_embed, _find_category

logger = logging.getLogger(__name__)


def _has_manage_permission(interaction: discord.Interaction) -> bool:
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    channel = interaction.channel
    if not member or not channel:
        return False
    perms = channel.permissions_for(member)
    return bool(perms.manage_threads or perms.manage_messages)


async def _send_temp_message(
    interaction: discord.Interaction,
    content: str,
    *,
    delay: int = 60,
) -> None:
    """Sends a non-ephemeral message and deletes it after delay seconds."""
    try:
        if interaction.response.is_done():
            msg = await interaction.followup.send(content=content, ephemeral=False)
        else:
            await interaction.response.send_message(content=content, ephemeral=False)
            msg = await interaction.original_response()
        try:
            await msg.delete(delay=delay)
        except Exception:
            pass
    except Exception:
        logger.exception("Failed to send temp response")


class _BaseSubmissionButton(discord.ui.Button):
    def __init__(self, *, label: str, style: discord.ButtonStyle, custom_id: str, category_code: str):
        super().__init__(label=label, style=style, custom_id=custom_id)
        self.category_code = category_code

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if not _has_manage_permission(interaction):
            await safe_reply(
                interaction,
                "Missing permission (requires `manage_threads` or `manage_messages`).",
                ephemeral=True,
            )
            return False
        return True


class _StartSessionButton(_BaseSubmissionButton):
    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await interaction.response.defer(ephemeral=True)
        if not await self._guard(interaction):
            return
        await start_new_session(interaction, category_code=self.category_code)


class _DownloadSessionButton(_BaseSubmissionButton):
    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await interaction.response.defer(ephemeral=True)
        if not await self._guard(interaction):
            return
        await download_session_export(interaction, category_code=self.category_code)


class _SubmitReviewButton(_BaseSubmissionButton):
    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await interaction.response.defer(ephemeral=True)
        if not await self._guard(interaction):
            return
        await submit_review_and_close_session(interaction, category_code=self.category_code)


class _EditLastReviewButton(_BaseSubmissionButton):
    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await interaction.response.defer(ephemeral=True)
        if not await self._guard(interaction):
            return
        await edit_last_session_review(interaction, category_code=self.category_code)


class _ReviewHereButton(_BaseSubmissionButton):
    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await interaction.response.defer(ephemeral=True)
        if not await self._guard(interaction):
            return
        from helpers.session_review import start_session_review

        await start_session_review(interaction, category_code=self.category_code)


class _SetLimitButton(_BaseSubmissionButton):
    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not _has_manage_permission(interaction):
            await interaction.response.send_message(
                "Missing permission (requires `manage_threads` or `manage_messages`).",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            _SetLimitModal(category_code=self.category_code, panel_message=interaction.message)
        )


class _EditCriteriaButton(_BaseSubmissionButton):
    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not _has_manage_permission(interaction):
            await interaction.response.send_message(
                "Missing permission (requires `manage_threads` or `manage_messages`).",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(
            _EditCriteriaModal(category_code=self.category_code, panel_message=interaction.message)
        )


class _UpdateCategoryButton(_BaseSubmissionButton):
    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await interaction.response.defer(ephemeral=True)
        if not await self._guard(interaction):
            return

        try:
            thread = await get_or_create_category_thread(interaction.client, category_code=self.category_code)
        except Exception as exc:
            await safe_reply(interaction, f"Failed to access category thread: {exc}", ephemeral=True)
            return

        try:
            await _ensure_category_thread_embed(thread, category_code=self.category_code)
        except Exception:
            logger.exception("Failed to update initial embed for %s", self.category_code)

        # Refresh panel embed (updates submission limit).
        try:
            from helpers.submission_panel import parse_panel_footer

            msg = interaction.message
            last_no = 0
            current_no = None
            if msg and msg.embeds:
                meta = parse_panel_footer(getattr(msg.embeds[0].footer, "text", "") if msg.embeds[0].footer else "")
                last_no = int(meta.get("last") or 0)
                current_no = meta.get("current_no")
                last_end = meta.get("last_end")
            else:
                last_end = None
            embed = build_submission_panel_embed(
                self.category_code,
                last_session_no=last_no,
                current_thread_id=int(thread.id),
                current_session_no=int(current_no) if current_no else None,
                is_locked=bool(thread.locked),
                last_finished_ts=int(last_end) if last_end else None,
            )
            await interaction.message.edit(
                embeds=[embed],
                view=MapSubmissionPanelView(self.category_code, show_start=False, is_locked=bool(thread.locked)),
            )
        except Exception:
            logger.exception("Failed to update panel embed for %s", self.category_code)

        await safe_reply(interaction, "✅ Category updated.", ephemeral=True)


class _ToggleThreadLockButton(_BaseSubmissionButton):
    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await self._guard(interaction):
            await interaction.response.defer(ephemeral=True)
            return

        try:
            thread = await get_category_thread(interaction.client, category_code=self.category_code)
        except Exception:
            thread = None
        if not isinstance(thread, discord.Thread):
            await interaction.response.defer(ephemeral=True)
            await safe_reply(interaction, "I couldn't access the category thread.", ephemeral=True)
            return

        new_locked = not bool(getattr(thread, "locked", False))
        if new_locked:
            modal = _LockReasonModal(
                category_code=self.category_code,
                thread=thread,
                panel_message=interaction.message,
            )
            await interaction.response.send_modal(modal)
            return

        # Unlock flow: delete last lock reason message.
        await interaction.response.defer(ephemeral=True)
        try:
            await thread.edit(locked=False)
        except Exception:
            logger.exception("Failed to unlock thread for %s", self.category_code)
            await _send_temp_message(interaction, "Failed to unlock thread.")
            return

        await _delete_last_lock_reason(thread)
        await _refresh_panel_view(interaction, thread=thread, is_locked=False)


class MapSubmissionPanelView(discord.ui.View):
    """Persistent panel view. Must be registered at bot startup via bot.add_view()."""

    def __init__(self, category_code: str, *, show_start: bool = True, is_locked: bool | None = None):
        super().__init__(timeout=None)
        self.category_code = category_code

        if show_start:
            self.add_item(
                _StartSessionButton(
                    label="Start session",
                    style=discord.ButtonStyle.success,
                    custom_id=f"map_submissions:{category_code}:start",
                    category_code=category_code,
                )
            )
        else:
            self.add_item(
                _UpdateCategoryButton(
                    label="Update category",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"map_submissions:{category_code}:update_category",
                    category_code=category_code,
                )
            )
        self.add_item(
            _DownloadSessionButton(
                label="Download session",
                style=discord.ButtonStyle.secondary,
                custom_id=f"map_submissions:{category_code}:download",
                category_code=category_code,
            )
        )
        self.add_item(
            _ReviewHereButton(
                label="Review here",
                style=discord.ButtonStyle.primary,
                custom_id=f"map_submissions:{category_code}:review_here",
                category_code=category_code,
            )
        )
        self.add_item(
            _SubmitReviewButton(
                label="Submit review",
                style=discord.ButtonStyle.secondary,
                custom_id=f"map_submissions:{category_code}:submit_review",
                category_code=category_code,
            )
        )
        self.add_item(
            _EditLastReviewButton(
                label="Edit last review",
                style=discord.ButtonStyle.secondary,
                custom_id=f"map_submissions:{category_code}:edit_last_review",
                category_code=category_code,
            )
        )

        lock_label = "Lock thread 🔒"
        if is_locked is True:
            lock_label = "Unlock thread 🔓"
        self.add_item(
            _ToggleThreadLockButton(
                label=lock_label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"map_submissions:{category_code}:toggle_lock",
                category_code=category_code,
            )
        )
        self.add_item(
            _SetLimitButton(
                label="Set map limit",
                style=discord.ButtonStyle.secondary,
                custom_id=f"map_submissions:{category_code}:set_limit",
                category_code=category_code,
            )
        )
        self.add_item(
            _EditCriteriaButton(
                label="Edit criteria",
                style=discord.ButtonStyle.secondary,
                custom_id=f"map_submissions:{category_code}:edit_criteria",
                category_code=category_code,
            )
        )


LOCK_REASON_PREFIX = EMOJI_LIST.get("_P1", "")
LOCK_REASON_MESSAGE_PREFIX = f"-# *{LOCK_REASON_PREFIX} "


async def _delete_last_lock_reason(thread: discord.Thread) -> None:
    try:
        async for msg in thread.history(limit=200, oldest_first=False):
            if not msg.author or getattr(msg.author, "bot", False) is False:
                continue
            if (msg.content or "").startswith(LOCK_REASON_MESSAGE_PREFIX):
                try:
                    await msg.delete()
                except Exception:
                    pass
                return
    except Exception:
        pass


async def _refresh_panel_view(
    interaction: discord.Interaction,
    *,
    thread: discord.Thread,
    is_locked: bool,
    panel_message: discord.Message | None = None,
) -> None:
    try:
        from helpers.submission_panel import parse_panel_footer

        msg = panel_message or interaction.message
        last_no = 0
        current_no = None
        if msg and msg.embeds:
            meta = parse_panel_footer(getattr(msg.embeds[0].footer, "text", "") if msg.embeds[0].footer else "")
            last_no = int(meta.get("last") or 0)
            current_no = meta.get("current_no")
            last_end = meta.get("last_end")
        else:
            last_end = None
        category_code = interaction.data.get("custom_id", "").split(":")[1]
        embed = build_submission_panel_embed(
            category_code,
            last_session_no=last_no,
            current_thread_id=int(thread.id),
            current_session_no=int(current_no) if current_no else None,
            is_locked=is_locked,
            last_finished_ts=int(last_end) if last_end else None,
        )
        if not msg:
            return
        await msg.edit(
            embeds=[embed],
            view=MapSubmissionPanelView(
                category_code,
                show_start=False,
                is_locked=is_locked,
            ),
        )
    except Exception:
        logger.exception("Failed to update panel after lock toggle")


class _LockReasonModal(discord.ui.Modal):
    def __init__(self, *, category_code: str, thread: discord.Thread, panel_message: discord.Message | None):
        super().__init__(title="Lock thread")
        self.category_code = category_code
        self.thread = thread
        self.panel_message = panel_message
        self.reason = discord.ui.TextInput(
            label="Reason for locking",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await interaction.response.defer(ephemeral=True)
        reason_text = str(self.reason.value or "").strip()
        try:
            await self.thread.send(f"{LOCK_REASON_MESSAGE_PREFIX}{reason_text}*")
            await self.thread.edit(locked=True)
        except Exception:
            logger.exception("Failed to lock thread for %s", self.category_code)
            await _send_temp_message(interaction, "Failed to lock thread.")
            return

        await _refresh_panel_view(interaction, thread=self.thread, is_locked=True, panel_message=self.panel_message)
        await _send_temp_message(interaction, "🔒 Thread locked.")


class _SetLimitModal(discord.ui.Modal):
    def __init__(self, *, category_code: str, panel_message: discord.Message | None):
        super().__init__(title="Set map limit")
        self.category_code = category_code
        self.panel_message = panel_message
        cat = _find_category(category_code) or {}
        current = cat.get("submissionlimit", 3)
        try:
            default = str(int(current)) if int(current) >= 1 else "3"
        except Exception:
            default = "3"
        self.limit = discord.ui.TextInput(
            label="Maps per user per session (>= 1)",
            placeholder="e.g. 3",
            required=True,
            max_length=4,
            default=default,
        )
        self.add_item(self.limit)

    async def on_submit(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await interaction.response.defer(ephemeral=True)
        raw = str(self.limit.value or "").strip()
        try:
            value = int(raw)
        except Exception:
            await safe_reply(interaction, "Invalid number. Please enter an integer >= 1.", ephemeral=True)
            return
        if value < 1:
            await safe_reply(interaction, "The limit must be an integer >= 1.", ephemeral=True)
            return
        await update_category_settings(
            interaction,
            category_code=self.category_code,
            updates={"submissionlimit": value},
            panel_message=self.panel_message,
            success_message=f"✅ Map limit updated to {value} map(s) per user.",
        )


class _EditCriteriaModal(discord.ui.Modal):
    def __init__(self, *, category_code: str, panel_message: discord.Message | None):
        super().__init__(title="Edit category criteria")
        self.category_code = category_code
        self.panel_message = panel_message
        cat = _find_category(category_code) or {}
        rules = cat.get("submissionRules") or []
        default = "\n".join(str(r) for r in rules)
        self.criteria = discord.ui.TextInput(
            label="Criteria (one per line)",
            placeholder="One rule/criterion per line. Shown in the public topic.",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=4000,
            default=default[:4000],
        )
        self.add_item(self.criteria)

    async def on_submit(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await interaction.response.defer(ephemeral=True)
        raw = str(self.criteria.value or "")
        rules = [line.strip() for line in raw.splitlines() if line.strip()]
        await update_category_settings(
            interaction,
            category_code=self.category_code,
            updates={"submissionRules": rules},
            panel_message=self.panel_message,
            success_message="✅ Category criteria updated.",
        )

