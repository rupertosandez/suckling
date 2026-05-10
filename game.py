"""
In-memory state for active guessing rounds, keyed by channel ID.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime


@dataclass
class GuessRound:
    channel_id: int
    movie_id: int
    title: str
    started_at: datetime
    started_by: str
    end_event: asyncio.Event
    difficulty: str = "easy"  # "easy" or "hard"
    winner_id: str | None = None
    winner_tag: str | None = None
    revealed: bool = False


# channel_id → active round
_rounds: dict[int, GuessRound] = {}


def get_round(channel_id: int) -> GuessRound | None:
    return _rounds.get(channel_id)


def start_round(round_obj: GuessRound) -> bool:
    """Register a new round. Returns False if one is already active in that channel."""
    if round_obj.channel_id in _rounds:
        return False
    _rounds[round_obj.channel_id] = round_obj
    return True


def end_round(channel_id: int) -> None:
    """Remove a round from the active set."""
    _rounds.pop(channel_id, None)


def title_matches(guess: str, title: str) -> bool:
    """Loose case-insensitive match. Strips common articles and punctuation."""
    def normalize(s: str) -> str:
        s = s.lower().strip()
        # Strip leading articles
        for article in ("the ", "a ", "an "):
            if s.startswith(article):
                s = s[len(article):]
        # Strip non-alphanumerics
        return "".join(c for c in s if c.isalnum() or c.isspace()).strip()

    return normalize(guess) == normalize(title)