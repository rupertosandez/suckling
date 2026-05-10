from datetime import datetime

import aiohttp

import config
import cache

BASE_URL = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

RELEASE_TYPE_PREMIERE = 1
RELEASE_TYPE_LIMITED_THEATRICAL = 2
RELEASE_TYPE_THEATRICAL = 3
RELEASE_TYPE_DIGITAL = 4
RELEASE_TYPE_PHYSICAL = 5
RELEASE_TYPE_TV = 6


class TMDBError(Exception):
    pass


async def _get(session: aiohttp.ClientSession, path: str, params: dict | None = None) -> dict:
    """Make a GET request to TMDB and return the JSON."""
    params = params or {}
    params["api_key"] = config.TMDB_API_KEY
    url = f"{BASE_URL}{path}"
    async with session.get(url, params=params) as resp:
        if resp.status != 200:
            raise TMDBError(f"TMDB returned {resp.status} for {path}")
        return await resp.json()


async def search_movie(query: str, year: int | None = None) -> list[dict]:
    """Search for movies by title, sorted by popularity (descending)."""
    async with aiohttp.ClientSession() as session:
        params = {"query": query}
        if year is not None:
            params["year"] = year
        data = await _get(session, "/search/movie", params)
        results = data.get("results", [])
        results.sort(key=lambda r: r.get("popularity", 0), reverse=True)
        return results


async def search_person(query: str) -> list[dict]:
    """Search for people (actors, directors, etc) by name, sorted by popularity."""
    async with aiohttp.ClientSession() as session:
        data = await _get(session, "/search/person", {"query": query})
        results = data.get("results", [])
        results.sort(key=lambda r: r.get("popularity", 0), reverse=True)
        return results


async def get_movie_details(movie_id: int, force: bool = False) -> dict:
    """Get full details for a movie. Cached for 6 hours unless force=True."""
    cache_key = f"details:{movie_id}"
    if not force:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    async with aiohttp.ClientSession() as session:
        data = await _get(
            session,
            f"/movie/{movie_id}",
            {"append_to_response": "credits,release_dates"},
        )
    cache.set(cache_key, data)
    return data


async def get_movie_cast(movie_id: int, force: bool = False) -> list[dict]:
    """
    Get the cast list for a movie. Returns list of dicts with 'id', 'name', 'character'.
    Cached for 6 hours unless force=True.
    """
    cache_key = f"cast:{movie_id}"
    if not force:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    async with aiohttp.ClientSession() as session:
        data = await _get(session, f"/movie/{movie_id}/credits")
        cast = data.get("cast", [])
    cache.set(cache_key, cast)
    return cast


async def get_popular_people(page: int = 1) -> list[dict]:
    """Get a page of popular people (~20 per page)."""
    async with aiohttp.ClientSession() as session:
        data = await _get(session, "/person/popular", {"page": page})
        return data.get("results", [])


async def get_watch_providers(movie_id: int, region: str = "US", force: bool = False) -> dict:
    """Get watch providers for a movie. Cached for 6 hours unless force=True."""
    cache_key = f"providers:{movie_id}:{region}"
    if not force:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    async with aiohttp.ClientSession() as session:
        data = await _get(session, f"/movie/{movie_id}/watch/providers")
        results = data.get("results", {})
        region_data = results.get(region, {})
    cache.set(cache_key, region_data)
    return region_data


async def get_movie_keywords(movie_id: int, force: bool = False) -> list[str]:
    """Get TMDB keywords for a movie. Cached for 6 hours."""
    cache_key = f"keywords:{movie_id}"
    if not force:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    async with aiohttp.ClientSession() as session:
        data = await _get(session, f"/movie/{movie_id}/keywords")
        keywords = [kw["name"].lower() for kw in data.get("keywords", [])]
    cache.set(cache_key, keywords)
    return keywords


async def get_movie_images(movie_id: int, force: bool = False) -> dict:
    """Get all images for a movie. Cached for 6 hours."""
    cache_key = f"images:{movie_id}"
    if not force:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    async with aiohttp.ClientSession() as session:
        data = await _get(session, f"/movie/{movie_id}/images")
    cache.set(cache_key, data)
    return data


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