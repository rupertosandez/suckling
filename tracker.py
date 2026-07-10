"""
Daily polling job that detects newly-streaming horror movies.
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import discord

import tmdb
import db
import embeds
import achievements as achievement_module


HORROR_GENRE_ID = 27
LOOKBACK_MONTHS = 18
DISCOVER_PAGES = 10
FETCH_CONCURRENCY = 8
PROVIDER_CHECK_CONCURRENCY = 8


@dataclass
class CheckResult:
    started_at: datetime
    finished_at: datetime | None = None
    discover_count: int = 0
    candidate_count: int = 0
    is_first_run: bool = False
    announcements: list[tuple[int, str, list[str], str | None, str]] = field(default_factory=list)
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
            for movie_id, title, providers, *_tracker_user_id in self.announcements[:15]:
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
    movies: list[dict] = []
    params = {
        "with_genres": str(HORROR_GENRE_ID),
        "primary_release_date.gte": cutoff,
        "sort_by": "popularity.desc",
    }

    for start in range(1, DISCOVER_PAGES + 1, FETCH_CONCURRENCY):
        pages = range(start, min(start + FETCH_CONCURRENCY, DISCOVER_PAGES + 1))
        page_results = await asyncio.gather(
            *(tmdb.discover_movies(page=page, **params) for page in pages),
            return_exceptions=True,
        )

        stop_after_batch = False
        for item in page_results:
            if isinstance(item, Exception):
                _log(result, f"  [warn] Discover page failed: {item}", warning=True)
                continue
            if not item:
                stop_after_batch = True
                continue
            movies.extend(item)

        if stop_after_batch:
            break

    return movies


def _build_candidate_pool(discover_movies: list[dict]) -> dict[int, dict[str, str | None]]:
    candidates: dict[int, dict[str, str | None]] = {}
    for movie in discover_movies:
        candidates[movie["id"]] = {
            "title": movie.get("title", "Unknown"),
            "tracker_user_id": None,
        }
    for tracked in db.list_tracked_movies():
        candidates[tracked["tmdb_id"]] = {
            "title": tracked["title"],
            "tracker_user_id": tracked.get("added_by_id"),
        }
    return candidates


def _provider_names(providers: dict, *categories: str) -> list[str]:
    """Collect deduped provider names across the given TMDB categories."""
    names: list[str] = []
    for category in categories:
        for provider in providers.get(category, []):
            name = provider.get("provider_name")
            if name and name not in names:
                names.append(name)
    return names


async def _fetch_movie_provider_names(
    movie_id: int,
    title: str,
    result: CheckResult,
) -> tuple[list[str], bool, list[str], bool]:
    """
    Fetch current provider names for a movie, split by tier.

    Returns (sub_names, currently_streaming, digital_names, currently_digital).
    Subscription is flatrate; digital is rent + buy. Database reads/writes happen
    later in the main loop so SQLite writes stay serialized.
    """
    try:
        providers = await tmdb.get_watch_providers(movie_id, region="US", force=True)
    except tmdb.TMDBError as e:
        _log(result, f"  [warn] Couldn't fetch providers for {title}: {e}", warning=True)
        return [], False, [], False

    sub_names = _provider_names(providers, "flatrate")
    digital_names = _provider_names(providers, "rent", "buy")
    return sub_names, bool(sub_names), digital_names, bool(digital_names)


async def _post_announcement(
    bot: discord.Client,
    movie_id: int,
    new_providers: list[str],
    tracker_user_id: str | None,
    result: CheckResult,
    tier: str = "subscription",
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

    if tier == "digital":
        embed = embeds.digital_announcement_embed(details, new_providers)
        content = (
            f"<@{tracker_user_id}> your tracked movie is now available to rent or buy."
            if tracker_user_id
            else None
        )
    else:
        embed = embeds.streaming_announcement_embed(details, new_providers)
        content = f"<@{tracker_user_id}> your tracked movie is streaming." if tracker_user_id else None
    allowed_mentions = discord.AllowedMentions(users=True) if tracker_user_id else None
    try:
        await channel.send(content=content, embed=embed, allowed_mentions=allowed_mentions)
        return True
    except discord.HTTPException as e:
        _log(result, f"  [warn] Failed to post to channel: {e}", warning=True)
        return False


async def _award_stream_prophet(
    bot: discord.Client,
    tracker_user_id: str | None,
    movie_id: int,
) -> None:
    if not tracker_user_id:
        return

    user = None
    try:
        user = await bot.fetch_user(int(tracker_user_id))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
        pass

    user_tag = str(user) if user else tracker_user_id
    await asyncio.to_thread(
        achievement_module.record_event,
        tracker_user_id,
        user_tag,
        "stream_prophet",
        str(movie_id),
    )
    if user:
        await achievement_module.award_for_user(
            bot,
            user,
            source_type="stream_prophet",
            source_id=str(movie_id),
        )
    else:
        await asyncio.to_thread(
            achievement_module.evaluate_user,
            tracker_user_id,
            user_tag,
            source_type="stream_prophet",
            source_id=str(movie_id),
        )


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

    # Same one-time baseline for the digital (rent/buy) tier: announced_digital
    # empty means we've never tracked digital availability, so silently mark
    # currently-available tracked films instead of dumping the back catalog.
    is_first_digital_run = False
    if not result.is_first_run:
        if db.announced_digital_count() == 0:
            is_first_digital_run = True
            print("[tracker] First digital-availability run — baselining currently-available tracked films")

    # Digital availability is tracked-only; build the set once for scoping.
    tracked_ids = {m["tmdb_id"] for m in db.list_tracked_movies()}

    discover_movies = await _discover_horror_movies(result)
    result.discover_count = len(discover_movies)
    print(f"[tracker] Pulled {result.discover_count} films from Discover")

    candidates = _build_candidate_pool(discover_movies)
    result.candidate_count = len(candidates)
    print(f"[tracker] Candidate pool: {result.candidate_count} films (Discover + tracked)")

    candidate_items = [
        (movie_id, meta["title"] or "Unknown", meta.get("tracker_user_id"))
        for movie_id, meta in candidates.items()
    ]
    for start in range(0, len(candidate_items), PROVIDER_CHECK_CONCURRENCY):
        batch = candidate_items[start:start + PROVIDER_CHECK_CONCURRENCY]
        batch_movie_ids = [movie_id for movie_id, _title, _tracker_user_id in batch]
        seen_providers = db.get_provider_snapshot_map(batch_movie_ids)
        announced_ids = (
            set()
            if result.is_first_run or is_first_announce_run
            else db.get_announced_movie_ids(batch_movie_ids)
        )
        announced_digital_ids = (
            set()
            if result.is_first_run or is_first_digital_run
            else db.get_announced_digital_ids(batch_movie_ids)
        )
        checks = await asyncio.gather(
            *(
                _fetch_movie_provider_names(movie_id, title, result)
                for movie_id, title, _tracker_user_id in batch
            ),
            return_exceptions=True,
        )

        provider_records: list[tuple[int, str]] = []
        announce_records: list[tuple[int, str]] = []
        digital_announce_records: list[tuple[int, str]] = []

        for (movie_id, title, tracker_user_id), check_result in zip(batch, checks):
            if isinstance(check_result, Exception):
                _log(result, f"  [warn] Provider check failed for {title}: {check_result}", warning=True)
                continue

            provider_names, currently_streaming, digital_names, currently_digital = check_result
            is_tracked = movie_id in tracked_ids

            known_provider_names = seen_providers.get(movie_id, set())
            newly_seen = [
                provider_name
                for provider_name in provider_names
                if provider_name not in known_provider_names
            ]
            provider_records.extend((movie_id, provider_name) for provider_name in newly_seen)

            # Digital snapshots share provider_snapshots, namespaced by prefix.
            newly_digital: list[str] = []
            if is_tracked:
                newly_digital = [
                    name
                    for name in digital_names
                    if db.DIGITAL_SNAPSHOT_PREFIX + name not in known_provider_names
                ]
                provider_records.extend(
                    (movie_id, db.DIGITAL_SNAPSHOT_PREFIX + name) for name in newly_digital
                )

            # Skip everything during first-ever run (baseline only)
            if result.is_first_run:
                continue

            # First announce-aware run: silently mark currently-streaming films as announced
            if is_first_announce_run and currently_streaming:
                announce_records.append((movie_id, title))
            elif newly_seen:
                # Only announce if it has new providers AND hasn't been announced before
                if movie_id in announced_ids:
                    result.skipped_already_announced += 1
                else:
                    result.announcements.append(
                        (movie_id, title, newly_seen, tracker_user_id, "subscription")
                    )

            # Digital tier (tracked movies only)
            if is_tracked:
                if is_first_digital_run and currently_digital:
                    digital_announce_records.append((movie_id, title))
                elif newly_digital:
                    if movie_id in announced_digital_ids:
                        result.skipped_already_announced += 1
                    else:
                        result.announcements.append(
                            (movie_id, title, newly_digital, tracker_user_id, "digital")
                        )

        db.record_providers_many(provider_records)
        db.record_announced_movies_many(announce_records)
        db.record_announced_digital_many(digital_announce_records)

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
    for movie_id, title, providers, tracker_user_id, tier in result.announcements:
        provider_list = ", ".join(providers)
        print(f"  • {title} → {provider_list} [{tier}]")

    if dry_run or bot is None:
        print("[tracker] Dry run — not posting to Discord.")
        result.finished_at = datetime.now()
        return result

    print("[tracker] Posting announcements to Discord...")
    for movie_id, title, new_providers, tracker_user_id, tier in result.announcements:
        ok = await _post_announcement(bot, movie_id, new_providers, tracker_user_id, result, tier)
        if ok:
            result.posted_count += 1
            if tier == "digital":
                db.record_announced_digital(movie_id, title)
            else:
                db.record_announced_movie(movie_id, title)
            await _award_stream_prophet(bot, tracker_user_id, movie_id)
        await asyncio.sleep(1)
    print(f"[tracker] Posted {result.posted_count}/{len(result.announcements)} announcements.")

    result.finished_at = datetime.now()
    return result
