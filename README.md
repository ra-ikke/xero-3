Discord Bot with Slash Commands

Discord bot built with `discord.py` and native slash commands.

## 🚀 Features

- Modular cog structure for extensibility
- Native slash commands supported by Discord
- **Public commands** (available in every server)
- **Private commands** (restricted to configured servers)
- Secure token handling via `.env`
- Basic logging and error handling

## 📋 Available Commands

### Public Commands (Global)
- `/ping` — Checks the bot latency
- `/info` — Shows information about the bot
- `/userinfo [user]` — Shows information about a user
- `/serverinfo` — Shows server statistics

### Private Commands (Authorized Servers)
- `/admin` — Private administrative helper
- `/authorized-servers` — Lists the servers authorized for private commands

## 🛠️ Installation

### Requirements

- Python 3.8+
- Discord Developer Portal account
- Registered Discord bot

### Steps

1. **Clone or download this repository.**

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure the bot token:**
   - Copy `.env.example` to `.env`:
     ```bash
     cp .env.example .env
     ```
   - Open `.env` and add your Discord token:
     ```
     DISCORD_TOKEN=your_token_here
     ```
   - Get the token from the [Discord Developer Portal](https://discord.com/developers/applications)

4. **Configure private command servers (optional):**
   - Add server IDs to `.env`:
     ```
     PRIVATE_SERVER_IDS=123456789012345678,987654321098765432
     ```
   - Enable Developer Mode in Discord and copy server IDs via right-click > “Copy ID”

5. **Run the bot:**
   ```bash
   python bot.py
   ```

## ☁️ Cloud Deployment (Render)

This bot runs as a long‑running process, so use a worker/VM host (not serverless).

### Render (Background Worker)

1. Push this repo to GitHub/GitLab.
2. In Render, create a **Background Worker**.
3. Configure:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python bot.py`
4. Set environment variables (see below).
5. Deploy and check logs for a successful login.

### Environment variables

Required:
- `DISCORD_TOKEN`

Optional:
- `PRIVATE_SERVER_IDS` (comma separated list of server IDs)
- `COMMAND_PREFIX`
- `DEBUG` (`true` or `false`)
- `CYPHER_URL`
- `MAPDRAW_URL`
- `WEBHOOK_URL`
- `STATUS_URL`
- `MAPDRAW_STATUS_URL`

> Note: Render free tier may hibernate. If you need 24/7 uptime, consider a paid plan or a VM (e.g., Oracle Always Free).

## 🔧 Discord Bot Setup

1. Visit the [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application or choose an existing one
3. Go to the **Bot** tab and add a bot
4. Copy the token and paste it into `.env`
5. Under **OAuth2 > URL Generator**:
   - Select the `bot` scope
   - Grant permissions such as:
     - Send Messages
     - Use Slash Commands
     - Embed Links
     - Read Message History
   - Use the generated URL to invite the bot to your server

## 📁 Project Structure

```
xero3.0/
├── bot.py                 # Entry point to start the bot
├── config.py              # Configuration loader
├── cogs/                  # Cog registry
│   ├── __init__.py
│   ├── public/            # Public commands
│   │   ├── __init__.py
│   │   ├── ping.py        # /ping command
│   │   ├── info.py        # /info command
│   │   ├── userinfo.py    # /userinfo command
│   │   └── serverinfo.py  # /serverinfo command
│   └── private/           # Private commands
│       ├── __init__.py
│       ├── admin.py       # /admin command
│       └── authorized_servers.py # /authorized-servers command
├── .env                   # Secret settings (not committed)
├── .env.example           # Template .env file
├── .gitignore             # Ignore rules
├── requirements.txt       # Python dependencies
└── README.md              # This documentation
```

## ✚ Adding New Commands

Each command lives in its own file. Add commands as follows:

1. **Create a public command** (available everywhere):
   - Place a new file in `cogs/public/` (e.g., `cogs/public/mycommand.py`)
   - Example structure:
     ```python
     import discord
     from discord import app_commands
     from discord.ext import commands

     class MyCommand(commands.Cog):
         def __init__(self, bot: commands.Bot):
             self.bot = bot

         @app_commands.command(name='mycommand', description='Describe your command')
         async def my_command(self, interaction: discord.Interaction):
             await interaction.response.send_message('Command response')

     async def setup(bot: commands.Bot):
         await bot.add_cog(MyCommand(bot))
     ```

2. **Create a private command** (restricted to configured servers):
   - Add a new file in `cogs/private/` (e.g., `cogs/private/myprivate.py`)
   - Use this template:
     ```python
     import discord
     from discord import app_commands
     from discord.ext import commands
     from config import PRIVATE_SERVER_IDS

     _guild_objects = [discord.Object(id=server_id) for server_id in PRIVATE_SERVER_IDS] if PRIVATE_SERVER_IDS else []

     class MyPrivateCommand(commands.Cog):
         def __init__(self, bot: commands.Bot):
             self.bot = bot

         if _guild_objects:
             @app_commands.command(name='myprivate', description='Private command')
             @app_commands.guilds(*_guild_objects)
             async def my_private(self, interaction: discord.Interaction):
                 await interaction.response.send_message('Private command response')

     async def setup(bot: commands.Bot):
         await bot.add_cog(MyPrivateCommand(bot))
     ```

3. **The bot automatically loads** every `.py` file under `cogs/public/` and `cogs/private/` at startup.

## 📝 Notes

- Ensure the bot has the necessary permissions in your Discord server
- Slash commands may take a few minutes to appear after syncing
- Enable required intents on the Developer Portal when necessary

## 🐛 Troubleshooting

- **Invalid token**: Confirm the token is correct inside `.env`
- **Commands do not appear**: Wait a few minutes or restart the bot
- **Permission errors**: Verify the bot has the correct permissions on the server
- **Command deployment**: Slash commands are overwritten on every sync (global + private), which replaces stale commands and publishes the current set.
- **Standalone deployment helper**: Run `python deploy_commands.py` whenever you want to publish the latest slash commands without running `bot.py`.

## 📄 License

This project is open source and free to use.

