"""
Plex integration: connects to your Plex server and provides random movie picking
+ library stats.

Performance notes:
- plexapi is synchronous, so network-heavy calls run in a worker thread.
- The full library is cached and guarded by one refresh lock so concurrent
  commands do not stampede Plex with duplicate library scans.
- Common lookup data is precomputed during refresh for fast title checks/stats.
"""
from __future__ import annotations

import asyncio
import random
import time
from collections import Counter
from datetime import datetime
from typing import Any

from plexapi.exceptions import NotFound, Unauthorized
from plexapi.myplex import MyPlexAccount

import config
import db


CACHE_TTL_SECONDS = 3600
CONNECT_TIMEOUT_SECONDS = 15

_account: MyPlexAccount | None = None
_server: Any | None = None
_library: Any | None = None
_movies_cache: list[dict] | None = None
_movie_dict_cache: list[dict] | None = None
_title_index: dict[str, list[dict]] = {}
_cache_age: float = 0
_refresh_lock = asyncio.Lock()


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
    except Unauthorized as exc:
        raise PlexError("Plex token is invalid or expired") from exc
    except Exception as exc:
        raise PlexError(f"Couldn't authenticate with Plex: {exc}") from exc

    resources = [r for r in _account.resources() if r.owned and "server" in r.provides]
    if not resources:
        raise PlexError("No Plex servers found on this account")

    try:
        _server = resources[0].connect(timeout=CONNECT_TIMEOUT_SECONDS)
    except Exception as exc:
        raise PlexError(f"Couldn't connect to Plex server: {exc}") from exc

    try:
        _library = _server.library.section(config.PLEX_LIBRARY)
    except NotFound as exc:
        available = ", ".join(s.title for s in _server.library.sections())
        raise PlexError(
            f"Library '{config.PLEX_LIBRARY}' not found. Available: {available}"
        ) from exc


async def _connect() -> None:
    await asyncio.to_thread(_connect_sync)


def _datetime_to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _plex_timestamp_to_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value)).isoformat()
    except (TypeError, ValueError, OSError):
        return value


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _date_sort_key(value: str | None) -> datetime:
    return _parse_datetime(value) or datetime.min


def _refresh_cache_sync() -> list[dict]:
    if _library is None:
        raise PlexError("Not connected to Plex")

    movies: list[dict] = []
    offset = 0
    page_size = 500
    total_size = None
    while total_size is None or offset < total_size:
        root = _server.query(
            f"/library/sections/{_library.key}/all"
            f"?type=1&includeCollections=1"
            f"&X-Plex-Container-Start={offset}"
            f"&X-Plex-Container-Size={page_size}"
        )
        if total_size is None:
            total_size = int(root.attrib.get("totalSize") or 0)
        videos = root.findall("Video")
        if not videos:
            break
        movies.extend(_movie_element_to_dict(video) for video in videos)
        offset += len(videos)
    return movies


def _refresh_incremental_cache_sync() -> list[dict]:
    if _library is None:
        raise PlexError("Not connected to Plex")

    max_added_at, max_updated_at = db.get_plex_library_cache_watermarks()
    changed: dict[str, dict] = {}

    if max_added_at:
        added_since = _parse_datetime(max_added_at) or max_added_at
        for movie in _library.search(
            filters={"addedAt>>": added_since},
            sort="addedAt:asc",
        ):
            serialized = _movie_to_dict(movie)
            changed[serialized["rating_key"]] = serialized

    if max_updated_at:
        updated_since = _parse_datetime(max_updated_at) or max_updated_at
        for movie in _library.search(
            filters={"updatedAt>>": updated_since},
            sort="updatedAt:asc",
        ):
            serialized = _movie_to_dict(movie)
            changed[serialized["rating_key"]] = serialized

    if not max_added_at and not max_updated_at:
        return _refresh_cache_sync()

    return list(changed.values())


def _absolute_url(relative: str | None) -> str | None:
    """Convert a relative Plex image path to a full URL with auth token."""
    if not relative or _server is None:
        return None
    return f"{_server._baseurl}{relative}?X-Plex-Token={config.PLEX_TOKEN}"


def _tag_values(video: Any, tag: str) -> list[str]:
    return [
        child.attrib["tag"]
        for child in video.findall(tag)
        if child.attrib.get("tag")
    ]


def _movie_element_to_dict(video: Any) -> dict:
    duration = video.attrib.get("duration")
    audience_rating = video.attrib.get("audienceRating")
    year = video.attrib.get("year")
    thumb_path = video.attrib.get("thumb")
    art_path = video.attrib.get("art")
    return {
        "rating_key": str(video.attrib.get("ratingKey")),
        "title": video.attrib.get("title") or "Unknown",
        "year": int(year) if year and year.isdigit() else None,
        "summary": video.attrib.get("summary") or "",
        "thumb_path": thumb_path,
        "art_path": art_path,
        "thumb_url": _absolute_url(thumb_path),
        "art_url": _absolute_url(art_path),
        "duration_minutes": int(int(duration) / 60000) if duration else None,
        "rating": video.attrib.get("contentRating") or None,
        "audience_rating": float(audience_rating) if audience_rating else None,
        "added_at": _plex_timestamp_to_iso(video.attrib.get("addedAt")),
        "updated_at": _plex_timestamp_to_iso(video.attrib.get("updatedAt")),
        "genres": _tag_values(video, "Genre"),
        "directors": _tag_values(video, "Director"),
        "writers": _tag_values(video, "Writer"),
        "actors": _tag_values(video, "Role"),
        "countries": _tag_values(video, "Country"),
        "collections": _tag_values(video, "Collection"),
    }


def _movie_to_dict(movie: Any) -> dict:
    """Serialize a Plex movie object into a plain dict."""
    thumb_path = movie.thumb
    art_path = movie.art
    tag_values = lambda values: [item.tag for item in (values or []) if getattr(item, "tag", None)]
    return {
        "rating_key": str(movie.ratingKey),
        "title": movie.title,
        "year": movie.year,
        "summary": movie.summary or "",
        "thumb_path": thumb_path,
        "art_path": art_path,
        "thumb_url": _absolute_url(thumb_path),
        "art_url": _absolute_url(art_path),
        "duration_minutes": int(movie.duration / 60000) if movie.duration else None,
        "rating": movie.contentRating or None,
        "audience_rating": movie.audienceRating,
        "added_at": _datetime_to_iso(movie.addedAt),
        "updated_at": _datetime_to_iso(movie.updatedAt),
        "genres": [g.tag for g in (movie.genres or [])],
        "directors": tag_values(getattr(movie, "directors", [])),
        "writers": tag_values(getattr(movie, "writers", [])),
        "actors": tag_values(getattr(movie, "roles", [])) or tag_values(getattr(movie, "actors", [])),
        "countries": tag_values(getattr(movie, "countries", [])),
        "collections": tag_values(getattr(movie, "collections", [])),
    }


def _hydrate_cached_movie(movie: dict) -> dict:
    hydrated = dict(movie)
    hydrated["rating_key"] = str(hydrated["rating_key"])
    hydrated["thumb_url"] = _absolute_url(hydrated.get("thumb_path"))
    hydrated["art_url"] = _absolute_url(hydrated.get("art_path"))
    return hydrated


def _normalize_title(s: str) -> str:
    """Normalize a title for fuzzy matching: lowercase, strip articles & punctuation."""
    s = (s or "").lower().strip()
    for article in ("the ", "a ", "an "):
        if s.startswith(article):
            s = s[len(article):]
    return "".join(c for c in s if c.isalnum())


def _rebuild_indexes() -> None:
    global _movie_dict_cache, _title_index

    movies = _movies_cache or []
    _movie_dict_cache = movies

    title_index: dict[str, list[dict]] = {}
    for movie in _movie_dict_cache:
        key = _normalize_title(movie.get("title", ""))
        if key:
            title_index.setdefault(key, []).append(movie)
    _title_index = title_index


def _load_persistent_cache() -> list[dict]:
    return [_hydrate_cached_movie(movie) for movie in db.get_plex_library_cache()]


def _needs_metadata_backfill(movies: list[dict]) -> bool:
    return bool(movies) and not any(
        movie.get("directors")
        or movie.get("actors")
        or movie.get("countries")
        or movie.get("collections")
        for movie in movies
    )


async def refresh_full_cache() -> list[dict]:
    """Fully refresh the Plex cache and replace the persisted snapshot."""
    global _movies_cache, _cache_age

    async with _refresh_lock:
        await _connect()
        movies = await asyncio.to_thread(_refresh_cache_sync)
        db.replace_plex_library_cache(movies)
        _movies_cache = [_hydrate_cached_movie(movie) for movie in movies]
        _cache_age = time.time()
        _rebuild_indexes()
        print(f"[plex] Full library cache refreshed: {len(movies)} movies")
        return _movies_cache


async def refresh_incremental_cache() -> list[dict]:
    """Refresh only Plex items added or updated since the persisted snapshot."""
    async with _refresh_lock:
        return await _refresh_incremental_cache_unlocked()


async def _get_movies() -> list[dict]:
    """Get the movie list from memory, then the persisted snapshot, then Plex."""
    global _movies_cache, _cache_age

    now = time.time()
    if _movies_cache is not None and (now - _cache_age) <= CACHE_TTL_SECONDS:
        return _movies_cache

    async with _refresh_lock:
        # Another coroutine may have populated the cache while this one waited.
        now = time.time()
        if _movies_cache is not None and (now - _cache_age) <= CACHE_TTL_SECONDS:
            return _movies_cache

        movies = _load_persistent_cache()
        if movies:
            _movies_cache = movies
            _cache_age = time.time()
            _rebuild_indexes()
            return movies

    return await refresh_incremental_cache()


async def refresh_incremental_cache_if_stale() -> list[dict]:
    now = time.time()
    if _movies_cache is not None and (now - _cache_age) <= CACHE_TTL_SECONDS:
        return _movies_cache
    return await refresh_incremental_cache()


async def _refresh_incremental_cache_unlocked() -> list[dict]:
    global _movies_cache, _cache_age

    existing = _load_persistent_cache()
    if not existing or _needs_metadata_backfill(existing):
        await _connect()
        movies = await asyncio.to_thread(_refresh_cache_sync)
        db.replace_plex_library_cache(movies)
        _movies_cache = [_hydrate_cached_movie(movie) for movie in movies]
        _cache_age = time.time()
        _rebuild_indexes()
        print(f"[plex] Full library cache refreshed: {len(movies)} movies")
        return _movies_cache

    await _connect()
    changed = await asyncio.to_thread(_refresh_incremental_cache_sync)
    if changed:
        db.upsert_plex_library_cache(changed)

    movies = _load_persistent_cache()
    _movies_cache = movies
    _cache_age = time.time()
    _rebuild_indexes()
    print(
        "[plex] Incremental library cache refreshed: "
        f"{len(changed)} changed, {len(movies)} cached"
    )
    return movies


async def _get_movie_dicts() -> list[dict]:
    await _get_movies()
    return _movie_dict_cache or []


async def warm_cache() -> None:
    """Refresh Plex in the background so the first /rb9 command is fast."""
    if not config.PLEX_TOKEN:
        return
    await refresh_incremental_cache()


# ---------- random pick (existing /rb9) ----------

async def pick_random_movie() -> dict | None:
    """Pick a random movie from the library."""
    movies = await _get_movie_dicts()
    if not movies:
        return None
    return random.choice(movies)


# ---------- rental pick ----------

async def pick_random_for_rental(exclude_keys: set[str]) -> dict | None:
    """Pick a random movie from the library, excluding rating keys."""
    movies = await _get_movie_dicts()
    candidates = [m for m in movies if m["rating_key"] not in exclude_keys]
    if not candidates:
        return None
    return random.choice(candidates)


# ---------- title lookup ----------

async def find_movie_by_title(title: str, year: int | None = None) -> dict | None:
    """
    Look up a movie in the Plex library by title and optional year.
    Uses the precomputed normalized title index instead of scanning every movie.
    """
    await _get_movies()
    needle = _normalize_title(title)
    if not needle:
        return None

    matches = _title_index.get(needle, [])
    if year is not None:
        for movie in matches:
            if movie.get("year") == year:
                return movie
    return matches[0] if matches else None


async def check_availability(title: str | None, year: int | None = None) -> bool | None:
    """
    Safe wrapper for orchestration code.
    Returns True/False for found/missing, None if Plex is unavailable.
    """
    if not config.PLEX_TOKEN or not title:
        return None
    try:
        return await find_movie_by_title(title, year=year) is not None
    except Exception:
        return None


# ---------- stats commands ----------

async def get_library_summary() -> dict:
    """Overall library stats: count, total runtime, oldest, newest, avg rating."""
    movies = await _get_movie_dicts()
    if not movies:
        return {"count": 0}

    total_minutes = sum(m.get("duration_minutes") or 0 for m in movies)
    years = [m["year"] for m in movies if m.get("year")]
    ratings = [m["audience_rating"] for m in movies if m.get("audience_rating") is not None]

    oldest = min(movies, key=lambda m: m.get("year") or 9999)
    newest_by_year = max(movies, key=lambda m: m.get("year") or 0)
    newest_added = max(movies, key=lambda m: _date_sort_key(m.get("added_at")))

    return {
        "count": len(movies),
        "total_minutes": total_minutes,
        "oldest": oldest,
        "newest_by_year": newest_by_year,
        "newest_added": newest_added,
        "avg_rating": sum(ratings) / len(ratings) if ratings else None,
        "rated_count": len(ratings),
        "min_year": min(years) if years else None,
        "max_year": max(years) if years else None,
    }


async def get_longest_movie() -> dict | None:
    movies = await _get_movie_dicts()
    candidates = [m for m in movies if m.get("duration_minutes")]
    return max(candidates, key=lambda m: m["duration_minutes"]) if candidates else None


async def get_shortest_movie() -> dict | None:
    movies = await _get_movie_dicts()
    candidates = [m for m in movies if m.get("duration_minutes") and m["duration_minutes"] > 30]
    if not candidates:
        candidates = [m for m in movies if m.get("duration_minutes")]
    return min(candidates, key=lambda m: m["duration_minutes"]) if candidates else None


async def get_oldest_movie() -> dict | None:
    movies = await _get_movie_dicts()
    candidates = [m for m in movies if m.get("year")]
    return min(candidates, key=lambda m: m["year"]) if candidates else None


async def get_newest_movie() -> dict | None:
    """Most recently added to the library."""
    movies = await _get_movie_dicts()
    candidates = [m for m in movies if m.get("added_at")]
    return max(candidates, key=lambda m: _date_sort_key(m.get("added_at"))) if candidates else None


async def get_total_runtime() -> dict:
    """Total runtime + a fun breakdown of how long it'd take to watch."""
    movies = await _get_movie_dicts()
    total_minutes = sum(m.get("duration_minutes") or 0 for m in movies)
    days = total_minutes / 1440
    hours = total_minutes / 60
    weeks = days / 7
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
    movies = await _get_movie_dicts()
    decades: Counter[int] = Counter()
    for movie in movies:
        year = movie.get("year")
        if year:
            decades[(year // 10) * 10] += 1
    return [(f"{decade}s", count) for decade, count in sorted(decades.items())]


async def get_genre_breakdown(top_n: int = 10) -> list[tuple[str, int]]:
    """Returns the top N genres by count."""
    movies = await _get_movie_dicts()
    genres: Counter[str] = Counter()
    for movie in movies:
        genres.update(movie.get("genres") or [])
    return genres.most_common(top_n)


async def get_random_scene() -> dict | None:
    """Pick a random film with a backdrop/art image."""
    movies = await _get_movie_dicts()
    candidates = [m for m in movies if m.get("art_url")]
    if not candidates:
        return None
    movie = random.choice(candidates)
    return {
        "title": movie["title"],
        "year": movie.get("year"),
        "art_url": movie.get("art_url"),
        "thumb_url": movie.get("thumb_url"),
        "summary": movie.get("summary") or "",
    }


def force_refresh_cache() -> None:
    global _movies_cache, _movie_dict_cache, _title_index, _cache_age
    _movies_cache = None
    _movie_dict_cache = None
    _title_index = {}
    _cache_age = 0
