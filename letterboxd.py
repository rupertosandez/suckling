"""
Letterboxd RSS and public watchlist integration.

Provides async helpers to:
  - Parse a Letterboxd user's diary feed (recent watches)
  - Parse a Letterboxd user's watchlist feed
  - Validate that a Letterboxd username is public and reachable

All responses are cached with a 15-minute TTL using the shared cache module.
HTTP requests reuse a shared aiohttp session so feed checks keep connections warm.
"""

import asyncio
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from html import unescape

import aiohttp

import cache


_LB_DIARY_URL = "https://letterboxd.com/{username}/rss/"
_LB_WATCHLIST_URL = "https://letterboxd.com/{username}/watchlist/rss/"
_LB_WATCHLIST_PAGE_URL = "https://letterboxd.com/{username}/watchlist/"
_LB_WATCHLIST_PAGE_N_URL = "https://letterboxd.com/{username}/watchlist/page/{page}/"

# Letterboxd uses this namespace for custom RSS tags
_LB_NS = "https://a.ltrbxd.com/legal/letterboxd-terms-of-service"
# Yahoo media namespace for thumbnails
_MEDIA_NS = "http://search.yahoo.com/mrss/"

_CACHE_TTL = 15 * 60  # 15 minutes
_USER_AGENT = "sucklingbot/2.0 (discord bot; rss reader)"
_session: aiohttp.ClientSession | None = None


class LetterboxdError(Exception):
    pass


def get_session() -> aiohttp.ClientSession:
    """Return a shared Letterboxd HTTP session with keep-alive enabled."""
    global _session
    if _session is None or _session.closed:
        timeout = aiohttp.ClientTimeout(total=10)
        connector = aiohttp.TCPConnector(limit=10, limit_per_host=4, ttl_dns_cache=300)
        _session = aiohttp.ClientSession(timeout=timeout, connector=connector)
    return _session


async def close_session() -> None:
    """Close the shared session on bot shutdown/restart."""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


# ---------- cache keys ----------

def _lb_diary_key(username: str) -> str:
    return f"lb:diary:{username.lower()}"


def _lb_watchlist_key(username: str) -> str:
    return f"lb:watchlist:{username.lower()}"


# ---------- HTTP ----------

async def _fetch_text(url: str, forbidden_error: str = "private") -> str:
    """Fetch raw text from a URL. Raises LetterboxdError on failure."""
    headers = {"User-Agent": _USER_AGENT}
    try:
        session = get_session()
        async with session.get(url, headers=headers) as resp:
            if resp.status == 404:
                raise LetterboxdError("not_found")
            if resp.status == 403:
                raise LetterboxdError(forbidden_error)
            if resp.status != 200:
                raise LetterboxdError(f"http_{resp.status}")
            return await resp.text()
    except LetterboxdError:
        raise
    except asyncio.TimeoutError as e:
        raise LetterboxdError("network timeout") from e
    except aiohttp.ClientError as e:
        raise LetterboxdError(f"network error: {e}") from e


async def _fetch_rss(url: str) -> str:
    """Fetch raw RSS XML from a URL. Raises LetterboxdError on failure."""
    return await _fetch_text(url, forbidden_error="private")


# ---------- parsing helpers ----------

def _lb_tag(item: ET.Element, local_name: str) -> str | None:
    """Find a Letterboxd-namespaced tag value on an RSS item."""
    el = item.find(f"{{{_LB_NS}}}{local_name}")
    return el.text.strip() if el is not None and el.text else None


def _media_thumb(item: ET.Element) -> str | None:
    """Extract a media:thumbnail URL from an RSS item."""
    el = item.find(f"{{{_MEDIA_NS}}}thumbnail")
    return el.get("url") if el is not None else None


def _description_image(html: str) -> str | None:
    """Extract the first image URL from an RSS description."""
    match = re.search(r"<img\b[^>]*>", html, flags=re.IGNORECASE)
    if not match:
        return None
    return _html_attr(match.group(0), "src")


def _plain_text(html: str) -> str:
    """Strip HTML tags from a string."""
    return re.sub(r"<[^>]+>", "", html).strip()


def _rss_channel(xml_text: str) -> ET.Element | None:
    """Return the RSS channel element, or raise on malformed XML."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise LetterboxdError(f"xml parse error: {e}") from e
    return root.find("channel")


def _rss_datetime(item: ET.Element, tag_name: str) -> str | None:
    el = item.find(tag_name)
    if el is None or not el.text:
        return None
    try:
        parsed = parsedate_to_datetime(el.text.strip())
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.isoformat()
    return parsed.astimezone().isoformat()


def _html_attr(tag: str, attr_name: str) -> str | None:
    match = re.search(rf'{attr_name}="([^"]*)"', tag)
    return unescape(match.group(1)).strip() if match else None


def _rating_to_stars(rating: float | None) -> str:
    """Convert a decimal rating (0-5, 0.5 increments) to star characters."""
    if rating is None:
        return ""
    full = int(rating)
    half = (rating % 1) >= 0.5
    return "★" * full + ("½" if half else "")


# ---------- diary feed ----------

def _parse_diary_xml(xml_text: str) -> list[dict]:
    """Parse a Letterboxd diary RSS feed into a list of entry dicts."""
    channel = _rss_channel(xml_text)
    if channel is None:
        return []

    entries = []
    for item in channel.findall("item"):
        title_el = item.find("title")
        raw_title = (title_el.text or "").strip()

        # LB title format: "Film Name, YYYY - ★★★½" or "Film Name, YYYY"
        # Strip rating suffix first
        film_title = re.sub(r"\s*-\s*[★½].*$", "", raw_title).strip()
        # Strip ", YYYY" suffix
        film_title = re.sub(r",\s*\d{4}$", "", film_title).strip()

        year_str = _lb_tag(item, "filmYear")
        year = int(year_str) if year_str and year_str.isdigit() else None

        rating_str = _lb_tag(item, "memberRating")
        try:
            rating = float(rating_str) if rating_str else None
        except ValueError:
            rating = None

        watch_date = _lb_tag(item, "watchedDate") or ""
        rewatch = _lb_tag(item, "rewatch") == "Yes"

        link_el = item.find("link")
        link = (link_el.text or "").strip() if link_el is not None else ""

        desc_el = item.find("description")
        desc_html = (desc_el.text or "") if desc_el is not None else ""
        thumb = _media_thumb(item) or _description_image(desc_html)
        review = _plain_text(desc_html)
        if len(review) > 150:
            review = review[:147].rstrip() + "..."

        entries.append({
            "film_title": film_title,
            "year": year,
            "rating": rating,
            "stars": _rating_to_stars(rating),
            "watch_date": watch_date,
            "published_at": _rss_datetime(item, "pubDate"),
            "rewatch": rewatch,
            "link": link,
            "thumb": thumb,
            "review": review or None,
        })

    return entries


# ---------- watchlist feed ----------

def _parse_watchlist_xml(xml_text: str) -> list[dict]:
    """
    Parse a Letterboxd watchlist RSS feed into film dicts.
    LB uses letterboxd:filmYear for the year.
    """
    channel = _rss_channel(xml_text)
    if channel is None:
        return []

    films = []
    for item in channel.findall("item"):
        title_el = item.find("title")
        raw_title = (title_el.text or "").strip()

        # Try LB namespace year first
        year_str = _lb_tag(item, "filmYear")
        year = int(year_str) if year_str and year_str.isdigit() else None

        # Fall back to "(YYYY)" in title if the feed omits the LB year tag.
        if year is None:
            m = re.search(r"\((\d{4})\)\s*$", raw_title)
            if m:
                year = int(m.group(1))
                raw_title = raw_title[: m.start()].strip()

        link_el = item.find("link")
        link = (link_el.text or "").strip() if link_el is not None else ""

        thumb = _media_thumb(item)

        films.append({
            "film_title": raw_title,
            "year": year,
            "link": link,
            "thumb": thumb,
        })

    return films


def _parse_watchlist_page(html_text: str) -> list[dict]:
    """Parse film rows from a public Letterboxd watchlist HTML page."""
    films = []
    seen_links = set()
    for match in re.finditer(
        r'<div[^>]+data-component-class="LazyPoster"[^>]+>',
        html_text,
        re.IGNORECASE,
    ):
        tag = match.group(0)
        raw_name = _html_attr(tag, "data-item-name") or _html_attr(tag, "data-item-full-display-name")
        link = _html_attr(tag, "data-item-link") or ""
        if not raw_name or not link or link in seen_links:
            continue

        title = raw_name
        year = None
        year_match = re.search(r"\((\d{4})\)\s*$", raw_name)
        if year_match:
            year = int(year_match.group(1))
            title = raw_name[: year_match.start()].strip()

        films.append({
            "film_title": title,
            "year": year,
            "link": f"https://letterboxd.com{link}" if link.startswith("/") else link,
            "thumb": None,
        })
        seen_links.add(link)

    return films


def _watchlist_page_count(html_text: str) -> int:
    pages = [1]
    for match in re.finditer(r"/watchlist/page/(\d+)/", html_text):
        pages.append(int(match.group(1)))
    return max(pages)


async def _get_watchlist_from_pages(username: str) -> list[dict]:
    first_url = _LB_WATCHLIST_PAGE_URL.format(username=username)
    first_page = await _fetch_text(first_url, forbidden_error="private")
    films = _parse_watchlist_page(first_page)
    total_pages = _watchlist_page_count(first_page)

    for page in range(2, total_pages + 1):
        page_url = _LB_WATCHLIST_PAGE_N_URL.format(username=username, page=page)
        page_html = await _fetch_text(page_url, forbidden_error="private")
        films.extend(_parse_watchlist_page(page_html))

    return films


# ---------- public API ----------

async def get_diary(username: str, force: bool = False) -> list[dict]:
    """Fetch and cache a user's recent Letterboxd diary entries (last ~50)."""
    key = _lb_diary_key(username)
    if not force:
        cached = cache.get(key)
        if cached is not None:
            return cached

    url = _LB_DIARY_URL.format(username=username)
    xml_text = await _fetch_rss(url)
    entries = _parse_diary_xml(xml_text)
    cache.set(key, entries, ttl_seconds=_CACHE_TTL)
    return entries


async def get_watchlist(username: str, force: bool = False) -> list[dict]:
    """Fetch and cache a user's Letterboxd watchlist."""
    key = _lb_watchlist_key(username)
    if not force:
        cached = cache.get(key)
        if cached is not None:
            return cached

    url = _LB_WATCHLIST_URL.format(username=username)
    try:
        xml_text = await _fetch_rss(url)
        films = _parse_watchlist_xml(xml_text)
    except LetterboxdError as e:
        if "private" not in str(e):
            raise
        films = await _get_watchlist_from_pages(username)
    cache.set(key, films, ttl_seconds=_CACHE_TTL)
    return films


async def validate_username(username: str) -> bool:
    """
    Returns True if the username resolves to a public LB diary feed.
    Returns False for 404 (not found) or 403 (private).
    Raises LetterboxdError on network errors.
    """
    url = _LB_DIARY_URL.format(username=username)
    try:
        await _fetch_rss(url)
        return True
    except LetterboxdError as e:
        msg = str(e)
        if "not_found" in msg or "private" in msg:
            return False
        raise
