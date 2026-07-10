import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_PATH = PROJECT_ROOT / ".env"

load_dotenv(ENV_PATH)


def _configured_path(env_name: str, default: Path) -> Path:
    value = os.getenv(env_name)
    path = Path(value).expanduser() if value else default
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
GUILD_ID = os.getenv("GUILD_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
PLEX_TOKEN = os.getenv("PLEX_TOKEN")
PLEX_LIBRARY = os.getenv("PLEX_LIBRARY", "Movies")  # default to "Movies"
TAUTULLI_URL = (os.getenv("TAUTULLI_URL") or "").rstrip("/")
TAUTULLI_API_KEY = os.getenv("TAUTULLI_API_KEY")
PLEX_CLEANUP_ENABLED = os.getenv("PLEX_CLEANUP_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
BOT_TIMEZONE = os.getenv("BOT_TIMEZONE", "America/Los_Angeles")
# Postgres connection pool sizing (only used when DATABASE_URL is set).
DB_POOL_MIN_SIZE = int(os.getenv("DB_POOL_MIN_SIZE", "2"))
DB_POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", "10"))
ACHIEVEMENT_CATALOG_URL = (
    os.getenv("ACHIEVEMENT_CATALOG_URL")
    or "https://rupertosandez.github.io/sucklingsite/achievements/"
)
PORTAL_BASE_URL = (os.getenv("PORTAL_BASE_URL") or "https://sucklingweb.onrender.com").rstrip("/")

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN not set in .env")
if not TMDB_API_KEY:
    raise RuntimeError("TMDB_API_KEY not set in .env")
if not GUILD_ID:
    raise RuntimeError("GUILD_ID not set in .env")
# PLEX_TOKEN is optional — bot still works without it, /plex command will be unavailable

GUILD_ID = int(GUILD_ID)

DATA_DIR = _configured_path("SUCKLINGBOT_DATA_DIR", PROJECT_ROOT / "data")
ASSETS_DIR = _configured_path("SUCKLINGBOT_ASSETS_DIR", PROJECT_ROOT / "assets")

DB_PATH = DATA_DIR / "moviebot.db"
LOG_PATH = DATA_DIR / "bot.log"
LOGO_PATH = ASSETS_DIR / "logo.png"
