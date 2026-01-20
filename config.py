import os
from dotenv import load_dotenv

# Load environment variables from the .env file
load_dotenv()

# Discord bot token
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

# Ensure the token was provided
if not DISCORD_TOKEN:
    raise ValueError(
        "DISCORD_TOKEN not found in .env. Create .env with DISCORD_TOKEN=your_token_here"
    )

# Optional prefix for text commands
COMMAND_PREFIX = os.getenv('COMMAND_PREFIX', '!')

# Additional configuration values
DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'

# Server IDs for private commands (comma separated)
# Example: PRIVATE_SERVER_IDS=123456789012345678,987654321098765432
PRIVATE_SERVER_IDS_STR = os.getenv('PRIVATE_SERVER_IDS', '')
PRIVATE_SERVER_IDS = []

if PRIVATE_SERVER_IDS_STR:
    try:
        PRIVATE_SERVER_IDS = [
            int(server_id.strip())
            for server_id in PRIVATE_SERVER_IDS_STR.split(',')
            if server_id.strip()
        ]
    except ValueError:
        raise ValueError(
            "PRIVATE_SERVER_IDS contains invalid values. "
            "Use only numbers separated by commas (e.g., 123456789012345678,987654321098765432)"
        )

# Map service configuration
def _string_from_env(key: str, default: str) -> str:
    value = os.getenv(key, default).strip()
    return value or default

RAW_CYPHER_URL = _string_from_env('CYPHER_URL', 'http://localhost:8000')
CYPHER_URL = RAW_CYPHER_URL.rstrip('/')
CYPHER_GETMAP_URL = f'{CYPHER_URL}/maps/?mapCode='
MAPDRAW_URL = _string_from_env('MAPDRAW_URL', 'http://localhost:3000/upload-image')
WEBHOOK_URL = _string_from_env('WEBHOOK_URL', 'http://localhost:8000/webhook')
STATUS_URL = _string_from_env('STATUS_URL', 'http://localhost:8000/')
MAPDRAW_STATUS_URL = _string_from_env('MAPDRAW_STATUS_URL', 'http://localhost:3000/status')


