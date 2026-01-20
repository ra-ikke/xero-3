"""Background review poster task (runs every 30 minutes)."""

from __future__ import annotations

import logging

from discord.ext import commands, tasks

from forum.review_post import update_reviews

logger = logging.getLogger(__name__)


class ReviewPoster(commands.Cog):
    """Runs a periodic task to scrape forum reviews and update Discord messages."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.review_loop.start()

    def cog_unload(self):
        self.review_loop.cancel()

    @tasks.loop(minutes=30)
    async def review_loop(self):
        await update_reviews(self.bot)

    @review_loop.before_loop
    async def before_review_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    """Registers the cog."""
    await bot.add_cog(ReviewPoster(bot))

