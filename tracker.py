"""
Daily polling job that detects newly-streaming horror movies.
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import discord

import config
import tmdb
import db
import embeds


HORROR_GENRE_ID = 27
LOOKBACK_MONTHS = 18
DISCOVER_PAGES = 10
PER_PAGE_SLEEP_SECONDS = 0.3


@dataclass
class CheckResult:
    started_at: datetime
    finished_at: datetime | None = None
    discover_count: int = 0
    candidate_count: int = 0
    is_first_run: bool = False
    announcements: list[tuple[int, str, list[str]]] = field(default_factory=list)
    posted_count: int = 0
    dry_run: bool = True
    warnings: list[str] = field(default_factory=list)
    skipped_already_announced: int = 0

    def duration_seconds(self) -> float:
        if not self.finished_at:
            return 0
        return (self.finished_at - self.started_at).total_seconds()

    def to_discord_summary(self) -> str:
        lines = ["🔍 **Streaming check complete**", ""]

        if self.is_first_run:
            lines.append("⚠️ First run — baseline established, no announcements made.")
            lines.append(f"Pulled **{self.discover_count}** films from Discover")
            lines.append(f"Candidate pool: **{self.candidate_count}** films")
            lines.append(f"Took {self.duration_seconds():.0f}s")
            return "\n".join(lines)

        lines.append(f"Pulled **{self.discover_count}** films from Discover")
        lines.append(f"Candidate pool: **{self.candidate_count}** films")
        lines.append("")

        if not self.announcements:
            lines.append("No new streaming additions.")
        else:
            n = len(self.announcements)
            lines.append(f"**{n} new announcement(s)**:")
            for movie_id, title, providers in self.announcements[:15]:
                provider_list = ", ".join(providers)
                lines.append(f"• {title} → {provider_list}")
            if n > 15:
                lines.append(f"…and {n - 15} more.")

        if self.skipped_already_announced > 0:
            lines.append("")
            lines.append(
                f"*Skipped {self.skipped_already_announced} film(s) that were already announced previously.*"
            )

        if self.dry_run:
            lines.append("")
            lines.append("*Dry run — no posts made.*")
        elif self.announcements:
            lines.append("")
            lines.append(f"Posted **{self.posted_count}/{len(self.announcements)}** to Discord.")

        if self.warnings:
            lines.append("")
            lines.append(f"⚠️ {len(self.warnings)} warning(s) — see PowerShell.")

        lines.append("")
        lines.append(f"Took {self.duration_seconds():.0f}s")
        return "\n".join(lines)


def _log(result: CheckResult, msg: str, *, warning: bool = False) -> None:
    print(msg)
    if warning:
        result.warnings.append(msg)


async def _discover_horror_movies(result: CheckResult) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=LOOKBACK_MONTHS * 30)).strftime("%Y-%m-%d")
    session = tmdb.get_session()
    movies = []

    for page in range(1, DISCOVER_PAGES + 1):
        params = {
            "api_key": config.TMDB_API_KEY,
            "with_genres": str(HORROR_GENRE_ID),
            "primary_release_date.gte": cutoff,
            "sort_by": "popularity.desc",
            "page": page,
            "include_adult": "false",
        }
        url = f"{tmdb.BASE_URL}/discover/movie"
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                _log(result, f"  [warn] Discover page {page} returned {resp.status}, skipping", warning=True)
                continue
            data = await resp.json()
            page_results = data.get("results", [])
            movies.extend(page_results)
            if not page_results:
                break

        await asyncio.sleep(PER_PAGE_SLEEP_SECONDS)

    return movies


def _build_candidate_pool(discover_movies: list[dict]) -> dict[int, str]:
    candidates: dict[int, str] = {}
    for movie in discover_movies:
        candidates[movie["id"]] = movie.get("title", "Unknown")
    for tracked in db.list_tracked_movies():
        candidates[tracked["tmdb_id"]] = tracked["title"]
    return candidates


async def _check_movie_providers(
    movie_id: int,
    title: str,
    result: CheckResult,
) -> tuple[list[str], bool]:
    """
    Check current subscription providers for a movie.

    Returns (newly_seen_providers, currently_has_any_streaming).
    """
    try:
        providers = await tmdb.get_watch_providers(movie_id, region="US", force=True)
    except tmdb.TMDBError as e:
        _log(result, f"  [warn] Couldn't fetch providers for {title}: {e}", warning=True)
        return [], False

    flatrate = providers.get("flatrate", [])
    if not flatrate:
        return [], False

    newly_seen = []
    for provider in flatrate:
        provider_name = provider.get("provider_name", "")
        if not provider_name:
            continue
        if not db.has_seen_provider(movie_id, provider_name):
            newly_seen.append(provider_name)
            db.record_provider(movie_id, provider_name)

    return newly_seen, True


async def _post_announcement(
    bot: discord.Client,
    movie_id: int,
    new_providers: list[str],
    result: CheckResult,
) -> bool:
    channel_id = db.get_announcement_channel_id()
    if not channel_id:
        _log(result, "  [warn] No announcement channel configured — skipping post", warning=True)
        return False

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.NotFound:
            _log(result, f"  [warn] Announcement channel {channel_id} not found", warning=True)
            return False
        except discord.Forbidden:
            _log(result, f"  [warn] No access to announcement channel {channel_id}", warning=True)
            return False

    try:
        details = await tmdb.get_movie_details(movie_id, force=True)
    except tmdb.TMDBError as e:
        _log(result, f"  [warn] Couldn't fetch details for movie {movie_id}: {e}", warning=True)
        return False

    embed = embeds.streaming_announcement_embed(details, new_providers)
    try:
        await channel.send(embed=embed)
        return True
    except discord.HTTPException as e:
        _log(result, f"  [warn] Failed to post to channel: {e}", warning=True)
        return False


async def run_check(bot: discord.Client | None = None, dry_run: bool = True) -> CheckResult:
    """
    Main polling routine.

    Returns a CheckResult summarizing what happened.
    """
    result = CheckResult(started_at=datetime.now(), dry_run=dry_run)
    print(f"[tracker] Starting check at {result.started_at.isoformat(timespec='seconds')} (dry_run={dry_run})")

    # First-run detection: provider_snapshots empty means we've never run before
    with db._connect() as conn:
        snapshot_count = conn.execute(
            "SELECT COUNT(*) AS c FROM provider_snapshots"
        ).fetchone()["c"]
    result.is_first_run = snapshot_count == 0
    if result.is_first_run:
        print("[tracker] First run detected — populating baseline snapshots silently")

    # Detect "first announce-aware run": announced_movies empty, but provider_snapshots has data.
    # This happens once after deploying the new feature on existing installs.
    # We need to silently mark all currently-streaming films as already-announced.
    is_first_announce_run = False
    if not result.is_first_run:
        announced_so_far = db.announced_count()
        if announced_so_far == 0:
            is_first_announce_run = True
            print("[tracker] First announce-aware run — marking currently-streaming films as already announced")

    discover_movies = await _discover_horror_movies(result)
    result.discover_count = len(discover_movies)
    print(f"[tracker] Pulled {result.discover_count} films from Discover")

    candidates = _build_candidate_pool(discover_movies)
    result.candidate_count = len(candidates)
    print(f"[tracker] Candidate pool: {result.candidate_count} films (Discover + tracked)")

    for movie_id, title in candidates.items():
        newly_seen, currently_streaming = await _check_movie_providers(movie_id, title, result)

        # Skip everything during first-ever run (baseline only)
        if result.is_first_run:
            await asyncio.sleep(PER_PAGE_SLEEP_SECONDS)
            continue

        # First announce-aware run: silently mark currently-streaming films as announced
        if is_first_announce_run and currently_streaming:
            db.record_announced_movie(movie_id, title)
            await asyncio.sleep(PER_PAGE_SLEEP_SECONDS)
            continue

        # Normal flow: only announce if it has new providers AND hasn't been announced before
        if newly_seen:
            if db.has_been_announced(movie_id):
                result.skipped_already_announced += 1
            else:
                result.announcements.append((movie_id, title, newly_seen))

        await asyncio.sleep(PER_PAGE_SLEEP_SECONDS)

    print(f"[tracker] Scan complete. {len(result.announcements)} new announcement(s).")
    if result.skipped_already_announced > 0:
        print(f"[tracker] Skipped {result.skipped_already_announced} films already announced previously.")

    if result.is_first_run:
        print("[tracker] First run complete — baseline established, no announcements made.")
        result.finished_at = datetime.now()
        return result

    if is_first_announce_run:
        print("[tracker] First announce-aware run complete — currently-streaming films marked as announced.")
        result.finished_at = datetime.now()
        return result

    if not result.announcements:
        print("[tracker] No new streaming additions today.")
        result.finished_at = datetime.now()
        return result

    print("[tracker] Announcements:")
    for movie_id, title, providers in result.announcements:
        provider_list = ", ".join(providers)
        print(f"  • {title} → {provider_list}")

    if dry_run or bot is None:
        print("[tracker] Dry run — not posting to Discord.")
        result.finished_at = datetime.now()
        return result

    print("[tracker] Posting announcements to Discord...")
    for movie_id, title, new_providers in result.announcements:
        ok = await _post_announcement(bot, movie_id, new_providers, result)
        if ok:
            result.posted_count += 1
            db.record_announced_movie(movie_id, title)
        await asyncio.sleep(1)
    print(f"[tracker] Posted {result.posted_count}/{len(result.announcements)} announcements.")

    result.finished_at = datetime.now()
    return result