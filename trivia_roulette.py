"""
Trivia Roulette: /play game.

Randomly picks one of four curated trivia categories (quote, emoji, tagline,
trivia) and posts a prompt. First correct guess in chat wins the round.

Content lives in assets/*.json — loaded once at startup. Each entry has:
  - answer: canonical film title
  - year:   release year
  - prompt: the clue text (quote, emoji string, tagline, or trivia)
  - aliases: optional list of accepted alternate answers
"""
import asyncio
import json
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ROUND_DURATION_SECONDS = 30
ASSETS_DIR = Path("assets")

# Category metadata: display label, emoji, embed color (hex int)
CATEGORIES = {
    "quote":   {"label": "quote",   "emoji": "🎬", "color": 0x4A90E2, "file": "quotes.json"},
    "emoji":   {"label": "emoji",   "emoji": "🎭", "color": 0xF5A623, "file": "emoji.json"},
    "tagline": {"label": "tagline", "emoji": "📜", "color": 0x7ED321, "file": "taglines.json"},
    "trivia":  {"label": "trivia",  "emoji": "🎞️", "color": 0xBD10E0, "file": "trivia.json"},
}


@dataclass
class TriviaRound:
    channel_id: int
    category: str           # one of CATEGORIES keys
    prompt: str
    answer: str
    year: int | None
    aliases: list[str]
    started_at: datetime
    started_by: str
    end_event: asyncio.Event
    winner_id: str | None = None
    winner_tag: str | None = None
    revealed: bool = False


# channel_id → active TriviaRound
_rounds: dict[int, TriviaRound] = {}

# category name → list of entry dicts. Populated by load_assets().
_entries: dict[str, list[dict]] = {}


def load_assets() -> dict[str, int]:
    """
    Load all category JSON files from the assets dir.

    Missing or empty files log a warning and are skipped — the bot keeps
    running with whatever categories are populated. Returns a dict of
    category → entry count for the startup log line.
    """
    counts: dict[str, int] = {}
    for category, meta in CATEGORIES.items():
        path = ASSETS_DIR / meta["file"]
        if not path.exists():
            print(f"[trivia] WARN: {path} not found — skipping '{category}' category")
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[trivia] WARN: failed to load {path}: {e} — skipping '{category}' category")
            continue
        if not isinstance(data, list) or not data:
            print(f"[trivia] WARN: {path} is empty or not a list — skipping '{category}' category")
            continue
        _entries[category] = data
        counts[category] = len(data)
    return counts


def available_categories() -> list[str]:
    """Categories that have at least one loaded entry."""
    return list(_entries.keys())


def get_round(channel_id: int) -> TriviaRound | None:
    return _rounds.get(channel_id)


def start_round(round_obj: TriviaRound) -> bool:
    """Register a new round. Returns False if one is already active in that channel."""
    if round_obj.channel_id in _rounds:
        return False
    _rounds[round_obj.channel_id] = round_obj
    return True


def end_round(channel_id: int) -> None:
    """Remove a round from the active set."""
    _rounds.pop(channel_id, None)


def pick_random_entry() -> tuple[str, dict] | None:
    """
    Pick a random category (uniformly across available ones), then a random
    entry from that category. Returns (category, entry) or None if no
    categories are loaded.
    """
    categories = available_categories()
    if not categories:
        return None
    category = random.choice(categories)
    entry = random.choice(_entries[category])
    return category, entry


def _normalize(s: str) -> str:
    """Lowercase, strip leading articles, strip non-alphanumerics."""
    s = (s or "").lower().strip()
    for article in ("the ", "a ", "an "):
        if s.startswith(article):
            s = s[len(article):]
    return "".join(c for c in s if c.isalnum())


def answer_matches(guess: str, round_obj: TriviaRound) -> bool:
    """Check a guess against the round's answer and aliases. Loose fuzzy match."""
    normalized = _normalize(guess)
    if not normalized:
        return False
    if normalized == _normalize(round_obj.answer):
        return True
    for alias in round_obj.aliases:
        if normalized == _normalize(alias):
            return True
    return False
