"""
Six Degrees of Separation game.

Picks two random popular actors, players submit chains connecting them via
shared films. First valid chain wins; shorter chains earn more points.
"""
import asyncio
import random
import re
from dataclasses import dataclass, field
from datetime import datetime

import tmdb


# Scoring: shorter chains demonstrate more knowledge
POINTS_BY_LENGTH = {
    1: 5,  # direct co-stars
    2: 4,
    3: 3,
    4: 2,
    5: 1,
    6: 1,
}
MAX_CHAIN_FILMS = 6
ROUND_DURATION_SECONDS = 240  # 4 minutes


# Pool of popular people, refreshed once per day
_actor_pool: list[dict] | None = None
_pool_fetched_at: datetime | None = None
_POOL_TTL_SECONDS = 24 * 3600


# channel_id → active SixRound
_rounds: dict[int, "SixRound"] = {}


@dataclass
class SixRound:
    channel_id: int
    actor_a_id: int
    actor_a_name: str
    actor_b_id: int
    actor_b_name: str
    started_at: datetime
    end_event: asyncio.Event
    winner_id: str | None = None
    winner_tag: str | None = None
    winning_chain: list[str] | None = None
    winning_film_count: int = 0
    revealed: bool = False


def get_round(channel_id: int) -> SixRound | None:
    return _rounds.get(channel_id)


def start_round(round_obj: SixRound) -> bool:
    if round_obj.channel_id in _rounds:
        return False
    _rounds[round_obj.channel_id] = round_obj
    return True


def end_round(channel_id: int) -> None:
    _rounds.pop(channel_id, None)


async def _refresh_actor_pool() -> list[dict]:
    """Pull popular actors from TMDB. Cached for 24 hours."""
    global _actor_pool, _pool_fetched_at

    now = datetime.now()
    if (
        _actor_pool is not None
        and _pool_fetched_at is not None
        and (now - _pool_fetched_at).total_seconds() < _POOL_TTL_SECONDS
    ):
        return _actor_pool

    print("[sixdegrees] Refreshing actor pool...")
    pool = []
    for page in range(1, 11):  # 10 pages × ~20 = ~200 popular actors
        try:
            people = await tmdb.get_popular_people(page=page)
        except tmdb.TMDBError:
            break
        # Filter out non-actors (some entries are directors, producers)
        actors = [p for p in people if p.get("known_for_department") == "Acting"]
        pool.extend(actors)
        await asyncio.sleep(0.2)

    _actor_pool = pool
    _pool_fetched_at = now
    print(f"[sixdegrees] Pool refreshed: {len(pool)} actors")
    return pool


async def pick_two_actors() -> tuple[dict, dict] | None:
    """Pick two distinct random actors from the popular pool."""
    pool = await _refresh_actor_pool()
    if len(pool) < 2:
        return None
    a, b = random.sample(pool, 2)
    return a, b


# ---------- chain parsing & validation ----------

def _normalize(s: str) -> str:
    """Lowercase, strip punctuation/extra whitespace for fuzzy comparison."""
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def parse_chain(text: str) -> list[str] | None:
    """
    Parse a chain string into a list of alternating actor/film names.
    Returns None if the format is invalid.
    Accepts -> or → as separator.
    """
    # Normalize both arrow types to ->
    text = text.replace("→", "->")
    parts = [p.strip() for p in text.split("->")]
    parts = [p for p in parts if p]  # drop empties

    # Need odd count: actor, film, actor, film, ..., actor
    if len(parts) < 3 or len(parts) % 2 == 0:
        return None
    return parts


@dataclass
class ValidationResult:
    valid: bool
    film_count: int = 0
    error: str | None = None


async def validate_chain(
    chain: list[str],
    expected_start: str,
    expected_end: str,
) -> ValidationResult:
    """
    Validate a chain of actor/film/actor/film/.../actor names.

    Confirms:
    - First and last actors match the expected endpoints (fuzzy)
    - Each "actor -> film -> actor" triple has both actors in the film's cast
    - No duplicate films within the chain
    """
    if len(chain) < 3:
        return ValidationResult(False, error="Chain must have at least 1 film (actor → film → actor)")

    # Validate length: number of films = (len - 1) / 2
    film_count = (len(chain) - 1) // 2
    if film_count > MAX_CHAIN_FILMS:
        return ValidationResult(
            False,
            error=f"Chain has {film_count} films — max allowed is {MAX_CHAIN_FILMS}.",
        )

    # First and last actors must match endpoints
    if _normalize(chain[0]) != _normalize(expected_start):
        return ValidationResult(
            False,
            error=f"Chain must start with **{expected_start}**, you started with **{chain[0]}**.",
        )
    if _normalize(chain[-1]) != _normalize(expected_end):
        return ValidationResult(
            False,
            error=f"Chain must end with **{expected_end}**, you ended with **{chain[-1]}**.",
        )

    # Check for duplicate films
    films_in_chain = [chain[i] for i in range(1, len(chain), 2)]
    seen_films = set()
    for film in films_in_chain:
        normalized = _normalize(film)
        if normalized in seen_films:
            return ValidationResult(
                False,
                error=f"Film **{film}** appears more than once. Each film must be unique.",
            )
        seen_films.add(normalized)

    # Validate each "actor -> film -> actor" triple
    for i in range(0, len(chain) - 2, 2):
        actor_a_name = chain[i]
        film_name = chain[i + 1]
        actor_b_name = chain[i + 2]

        # Find the film
        try:
            results = await tmdb.search_movie(film_name)
        except tmdb.TMDBError as e:
            return ValidationResult(False, error=f"Couldn't search TMDB for **{film_name}**: {e}")

        if not results:
            return ValidationResult(False, error=f"Couldn't find a film called **{film_name}** on TMDB.")

        film = results[0]
        film_id = film["id"]
        film_title_actual = film.get("title", film_name)

        # Get its cast
        try:
            cast = await tmdb.get_movie_cast(film_id)
        except tmdb.TMDBError as e:
            return ValidationResult(False, error=f"Couldn't fetch cast for **{film_title_actual}**: {e}")

        cast_names_normalized = {_normalize(c.get("name", "")) for c in cast}

        if _normalize(actor_a_name) not in cast_names_normalized:
            return ValidationResult(
                False,
                error=f"**{actor_a_name}** is not in the cast of **{film_title_actual}**.",
            )
        if _normalize(actor_b_name) not in cast_names_normalized:
            return ValidationResult(
                False,
                error=f"**{actor_b_name}** is not in the cast of **{film_title_actual}**.",
            )

    return ValidationResult(True, film_count=film_count)


def points_for(film_count: int) -> int:
    """Return the points awarded for a chain of N films."""
    return POINTS_BY_LENGTH.get(film_count, 1)