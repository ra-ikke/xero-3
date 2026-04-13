import discord
from discord.ext import commands
import logging
import asyncio
from config import DISCORD_TOKEN
from app.cogs_loader import load_cogs
from app.sync import sync_commands
from service.http_client import close_session as close_http_session
from app.session_api import start_session_api, stop_session_api

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Required intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# Build the bot instance
bot = commands.Bot(
    command_prefix='!',  # Optional prefix for text commands
    intents=intents,
    help_command=None  # Disables the default help command
)


@bot.event
async def on_ready():
    """Event fired once the bot is ready."""
    logger.info(f'Connected as {bot.user}')
    logger.info(f'Bot ID: {bot.user.id}')
    logger.info(f'Connected to {len(bot.guilds)} guild(s)')

    try:
        await sync_commands(bot)
    except Exception as err:
        logger.exception("Failed to sync commands: %s", err)


@bot.event
async def on_connect():
    """Event fired when the bot establishes a connection."""
    logger.info('Connected to Discord')


@bot.event
async def on_disconnect():
    """Event fired when the bot disconnects."""
    logger.warning('Disconnected from Discord')


async def main():
    """Main function that starts the bot."""
    # Load the cogs
    await load_cogs(bot)

    # Register persistent views (UI components)
    session_api_runner = None
    try:
        from ui.close_discussion_view import CloseDiscussionView
        from ui.report_actions import ReportActionsViewDiscuss, ReportActionsViewHandle
        from ui.map_submission_view import MapSubmissionPanelView
        from ui.votecrew_review_view import VotecrewReviewView
        from resources.channels import SUPPORTED_SUBMISSION_CATEGORIES
        bot.add_view(CloseDiscussionView())
        bot.add_view(ReportActionsViewDiscuss())
        bot.add_view(ReportActionsViewHandle())
        bot.add_view(VotecrewReviewView())
        for code in SUPPORTED_SUBMISSION_CATEGORIES:
            bot.add_view(MapSubmissionPanelView(code))
            bot.add_view(MapSubmissionPanelView(code, show_start=False))
        logger.info('Registered persistent views')

        # Optional local HTTP API for session JSON export.
        try:
            session_api_runner = await start_session_api(bot)
        except Exception as err:
            logger.error("Failed to start Session API: %s", err)
    except Exception as err:
        logger.error('Failed to register persistent views: %s', err)
    
    # Start the bot
    try:
        await bot.start(DISCORD_TOKEN)
    except discord.LoginFailure:
        logger.error('Invalid token. Check DISCORD_TOKEN in .env')
    except Exception as e:
        logger.error(f'Failed to start the bot: {e}')
    finally:
        try:
            await stop_session_api(session_api_runner)
        except Exception:
            logger.exception("Failed to stop Session API")
        # Ensure we close any shared aiohttp sessions on shutdown.
        try:
            await close_http_session()
        except Exception:
            logger.exception("Failed to close shared HTTP session")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info('Bot stopped by user')
    except Exception as e:
        logger.error(f'Fatal error: {e}')


