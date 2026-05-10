import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
GUILD_ID = os.getenv("GUILD_ID")
PLEX_TOKEN = os.getenv("PLEX_TOKEN")
PLEX_LIBRARY = os.getenv("PLEX_LIBRARY", "Movies")  # default to "Movies"

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN not set in .env")
if not TMDB_API_KEY:
    raise RuntimeError("TMDB_API_KEY not set in .env")
if not GUILD_ID:
    raise RuntimeError("GUILD_ID not set in .env")
# PLEX_TOKEN is optional — bot still works without it, /plex command will be unavailable

GUILD_ID = int(GUILD_ID)

DB_PATH = "data/moviebot.db"