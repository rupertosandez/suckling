from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import aiohttp

import cache
import config

BASE_URL = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

RELEASE_TYPE_PREMIERE = 1
RELEASE_TYPE_LIMITED_THEATRICAL = 2
RELEASE_TYPE_THEATRICAL = 3
RELEASE_TYPE_DIGITAL = 4
RELEASE_TYPE_PHYSICAL = 5
RELEASE_TYPE_TV = 6

# TMDB allows much higher throughput than this, but keeping a local cap prevents
# command bursts from turning into noisy request spikes.
MAX_CONCURRENT_REQUESTS = 8
DEFAULT_CACHE_TTL_SECONDS = 6 * 3600
SEARCH_CACHE_TTL_SECONDS = 24 * 3600
PROVIDER_CACHE_TTL_SECONDS = 30 * 60


class TMDBError(Exception):
    pass


_session: aiohttp.ClientSession | None = None
_request_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
_inflight: dict[str, asyncio.Task[dict[str, Any]]] = {}
_inflight_lock = asyncio.Lock()


def get_session() -> aiohttp.ClientSession:
    """
    Return a module-level aiohttp session, creating it lazily on first call.

    The connector enables keep-alive pooling and DNS caching. Reusing one
    session is noticeably faster than opening a new connection for every TMDB
    image/API request.
    """
    global _session
    if _session is None or _session.closed:
        timeout = aiohttp.ClientTimeout(total=30, sock_connect=10, sock_read=20)
        connector = aiohttp.TCPConnector(
            limit=32,
            limit_per_host=MAX_CONCURRENT_REQUESTS,
            ttl_dns_cache=300,
            enable_cleanup_closed=True,
        )
        _session = aiohttp.ClientSession(timeout=timeout, connector=connector)
    return _session


async def close_session() -> None:
    """Close the shared session. Called on bot shutdown."""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


def _cache_key(path: str, params: dict[str, Any] | None) -> str:
    params = params or {}
    bits = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return f"tmdb:{path}?{bits}"


async def _request_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    params = dict(params or {})
    params["api_key"] = config.TMDB_API_KEY
    url = f"{BASE_URL}{path}"
    session = get_session()

    async with _request_semaphore:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with session.get(url, params=params) as resp:
                    if resp.status == 200:
                        return await resp.json()

                    if resp.status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        delay = float(retry_after) if retry_after else 1.0 + attempt
                        await asyncio.sleep(delay)
                        continue

                    if 500 <= resp.status < 600 and attempt < 2:
                        await asyncio.sleep(0.5 * (attempt + 1))
                        continue

                    text = await resp.text()
                    raise TMDBError(f"TMDB returned {resp.status} for {path}: {text[:200]}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = e
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                raise TMDBError(f"TMDB request failed for {path}: {e}") from e

        raise TMDBError(f"TMDB request failed for {path}: {last_error}")


async def _get(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    ttl_seconds: float | None = DEFAULT_CACHE_TTL_SECONDS,
    force: bool = False,
) -> dict[str, Any]:
    """
    Make a GET request to TMDB and return JSON.

    Adds two performance wins:
    - TTL caching for repeated calls.
    - in-flight request de-duping so simultaneous commands asking for the same
      movie wait on one network call instead of firing duplicates.
    """
    key = _cache_key(path, params)
    if ttl_seconds is not None and not force:
        cached = cache.get(key)
        if cached is not None:
            return cached

    async with _inflight_lock:
        task = _inflight.get(key)
        if task is None:
            task = asyncio.create_task(_request_json(path, params))
            _inflight[key] = task

    try:
        data = await task
    finally:
        async with _inflight_lock:
            if _inflight.get(key) is task:
                _inflight.pop(key, None)

    if ttl_seconds is not None:
        cache.set(key, data, ttl_seconds=ttl_seconds)
    return data


async def search_movie(query: str, year: int | None = None) -> list[dict]:
    """Search for movies by title, sorted by popularity descending."""
    params: dict[str, Any] = {"query": query}
    if year is not None:
        params["year"] = year
    data = await _get("/search/movie", params, ttl_seconds=SEARCH_CACHE_TTL_SECONDS)
    results = data.get("results", [])
    results.sort(key=lambda r: r.get("popularity", 0), reverse=True)
    return results


async def search_person(query: str) -> list[dict]:
    """Search for people by name, sorted by popularity."""
    data = await _get("/search/person", {"query": query}, ttl_seconds=SEARCH_CACHE_TTL_SECONDS)
    results = data.get("results", [])
    results.sort(key=lambda r: r.get("popularity", 0), reverse=True)
    return results


async def discover_movies(page: int = 1, **params: Any) -> list[dict]:
    """Fetch a TMDB Discover page. Useful for picker/tracker batching."""
    request_params = {"page": page, "include_adult": "false", **params}
    data = await _get("/discover/movie", request_params, ttl_seconds=24 * 3600)
    return data.get("results", [])


async def get_movie_details(movie_id: int, force: bool = False) -> dict:
    """Get full details for a movie. Cached for 6 hours unless force=True."""
    return await _get(
        f"/movie/{movie_id}",
        {"append_to_response": "credits,release_dates"},
        ttl_seconds=DEFAULT_CACHE_TTL_SECONDS,
        force=force,
    )


async def get_movie_cast(movie_id: int, force: bool = False) -> list[dict]:
    """Get the cast list for a movie. Cached for 6 hours unless force=True."""
    data = await _get(
        f"/movie/{movie_id}/credits",
        ttl_seconds=DEFAULT_CACHE_TTL_SECONDS,
        force=force,
    )
    return data.get("cast", [])


async def get_popular_people(page: int = 1) -> list[dict]:
    """Get a page of popular people."""
    data = await _get("/person/popular", {"page": page}, ttl_seconds=24 * 3600)
    return data.get("results", [])


async def get_watch_providers(movie_id: int, region: str = "US", force: bool = False) -> dict:
    """
    Get watch providers for a movie.

    Provider data changes more often than movie details, so the default TTL is
    shorter. Scheduled tracker checks can still pass force=True for fresh data.
    """
    data = await _get(
        f"/movie/{movie_id}/watch/providers",
        ttl_seconds=PROVIDER_CACHE_TTL_SECONDS,
        force=force,
    )
    return data.get("results", {}).get(region, {})


async def get_movie_keywords(movie_id: int, force: bool = False) -> list[str]:
    """Get TMDB keywords for a movie. Cached for 6 hours."""
    data = await _get(
        f"/movie/{movie_id}/keywords",
        ttl_seconds=DEFAULT_CACHE_TTL_SECONDS,
        force=force,
    )
    return [kw["name"].lower() for kw in data.get("keywords", [])]


async def get_movie_images(movie_id: int, force: bool = False) -> dict:
    """Get all images for a movie. Cached for 6 hours."""
    return await _get(
        f"/movie/{movie_id}/images",
        ttl_seconds=DEFAULT_CACHE_TTL_SECONDS,
        force=force,
    )


def pick_backdrop_url(images: dict) -> str | None:
    """Pick a high-quality backdrop URL, filtering language-tagged ones."""
    import random as _random

    backdrops = images.get("backdrops", []) or []
    clean = [b for b in backdrops if not b.get("iso_639_1")]
    pool = clean if clean else backdrops
    if not pool:
        return None

    pool.sort(key=lambda b: b.get("vote_average", 0), reverse=True)
    top_half = pool[: max(3, len(pool) // 2)]
    chosen = _random.choice(top_half)
    file_path = chosen.get("file_path")
    if not file_path:
        return None
    return f"{IMAGE_BASE}{file_path}"


def poster_url(poster_path: str | None) -> str | None:
    if not poster_path:
        return None
    return f"{IMAGE_BASE}{poster_path}"


def get_director(details: dict) -> str | None:
    crew = details.get("credits", {}).get("crew", [])
    directors = [person["name"] for person in crew if person.get("job") == "Director"]
    return ", ".join(directors) if directors else None


def _parse_tmdb_date(date_str: str) -> datetime | None:
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _release_dates_for_region(details: dict, region: str = "US") -> list[dict]:
    release_dates = details.get("release_dates", {}).get("results", [])
    for entry in release_dates:
        if entry.get("iso_3166_1") == region:
            return entry.get("release_dates", [])
    return []


def get_theatrical_date(details: dict, region: str = "US") -> datetime | None:
    region_releases = _release_dates_for_region(details, region)
    theatrical = [
        r for r in region_releases
        if r.get("type") in (RELEASE_TYPE_THEATRICAL, RELEASE_TYPE_LIMITED_THEATRICAL, RELEASE_TYPE_PREMIERE)
    ]
    dates = [_parse_tmdb_date(r.get("release_date", "")) for r in theatrical]
    dates = [d for d in dates if d is not None]
    if dates:
        return min(dates)
    return _parse_tmdb_date(details.get("release_date", ""))


def get_digital_date(details: dict, region: str = "US") -> datetime | None:
    region_releases = _release_dates_for_region(details, region)
    digital = [r for r in region_releases if r.get("type") == RELEASE_TYPE_DIGITAL]
    dates = [_parse_tmdb_date(r.get("release_date", "")) for r in digital]
    dates = [d for d in dates if d is not None]
    return min(dates) if dates else None


def is_in_theaters(details: dict, region: str = "US") -> bool:
    theatrical = get_theatrical_date(details, region)
    if not theatrical:
        return False
    days_since_release = (datetime.now() - theatrical).days
    return 0 <= days_since_release <= 90