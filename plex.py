"""
Plex integration: connects to your remote Plex server via plex.tv
and provides random movie picking + library stats.
"""
import asyncio
import random
from typing import Any

from plexapi.myplex import MyPlexAccount
from plexapi.exceptions import NotFound, Unauthorized

import config


_account: MyPlexAccount | None = None
_server: Any | None = None
_library: Any | None = None
_movies_cache: list | None = None
_cache_age: float = 0


class PlexError(Exception):
    pass


def _connect_sync() -> None:
    global _account, _server, _library

    if not config.PLEX_TOKEN:
        raise PlexError("PLEX_TOKEN not configured in .env")

    if _server is not None and _library is not None:
        return

    try:
        _account = MyPlexAccount(token=config.PLEX_TOKEN)
    except Unauthorized:
        raise PlexError("Plex token is invalid or expired")
    except Exception as e:
        raise PlexError(f"Couldn't authenticate with Plex: {e}")

    resources = [r for r in _account.resources() if r.owned and "server" in r.provides]
    if not resources:
        raise PlexError("No Plex servers found on this account")

    try:
        _server = resources[0].connect(timeout=15)
    except Exception as e:
        raise PlexError(f"Couldn't connect to Plex server: {e}")

    try:
        _library = _server.library.section(config.PLEX_LIBRARY)
    except NotFound:
        available = ", ".join(s.title for s in _server.library.sections())
        raise PlexError(
            f"Library '{config.PLEX_LIBRARY}' not found. "
            f"Available: {available}"
        )


async def _connect() -> None:
    await asyncio.to_thread(_connect_sync)


def _refresh_cache_sync() -> list:
    if _library is None:
        raise PlexError("Not connected to Plex")
    return list(_library.all())


async def _get_movies() -> list:
    """Get the movie list, refreshing the cache once per hour."""
    global _movies_cache, _cache_age

    import time
    now = time.time()
    if _movies_cache is None or (now - _cache_age) > 3600:
        await _connect()
        _movies_cache = await asyncio.to_thread(_refresh_cache_sync)
        _cache_age = now
    return _movies_cache


def _absolute_url(relative: str | None) -> str | None:
    """Convert a relative Plex image path to a full URL with auth token."""
    if not relative:
        return None
    server_url = _server._baseurl if _server else ""
    return f"{server_url}{relative}?X-Plex-Token={config.PLEX_TOKEN}"


def _movie_to_dict(movie: Any) -> dict:
    """Serialize a Plex movie object into a plain dict."""
    return {
        "title": movie.title,
        "year": movie.year,
        "summary": movie.summary or "",
        "thumb_url": _absolute_url(movie.thumb),
        "art_url": _absolute_url(movie.art),
        "duration_minutes": int(movie.duration / 60000) if movie.duration else None,
        "rating": movie.contentRating or None,
        "audience_rating": movie.audienceRating,
        "added_at": movie.addedAt,
        "genres": [g.tag for g in (movie.genres or [])],
    }


# ---------- random pick (existing /rb9) ----------

async def pick_random_movie() -> dict | None:
    """Pick a random movie from the library."""
    movies = await _get_movies()
    if not movies:
        return None
    return _movie_to_dict(random.choice(movies))


# ---------- stats commands ----------

async def get_library_summary() -> dict:
    """Overall library stats: count, total runtime, oldest, newest, avg rating."""
    movies = await _get_movies()
    if not movies:
        return {"count": 0}

    total_minutes = sum(int(m.duration / 60000) for m in movies if m.duration)
    years = [m.year for m in movies if m.year]
    ratings = [m.audienceRating for m in movies if m.audienceRating is not None]

    oldest = min(movies, key=lambda m: m.year if m.year else 9999)
    newest_by_year = max(movies, key=lambda m: m.year if m.year else 0)
    newest_added = max(movies, key=lambda m: m.addedAt if m.addedAt else 0)

    return {
        "count": len(movies),
        "total_minutes": total_minutes,
        "oldest": _movie_to_dict(oldest),
        "newest_by_year": _movie_to_dict(newest_by_year),
        "newest_added": _movie_to_dict(newest_added),
        "avg_rating": sum(ratings) / len(ratings) if ratings else None,
        "rated_count": len(ratings),
        "min_year": min(years) if years else None,
        "max_year": max(years) if years else None,
    }


async def get_longest_movie() -> dict | None:
    movies = await _get_movies()
    candidates = [m for m in movies if m.duration]
    if not candidates:
        return None
    longest = max(candidates, key=lambda m: m.duration)
    return _movie_to_dict(longest)


async def get_shortest_movie() -> dict | None:
    movies = await _get_movies()
    # Filter out very short entries (likely shorts/extras under 30 min)
    candidates = [m for m in movies if m.duration and m.duration > 30 * 60000]
    if not candidates:
        # Fall back to all if filter is too aggressive
        candidates = [m for m in movies if m.duration]
    if not candidates:
        return None
    shortest = min(candidates, key=lambda m: m.duration)
    return _movie_to_dict(shortest)


async def get_oldest_movie() -> dict | None:
    movies = await _get_movies()
    candidates = [m for m in movies if m.year]
    if not candidates:
        return None
    oldest = min(candidates, key=lambda m: m.year)
    return _movie_to_dict(oldest)


async def get_newest_movie() -> dict | None:
    """Most recently *added* to the library."""
    movies = await _get_movies()
    candidates = [m for m in movies if m.addedAt]
    if not candidates:
        return None
    newest = max(candidates, key=lambda m: m.addedAt)
    return _movie_to_dict(newest)


async def get_total_runtime() -> dict:
    """Total runtime + a fun breakdown of how long it'd take to watch."""
    movies = await _get_movies()
    total_minutes = sum(int(m.duration / 60000) for m in movies if m.duration)

    days = total_minutes / 1440
    hours = total_minutes / 60
    weeks = days / 7

    # If you watched 8h/day, how many days would it take?
    realistic_days = total_minutes / (8 * 60)

    return {
        "count": len(movies),
        "total_minutes": total_minutes,
        "total_hours": hours,
        "total_days": days,
        "total_weeks": weeks,
        "realistic_days_at_8h": realistic_days,
    }


async def get_decade_breakdown() -> list[tuple[str, int]]:
    """Returns [(decade_label, count), ...] sorted by decade ascending."""
    movies = await _get_movies()
    decades: dict[int, int] = {}
    for m in movies:
        if m.year:
            decade = (m.year // 10) * 10
            decades[decade] = decades.get(decade, 0) + 1

    sorted_decades = sorted(decades.items())
    return [(f"{d}s", count) for d, count in sorted_decades]


async def get_genre_breakdown(top_n: int = 10) -> list[tuple[str, int]]:
    """Returns the top N genres by count."""
    movies = await _get_movies()
    genres: dict[str, int] = {}
    for m in movies:
        for g in (m.genres or []):
            name = g.tag
            genres[name] = genres.get(name, 0) + 1

    sorted_genres = sorted(genres.items(), key=lambda x: x[1], reverse=True)
    return sorted_genres[:top_n]


async def get_random_scene() -> dict | None:
    """
    Pick a random film and a random backdrop from it.
    Returns dict with title, year, art_url, fanart_url (if multiple available).
    """
    movies = await _get_movies()
    # Filter to films that have art
    candidates = [m for m in movies if m.art]
    if not candidates:
        return None

    # Try a few times to find one with art that loads
    for _ in range(5):
        movie = random.choice(candidates)
        art_url = _absolute_url(movie.art)
        if art_url:
            return {
                "title": movie.title,
                "year": movie.year,
                "art_url": art_url,
                "thumb_url": _absolute_url(movie.thumb),
                "summary": movie.summary or "",
            }
    return None


def force_refresh_cache() -> None:
    global _movies_cache, _cache_age
    _movies_cache = None
    _cache_age = 0