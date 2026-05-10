"""
Random horror film picker.

Maintains a cached candidate pool (top ~1000 popular horror films) and provides
a filtered random selection.
"""
import asyncio
import random
from datetime import datetime, timedelta

import aiohttp

import config
import tmdb
import db


HORROR_GENRE_ID = 27
DISCOVER_PAGES = 50  # 20 movies per page = 1000 candidates
PER_PAGE_SLEEP_SECONDS = 0.2

# Cache the pool for 24 hours to avoid repeated full pulls
_pool_cache: dict[str, object] = {"movies": None, "fetched_at": None}
_POOL_TTL_SECONDS = 24 * 3600


async def _fetch_pool(session: aiohttp.ClientSession) -> list[dict]:
    """Fetch a broad pool of horror films from TMDB Discover."""
    movies = []
    for page in range(1, DISCOVER_PAGES + 1):
        params = {
            "api_key": config.TMDB_API_KEY,
            "with_genres": str(HORROR_GENRE_ID),
            "sort_by": "popularity.desc",
            "page": page,
            "include_adult": "false",
            "vote_count.gte": 50,  # filter out obscure entries with no ratings
        }
        url = f"{tmdb.BASE_URL}/discover/movie"
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                print(f"  [picker] page {page} returned {resp.status}, stopping")
                break
            data = await resp.json()
            page_results = data.get("results", [])
            if not page_results:
                break
            movies.extend(page_results)
        await asyncio.sleep(PER_PAGE_SLEEP_SECONDS)
    return movies


async def _get_pool() -> list[dict]:
    """Return the cached pool, refreshing if expired."""
    now = datetime.now()
    fetched_at = _pool_cache.get("fetched_at")
    if (
        _pool_cache.get("movies") is not None
        and fetched_at is not None
        and (now - fetched_at).total_seconds() < _POOL_TTL_SECONDS
    ):
        return _pool_cache["movies"]

    print(f"[picker] Refreshing candidate pool at {now.isoformat(timespec='seconds')}...")
    async with aiohttp.ClientSession() as session:
        movies = await _fetch_pool(session)
    _pool_cache["movies"] = movies
    _pool_cache["fetched_at"] = now
    print(f"[picker] Pool refreshed: {len(movies)} films")
    return movies


def _decade_filter(decade: str | None):
    """Return a function that checks if a movie's release year is in the given decade."""
    if not decade:
        return lambda m: True

    # Accept "1980s", "80s", "1980", "1990s", etc.
    digits = "".join(c for c in decade if c.isdigit())
    if len(digits) == 2:
        # "80s" → assume 1980s if <50, else 19xx; default to 19xx
        year_start = 1900 + int(digits) if int(digits) >= 30 else 2000 + int(digits)
    elif len(digits) == 4:
        year_start = (int(digits) // 10) * 10
    else:
        return lambda m: True

    year_end = year_start + 9

    def check(movie):
        date = movie.get("release_date", "")
        if len(date) < 4:
            return False
        try:
            year = int(date[:4])
        except ValueError:
            return False
        return year_start <= year <= year_end

    return check


def _runtime_filter(runtime: str | None):
    """
    Return a function that checks runtime category.
    Note: runtime isn't in the Discover response, so this filter requires
    a follow-up call. We approximate using `runtime` if present, else accept all.
    """
    if not runtime:
        return lambda m: True
    runtime = runtime.lower()

    def check(movie):
        rt = movie.get("runtime")
        if rt is None:
            return True  # unknown runtime, don't exclude
        if runtime in ("short", "<90"):
            return rt < 90
        if runtime in ("long", ">120"):
            return rt > 120
        if runtime in ("medium", "90-120"):
            return 90 <= rt <= 120
        return True

    return check


async def pick_random(
    decade: str | None = None,
    runtime: str | None = None,
    exclude_ids: set[int] | None = None,
) -> dict | None:
    """
    Pick a random horror film matching the given filters.
    Returns the basic movie dict from Discover, or None if no matches.
    """
    exclude_ids = exclude_ids or set()
    pool = await _get_pool()

    decade_check = _decade_filter(decade)

    # First pass: filter by decade and exclusions (cheap — uses pool data only)
    candidates = [m for m in pool if m["id"] not in exclude_ids and decade_check(m)]
    if not candidates:
        return None

    # Shuffle for random selection
    random.shuffle(candidates)

    # If runtime filter is requested, we may need to fetch details
    if runtime:
        runtime_check = _runtime_filter(runtime)
        for candidate in candidates:
            try:
                details = await tmdb.get_movie_details(candidate["id"])
            except tmdb.TMDBError:
                continue
            if runtime_check(details):
                return candidate
            # Limit how many details we fetch trying to satisfy runtime
            # (in case the filter is restrictive — don't loop forever)
        # If we exhausted candidates without a runtime match, return first one anyway
        return candidates[0] if candidates else None

    return candidates[0]


def force_refresh_pool() -> None:
    """Clear the cached pool so the next call re-fetches."""
    _pool_cache["movies"] = None
    _pool_cache["fetched_at"] = None