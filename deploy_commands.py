"""Utility script that clears and deploys slash commands without running the full bot."""

import asyncio
import logging

from bot import bot
from app.cogs_loader import load_cogs
from app.sync import sync_commands
from config import DISCORD_TOKEN

logger = logging.getLogger(__name__)


async def deploy_commands() -> None:
    """Load extensions, clear current commands, and re-sync."""
    await load_cogs(bot)
    await bot.login(DISCORD_TOKEN)

    try:
        await sync_commands(bot)
    finally:
        await bot.close()


def main() -> None:
    """Entrypoint for command deployment."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    asyncio.run(deploy_commands())


if __name__ == '__main__':
    main()
