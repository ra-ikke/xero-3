"""Cog loading utilities.

These helpers centralize how we discover and load extensions, so `bot.py` and
`deploy_commands.py` behave the same way.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable

from discord.ext import commands

logger = logging.getLogger(__name__)


def iter_extension_modules(package_dir: str) -> Iterable[str]:
    """Yields importable extension module paths under a package directory.

    Example:
        package_dir="cogs/public" -> yields "cogs.public.map_info"
    """
    if not os.path.exists(package_dir):
        return

    package = package_dir.replace("/", ".").replace("\\", ".")
    for filename in os.listdir(package_dir):
        if not filename.endswith(".py"):
            continue
        if filename.startswith("_") or filename == "__init__.py":
            continue
        # Safety: never load scratch/test modules.
        if filename.endswith(".test.py") or filename.endswith(".py.test"):
            continue
        module = f"{package}.{filename[:-3]}"
        yield module


async def load_cogs(bot: commands.Bot) -> None:
    """Loads all cogs from public and private directories."""
    for module in iter_extension_modules("cogs/public"):
        try:
            await bot.load_extension(module)
            logger.info("Loaded public cog: %s", module)
        except Exception:
            logger.exception("Failed to load public cog %s", module)

    for module in iter_extension_modules("cogs/private"):
        try:
            await bot.load_extension(module)
            logger.info("Loaded private cog: %s", module)
        except Exception:
            logger.exception("Failed to load private cog %s", module)

