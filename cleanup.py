from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiohttp
import discord

import config
import db
import embeds
import logger
import tmdb


MIN_SIZE_GB = 20
MIN_AGE_DAYS = 30
STALE_AFTER_DAYS = 365
MAX_PLAY_COUNT = 2
MEDIA_INFO_PAGE_SIZE = 250
PROVIDER_CHECK_CONCURRENCY = 4
POST_LIMIT = 10
UNPOPULARITY_POOL_SIZE = 200
MIN_TMDB_VOTES = 25

STREAMING_PROVIDER_TYPES = ("flatrate", "free", "ads")

_session: aiohttp.ClientSession | None = None


class CleanupError(Exception):
    pass


@dataclass
class CleanupCandidate:
    title: str
    year: int | None
    rating_key: str
    file_size_bytes: int
    play_count: int
    added_at: datetime | None
    last_played: datetime | None
    providers: list[str]
    tmdb_id: int | None = None
    score: float = 0

    @property
    def size_gb(self) -> float:
        return self.file_size_bytes / (1024 ** 3)


@dataclass
class CleanupResult:
    started_at: datetime
    finished_at: datetime | None = None
    dry_run: bool = True
    scanned_count: int = 0
    eligible_count: int = 0
    candidates: list[CleanupCandidate] = field(default_factory=list)
    posted: bool = False
    missing_channel: bool = False
    warnings: list[str] = field(default_factory=list)

    def duration_seconds(self) -> float:
        if not self.finished_at:
            return 0
        return (self.finished_at - self.started_at).total_seconds()

    def to_discord_summary(self) -> str:
        lines = ["**plex cleanup check complete**", ""]
        lines.append(f"scanned **{self.scanned_count}** library item(s)")
        lines.append(f"eligible after size/activity filters: **{self.eligible_count}**")
        lines.append(f"cleanup candidates with streaming availability: **{len(self.candidates)}**")
        if self.candidates:
            lines.append("")
            for candidate in self.candidates:
                providers = ", ".join(candidate.providers[:3])
                lines.append(
                    f"- **{candidate.title}** ({candidate.year or '????'}) "
                    f"- {candidate.size_gb:.1f} gb, {candidate.play_count} play(s), "
                    f"available on {providers}"
                )
        if self.missing_channel:
            lines.append("")
            lines.append("no announcement channel is set, so nothing was posted.")
        elif self.dry_run:
            lines.append("")
            lines.append("dry run - no post made.")
        elif self.posted:
            lines.append("")
            lines.append("posted to the announcement channel.")
        if self.warnings:
            lines.append("")
            lines.append(f"{len(self.warnings)} warning(s) - see PowerShell.")
        lines.append("")
        lines.append(f"took {self.duration_seconds():.0f}s")
        return "\n".join(lines)


@dataclass
class UnpopularityItem:
    title: str
    year: int | None
    play_count: int
    tmdb_score: float
    tmdb_vote_count: int


@dataclass
class UnpopularityResult:
    scanned_count: int
    checked_count: int
    items: list[UnpopularityItem]

    def to_discord_summary(self) -> str:
        lines = ["**plex unpopularity audit**", ""]
        lines.append(f"scanned **{self.scanned_count}** library item(s)")
        lines.append(f"checked **{self.checked_count}** low-watch title(s) against TMDB")
        if not self.items:
            lines.append("")
            lines.append("no TMDB-rated titles found yet.")
            return "\n".join(lines)

        lines.append("")
        for index, item in enumerate(self.items, start=1):
            lines.append(
                f"{index}. **{item.title}** ({item.year or '????'}) - "
                f"{item.play_count} watch(es), "
                f"{item.tmdb_score:.1f}/10 on TMDB from {item.tmdb_vote_count:,} vote(s)"
            )
        return "\n".join(lines)


def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        timeout = aiohttp.ClientTimeout(total=30, sock_connect=10, sock_read=20)
        _session = aiohttp.ClientSession(timeout=timeout)
    return _session


async def close_session() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


def _log(result: CleanupResult, msg: str, *, warning: bool = False) -> None:
    print(msg)
    if warning:
        result.warnings.append(msg)


def _require_config() -> None:
    if not config.TAUTULLI_URL or not config.TAUTULLI_API_KEY:
        raise CleanupError("TAUTULLI_URL and TAUTULLI_API_KEY must be set in .env")


async def _api(cmd: str, **params: Any) -> Any:
    _require_config()
    request_params = {
        "apikey": config.TAUTULLI_API_KEY,
        "cmd": cmd,
        **{key: value for key, value in params.items() if value is not None},
    }
    session = _get_session()
    try:
        async with session.get(
            f"{config.TAUTULLI_URL}/api/v2",
            params=request_params,
        ) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise CleanupError(f"Tautulli returned {resp.status}: {text[:200]}")
            payload = await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise CleanupError(f"Tautulli request failed for {cmd}: {exc}") from exc

    response = payload.get("response", {})
    if response.get("result") != "success":
        message = response.get("message") or "unknown error"
        raise CleanupError(f"Tautulli {cmd} failed: {message}")
    return response.get("data")


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_match_title(value: str | None) -> str:
    text = (value or "").lower().strip()
    for article in ("the ", "a ", "an "):
        if text.startswith(article):
            text = text[len(article):]
    return "".join(char for char in text if char.isalnum())


def _tmdb_match_year(match: dict) -> int | None:
    release_date = match.get("release_date") or ""
    if len(release_date) >= 4 and release_date[:4].isdigit():
        return int(release_date[:4])
    return None


def _best_tmdb_match(title: str, year: int | None, matches: list[dict]) -> dict | None:
    if not matches:
        return None

    needle = _normalize_match_title(title)
    exact_title = [
        match for match in matches
        if _normalize_match_title(match.get("title")) == needle
    ]
    if year is not None:
        exact_year = [
            match for match in exact_title
            if _tmdb_match_year(match) == year
        ]
        if exact_year:
            return exact_year[0]

    if exact_title:
        return exact_title[0]
    return matches[0]


async def _movie_section_id() -> str:
    sections = await _api("get_library_names")
    if not isinstance(sections, list):
        raise CleanupError("Tautulli did not return a library list")

    movie_sections = [
        section for section in sections
        if str(section.get("section_type", "")).lower() == "movie"
    ]
    for section in movie_sections:
        if section.get("section_name") == config.PLEX_LIBRARY:
            return str(section["section_id"])
    if movie_sections:
        return str(movie_sections[0]["section_id"])
    raise CleanupError("No movie library found in Tautulli")


async def _fetch_media_info(section_id: str) -> list[dict]:
    rows: list[dict] = []
    start = 0
    total = None
    while total is None or start < total:
        data = await _api(
            "get_library_media_info",
            section_id=section_id,
            section_type="movie",
            order_column="file_size",
            order_dir="desc",
            start=start,
            length=MEDIA_INFO_PAGE_SIZE,
        )
        if not isinstance(data, dict):
            raise CleanupError("Tautulli did not return media info rows")
        batch = data.get("data", []) or []
        rows.extend(batch)
        total = _parse_int(data.get("recordsFiltered"), len(rows))
        if not batch:
            break
        start += len(batch)
    return rows


def _preliminary_candidates(rows: list[dict]) -> list[CleanupCandidate]:
    now = datetime.now(timezone.utc)
    candidates: list[CleanupCandidate] = []
    for row in rows:
        file_size = _parse_int(row.get("file_size"))
        if file_size < MIN_SIZE_GB * (1024 ** 3):
            continue

        added_at = _parse_timestamp(row.get("added_at"))
        if added_at and (now - added_at).days < MIN_AGE_DAYS:
            continue

        play_count = _parse_int(row.get("play_count"))
        if play_count > MAX_PLAY_COUNT:
            continue

        last_played = _parse_timestamp(row.get("last_played"))
        if last_played and (now - last_played).days < STALE_AFTER_DAYS:
            continue

        title = str(row.get("title") or row.get("sort_title") or "").strip()
        if not title:
            continue

        candidates.append(
            CleanupCandidate(
                title=title,
                year=_parse_int(row.get("year")) or None,
                rating_key=str(row.get("rating_key") or ""),
                file_size_bytes=file_size,
                play_count=play_count,
                added_at=added_at,
                last_played=last_played,
                providers=[],
            )
        )
    return candidates


async def _provider_match(candidate: CleanupCandidate) -> CleanupCandidate | None:
    try:
        matches = await tmdb.search_movie(candidate.title, year=candidate.year)
    except tmdb.TMDBError:
        return None
    if not matches:
        return None

    match = matches[0]
    movie_id = match.get("id")
    if not movie_id:
        return None

    try:
        providers = await tmdb.get_watch_providers(movie_id, region="US", force=True)
    except tmdb.TMDBError:
        return None

    provider_names: list[str] = []
    for provider_type in STREAMING_PROVIDER_TYPES:
        for provider in providers.get(provider_type, []) or []:
            name = provider.get("provider_name")
            if name and name not in provider_names:
                provider_names.append(name)

    if not provider_names:
        return None

    candidate.tmdb_id = int(movie_id)
    candidate.providers = provider_names
    return candidate


async def _attach_provider_matches(
    candidates: list[CleanupCandidate],
    result: CleanupResult,
) -> list[CleanupCandidate]:
    matched: list[CleanupCandidate] = []
    for start in range(0, len(candidates), PROVIDER_CHECK_CONCURRENCY):
        batch = candidates[start:start + PROVIDER_CHECK_CONCURRENCY]
        results = await asyncio.gather(
            *(_provider_match(candidate) for candidate in batch),
            return_exceptions=True,
        )
        for item in results:
            if isinstance(item, Exception):
                _log(result, f"[cleanup] provider check failed: {item}", warning=True)
            elif item is not None:
                matched.append(item)
    return matched


def _score(candidate: CleanupCandidate) -> float:
    score = candidate.size_gb
    if candidate.play_count == 0:
        score += 35
    elif candidate.play_count == 1:
        score += 20
    else:
        score += 10

    now = datetime.now(timezone.utc)
    if candidate.last_played is None:
        score += 25
    else:
        score += min(25, (now - candidate.last_played).days / 30)

    score += min(15, len(candidate.providers) * 4)
    return score


async def _post_cleanup(bot: discord.Client, result: CleanupResult) -> bool:
    channel_id = db.get_announcement_channel_id()
    if not channel_id:
        result.missing_channel = True
        return False

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            _log(result, f"[cleanup] couldn't access announcement channel: {exc}", warning=True)
            return False

    embed = embeds.plex_cleanup_embed(result.candidates)
    try:
        await channel.send(embed=embed)
        result.posted = True
        return True
    except discord.HTTPException as exc:
        _log(result, f"[cleanup] failed to post cleanup embed: {exc}", warning=True)
        return False


async def run_cleanup(
    bot: discord.Client | None = None,
    *,
    dry_run: bool = True,
) -> CleanupResult:
    result = CleanupResult(started_at=datetime.now(timezone.utc), dry_run=dry_run)
    print(f"[cleanup] starting cleanup check (dry_run={dry_run})")

    section_id = await _movie_section_id()
    rows = await _fetch_media_info(section_id)
    result.scanned_count = len(rows)

    preliminary = _preliminary_candidates(rows)
    result.eligible_count = len(preliminary)
    print(f"[cleanup] {len(preliminary)} item(s) eligible after local filters")

    matched = await _attach_provider_matches(preliminary, result)
    for candidate in matched:
        candidate.score = _score(candidate)
    matched.sort(key=lambda candidate: candidate.score, reverse=True)
    result.candidates = matched[:POST_LIMIT]

    if not dry_run and bot is not None and result.candidates:
        await _post_cleanup(bot, result)

    result.finished_at = datetime.now(timezone.utc)
    print(f"[cleanup] complete with {len(result.candidates)} candidate(s)")
    return result


async def run_unpopularity_audit(limit: int = 10) -> UnpopularityResult:
    section_id = await _movie_section_id()
    rows = await _fetch_media_info(section_id)

    items: list[UnpopularityItem] = []
    low_watch_rows = sorted(
        rows,
        key=lambda row: (
            _parse_int(row.get("play_count")),
            str(row.get("title") or row.get("sort_title") or "").lower(),
        ),
    )[:UNPOPULARITY_POOL_SIZE]

    for row in low_watch_rows:
        title = str(row.get("title") or row.get("sort_title") or "").strip()
        if not title:
            continue

        year = _parse_int(row.get("year")) or None
        try:
            matches = await tmdb.search_movie(title, year=year)
        except tmdb.TMDBError:
            continue

        if not matches:
            continue

        match = _best_tmdb_match(title, year, matches)
        if not match:
            continue
        vote_count = _parse_int(match.get("vote_count"))
        tmdb_score = float(match.get("vote_average") or 0)
        if vote_count < MIN_TMDB_VOTES or tmdb_score <= 0:
            continue

        items.append(
            UnpopularityItem(
                title=title,
                year=year,
                play_count=_parse_int(row.get("play_count")),
                tmdb_score=tmdb_score,
                tmdb_vote_count=vote_count,
            )
        )

    items.sort(
        key=lambda item: (
            item.play_count,
            item.tmdb_score,
            -item.tmdb_vote_count,
            item.title.lower(),
        )
    )
    top_items = items[:limit]
    top_items.sort(
        key=lambda item: (
            item.tmdb_score,
            item.play_count,
            -item.tmdb_vote_count,
            item.title.lower(),
        )
    )
    return UnpopularityResult(
        scanned_count=len(rows),
        checked_count=len(low_watch_rows),
        items=top_items,
    )


async def scheduled_cleanup(bot: discord.Client) -> None:
    if not config.PLEX_CLEANUP_ENABLED:
        print("[scheduler] Plex cleanup skipped - disabled")
        return
    try:
        await run_cleanup(bot=bot, dry_run=False)
    except Exception as exc:
        logger.log_exception("scheduled_plex_cleanup", exc)
        print(f"[scheduler] Plex cleanup failed: {exc}")
