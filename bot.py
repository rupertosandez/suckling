import asyncio
import atexit
import os
import signal
import sys
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import tmdb
import db
import tracker
import embeds
import game
import logger
import plex
import version
import sixdegrees
import trivia_roulette
import rental as rental_module
import letterboxd as lb_module
import macguffin as macguffin_module
import cleanup as cleanup_module
import achievements as achievement_module
import update_announcements
import views

LB_ACTIVITY_POST_LIMIT = 20
LB_ACTIVITY_COMPACT_THRESHOLD = 3
LB_ACTIVITY_WINDOW_MINUTES = 60
ACHIEVEMENT_SPEEDRUN_SECONDS = 10
COG_EXTENSIONS = (
    "cogs.achievements",
    "cogs.discovery",
    "cogs.admin",
    "cogs.games",
    "cogs.letterboxd",
    "cogs.macguffins",
    "cogs.meta",
    "cogs.rb9",
    "cogs.rentals",
    "cogs.tracking",
    "cogs.watchlist",
)

intents = discord.Intents.default()
intents.message_content = True


class SucklingBot(commands.Bot):
    """Bot subclass that tracks startup time and closes the shared TMDB session on shutdown."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started_at = datetime.now(timezone.utc)

    async def setup_hook(self) -> None:
        views.register_persistent_public_film_buttons(self)
        for extension in COG_EXTENSIONS:
            await self.load_extension(extension)

    async def close(self) -> None:
        try:
            await tmdb.close_session()
        except Exception as e:
            logger.log_exception("bot_close", e)
        try:
            await lb_module.close_session()
        except Exception as e:
            logger.log_exception("bot_close_letterboxd", e)
        try:
            await cleanup_module.close_session()
        except Exception as e:
            logger.log_exception("bot_close_cleanup", e)
        await super().close()


bot = SucklingBot(command_prefix="!", intents=intents)
scheduler = AsyncIOScheduler()
_shutdown_started = False
_bot_loop: asyncio.AbstractEventLoop | None = None
_instance_lock_handle = None
_PID_FILE_NAME = "bot.pid"
_LAUNCHER_ENV_KEY = "SUCKLINGBOT_LAUNCHER_MANAGED"
_LAUNCHER_RESTART_EXIT_CODE = 75


def _configure_console_encoding() -> None:
    """Keep scheduled print output from failing on Windows cp1252 consoles."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _acquire_instance_lock() -> bool:
    """Prevent multiple bot processes from logging in with the same token."""
    global _instance_lock_handle

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = Path(config.DATA_DIR) / "bot.instance.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    handle.seek(0)

    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False

    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    _instance_lock_handle = handle
    return True


def _pid_file_path() -> Path:
    return Path(config.DATA_DIR) / _PID_FILE_NAME


def _write_pid_file() -> None:
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _pid_file_path().write_text(str(os.getpid()), encoding="utf-8")
    except Exception as e:
        logger.log_exception("bot_pid_write", e)


def _clear_pid_file() -> None:
    try:
        path = _pid_file_path()
        if path.exists() and path.read_text(encoding="utf-8").strip() == str(os.getpid()):
            path.unlink()
    except Exception as e:
        logger.log_exception("bot_pid_clear", e)


async def _shutdown_from_signal(signal_name: str) -> None:
    """Close the bot cleanly when the launcher asks the process to stop."""
    global _shutdown_started
    if _shutdown_started:
        return
    _shutdown_started = True

    print(f"[shutdown] received {signal_name}, closing sucklingbot")

    try:
        if scheduler.running:
            scheduler.shutdown(wait=False)
    except Exception as e:
        logger.log_exception("signal_scheduler_shutdown", e)

    try:
        await bot.close()
    finally:
        _clear_pid_file()
        print("[shutdown] closed sucklingbot")


def _request_shutdown(reason: str) -> None:
    loop = _bot_loop
    if loop is None or loop.is_closed():
        return

    loop.call_soon_threadsafe(
        lambda: asyncio.create_task(_shutdown_from_signal(reason))
    )


def _handle_shutdown_signal(signum, _frame) -> None:
    signal_name = signal.Signals(signum).name
    _request_shutdown(signal_name)


signal.signal(signal.SIGTERM, _handle_shutdown_signal)
if hasattr(signal, "SIGBREAK"):
    signal.signal(signal.SIGBREAK, _handle_shutdown_signal)


def _start_launcher_stdin_listener() -> None:
    """Listen for the desktop launcher's stdin shutdown request."""

    def _listen() -> None:
        try:
            for line in sys.stdin:
                if line.strip().lower() == "shutdown":
                    _request_shutdown("launcher stdin")
                    return
        except Exception as e:
            logger.log_exception("launcher_stdin_listener", e)

    if sys.stdin is None or sys.stdin.closed:
        return

    thread = threading.Thread(
        target=_listen,
        name="launcher-stdin-listener",
        daemon=True,
    )
    thread.start()


async def _post_update_announcement_once() -> None:
    """Post a startup update announcement once per shipped version."""
    current_version = version.VERSION
    if db.get_last_update_announced_version() == current_version:
        return

    ok, message = await update_announcements.post_update_announcement(bot)
    if ok:
        print(f"[startup-update] Posted update announcement for v{current_version}")
    else:
        print(f"[startup-update] {message}")


async def _scheduled_check():
    if not db.is_announcements_enabled():
        print("[scheduler] Streaming check skipped — announcements disabled")
        return
    try:
        await tracker.run_check(bot=bot, dry_run=False)
    except Exception as e:
        logger.log_exception("scheduled_check", e)
        print(f"[scheduler] Daily tracker check failed: {e}")


async def _scheduled_daily_rec():
    if not db.is_daily_rec_enabled():
        print("[scheduler] Daily recommendation skipped — daily rec disabled")
        return
    try:
        await bot.suckling_post_daily_recommendation(bot)
    except Exception as e:
        logger.log_exception("scheduled_daily_rec", e)
        print(f"[scheduler] Daily recommendation failed: {e}")


async def _restart_process(delay_seconds: float = 1.0) -> None:
    """Restart the bot by replacing the current process with the same Python invocation."""
    await asyncio.sleep(delay_seconds)
    print("[restart] Restart requested from Discord")

    try:
        scheduler.shutdown(wait=False)
    except Exception as e:
        logger.log_exception("restart_scheduler_shutdown", e)

    try:
        await tmdb.close_session()
    except Exception as e:
        logger.log_exception("restart_tmdb_close", e)

    try:
        await lb_module.close_session()
    except Exception as e:
        logger.log_exception("restart_letterboxd_close", e)

    try:
        await cleanup_module.close_session()
    except Exception as e:
        logger.log_exception("restart_cleanup_close", e)

    if os.getenv(_LAUNCHER_ENV_KEY):
        print("[restart] Handing restart to launcher")
        _clear_pid_file()
        os._exit(_LAUNCHER_RESTART_EXIT_CODE)

    try:
        os.execv(sys.executable, [sys.executable, *sys.argv])
    except Exception as e:
        logger.log_exception("restart_exec", e)
        print(f"[restart] Failed to exec replacement process: {e}")


async def _scheduled_rental_check():
    """Hourly job: send overdue DMs and 12-hour reminder DMs."""
    try:
        await rental_module.check_overdue(bot)
        await rental_module.check_reminders(bot)
    except Exception as e:
        logger.log_exception("scheduled_rental_check", e)
        print(f"[scheduler] Rental check failed: {e}")


def _lb_activity_key(lb_username: str, entry: dict) -> str:
    link = entry.get("link")
    if link:
        return link
    title = entry.get("film_title") or "Unknown"
    year = entry.get("year") or ""
    watch_date = entry.get("watch_date") or ""
    return f"{lb_username}:{title}:{year}:{watch_date}".lower()


async def _activity_channel() -> discord.abc.Messageable | None:
    channel_id = db.get_lb_activity_channel_id()
    if not channel_id:
        return None

    channel = bot.get_channel(channel_id)
    if channel is not None:
        return channel

    try:
        return await bot.fetch_channel(channel_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
        logger.log_exception("lb_activity_fetch_channel", e)
        return None


def _member_label(user_id: str, lb_username: str) -> str:
    try:
        return f"<@{int(user_id)}>"
    except (TypeError, ValueError):
        pass

    guild = bot.get_guild(config.GUILD_ID)
    if guild is None:
        return lb_username
    try:
        member = guild.get_member(int(user_id))
    except ValueError:
        member = None
    return member.display_name if member else lb_username


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _lb_activity_window_start(now: datetime) -> datetime:
    recent_cutoff = now - timedelta(minutes=LB_ACTIVITY_WINDOW_MINUTES)
    last_run = _parse_iso_datetime(db.get_lb_activity_last_run_at())
    if last_run is None:
        return recent_cutoff
    return max(recent_cutoff, last_run)


async def run_lb_activity_check(
    *,
    post: bool,
    seed_only: bool = False,
    limit: int = LB_ACTIVITY_POST_LIMIT,
) -> dict:
    accounts = db.get_all_lb_accounts()
    result = {
        "accounts": len(accounts),
        "fetched": 0,
        "new": 0,
        "recent": 0,
        "posted": 0,
        "compacted": 0,
        "seeded": 0,
        "skipped": 0,
        "errors": 0,
        "missing_channel": False,
    }

    channel = None
    if post and not seed_only:
        channel = await _activity_channel()
        if channel is None:
            result["missing_channel"] = True
            return result

    candidate_items = []
    for account in accounts:
        user_id = account["user_id"]
        lb_username = account["lb_username"]
        try:
            entries = await lb_module.get_diary(lb_username)
            result["fetched"] += 1
        except lb_module.LetterboxdError as e:
            result["errors"] += 1
            print(f"[letterboxd-activity] Failed to fetch {lb_username}: {e}")
            continue

        for entry in entries:
            entry_key = _lb_activity_key(lb_username, entry)
            candidate_items.append({
                "entry_key": entry_key,
                "user_id": user_id,
                "lb_username": lb_username,
                "discord_tag": _member_label(user_id, lb_username),
                "entry": entry,
            })

    seen_keys = db.get_seen_lb_activity_keys(
        item["entry_key"] for item in candidate_items
    )
    unseen_items = [
        item for item in candidate_items
        if item["entry_key"] not in seen_keys
    ]
    result["new"] = len(unseen_items)

    if seed_only:
        db.record_lb_activity_seen_many(
            (
                item["entry_key"],
                item["lb_username"],
                item["entry"].get("film_title", "Unknown"),
                False,
            )
            for item in unseen_items
        )
        result["seeded"] = len(unseen_items)
        db.set_lb_activity_last_run_at()
        return result

    if not post:
        window_start = _lb_activity_window_start(datetime.now(timezone.utc))
        result["recent"] = sum(
            1
            for item in unseen_items
            if (
                published_at := _parse_iso_datetime(
                    item["entry"].get("published_at")
                )
            )
            and published_at > window_start
        )
        return result

    now = datetime.now(timezone.utc)
    window_start = _lb_activity_window_start(now)
    new_items = []
    stale_items = []
    for item in unseen_items:
        published_at = _parse_iso_datetime(item["entry"].get("published_at"))
        if published_at is not None and published_at > window_start:
            new_items.append(item)
        else:
            stale_items.append(item)

    result["recent"] = len(new_items)

    new_items.sort(key=lambda item: item["entry"].get("published_at", ""))
    post_items = new_items[:limit]
    skipped_items = new_items[limit:] + stale_items

    grouped_items: dict[tuple[str, str], list[dict]] = {}
    for item in post_items:
        grouped_items.setdefault(
            (item["user_id"], item["lb_username"]),
            [],
        ).append(item)

    async def _send_lb_activity_items(items: list[dict]) -> bool:
        first_item = items[0]
        if len(items) > LB_ACTIVITY_COMPACT_THRESHOLD:
            embed = embeds.lb_activity_compact_embed(
                first_item["lb_username"],
                [item["entry"] for item in items],
                discord_tag=first_item["discord_tag"],
            )
        else:
            embed = embeds.lb_activity_embed(
                first_item["lb_username"],
                first_item["entry"],
                discord_tag=first_item["discord_tag"],
            )

        try:
            await channel.send(embed=embed)
        except discord.HTTPException as e:
            result["errors"] += 1
            logger.log_exception("lb_activity_post", e)
            return False

        if len(items) > LB_ACTIVITY_COMPACT_THRESHOLD:
            result["compacted"] += 1
        return True

    for items in grouped_items.values():
        if len(items) <= LB_ACTIVITY_COMPACT_THRESHOLD:
            for item in items:
                if not await _send_lb_activity_items([item]):
                    continue
                db.record_lb_activity_seen(
                    item["entry_key"],
                    item["lb_username"],
                    item["entry"].get("film_title", "Unknown"),
                    posted=True,
                )
                result["posted"] += 1
            continue

        if not await _send_lb_activity_items(items):
            continue
        db.record_lb_activity_seen_many(
            (
                item["entry_key"],
                item["lb_username"],
                item["entry"].get("film_title", "Unknown"),
                True,
            )
            for item in items
        )
        result["posted"] += len(items)

    db.record_lb_activity_seen_many(
        (
            item["entry_key"],
            item["lb_username"],
            item["entry"].get("film_title", "Unknown"),
            False,
        )
        for item in skipped_items
    )
    result["skipped"] = len(skipped_items)
    db.set_lb_activity_last_run_at(now.isoformat())
    return result


async def _scheduled_lb_activity_check():
    if not db.is_lb_activity_enabled():
        print("[scheduler] Letterboxd activity skipped - disabled")
        return
    try:
        result = await run_lb_activity_check(post=True)
        if result["missing_channel"]:
            print("[scheduler] Letterboxd activity skipped - no channel set")
        elif result["posted"] or result["skipped"]:
            print(
                "[scheduler] Letterboxd activity posted "
                f"{result['posted']} new entry/entries, skipped {result['skipped']}"
            )
    except Exception as e:
        logger.log_exception("scheduled_lb_activity_check", e)
        print(f"[scheduler] Letterboxd activity check failed: {e}")


async def _scheduled_plex_cleanup():
    await cleanup_module.scheduled_cleanup(bot)


async def _scheduled_plex_incremental_refresh():
    if not config.PLEX_TOKEN:
        return
    try:
        await plex.refresh_incremental_cache()
    except Exception as e:
        logger.log_exception("scheduled_plex_incremental_refresh", e)
        print(f"[scheduler] Plex incremental refresh failed: {e}")


async def _scheduled_plex_full_refresh():
    if not config.PLEX_TOKEN:
        return
    try:
        await plex.refresh_full_cache()
    except Exception as e:
        logger.log_exception("scheduled_plex_full_refresh", e)
        print(f"[scheduler] Plex full refresh failed: {e}")


@bot.event
async def on_ready():
    global _bot_loop
    _bot_loop = asyncio.get_running_loop()

    print(f"Logged in as {bot.user} (id: {bot.user.id})")
    guild = discord.Object(id=config.GUILD_ID)
    try:
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"Synced {len(synced)} slash command(s) to guild {config.GUILD_ID}")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

    if not scheduler.running:
        scheduler.add_job(
            _scheduled_check, trigger="cron", hour=9, minute=0,
            id="daily_tracker_check", replace_existing=True,
        )
        scheduler.add_job(
            _scheduled_daily_rec, trigger="cron", hour=12, minute=0,
            id="daily_horror_rec", replace_existing=True,
        )
        scheduler.add_job(
            _scheduled_rental_check, trigger="interval", hours=1,
            id="rental_check", replace_existing=True,
        )
        scheduler.add_job(
            _scheduled_lb_activity_check, trigger="interval", hours=1,
            id="lb_activity_check", replace_existing=True,
        )
        scheduler.add_job(
            _scheduled_plex_cleanup, trigger="cron", day=1, hour=10, minute=0,
            id="plex_cleanup_check", replace_existing=True,
        )
        scheduler.add_job(
            _scheduled_plex_incremental_refresh, trigger="interval", hours=1,
            id="plex_incremental_refresh", replace_existing=True,
        )
        scheduler.add_job(
            _scheduled_plex_full_refresh,
            trigger="cron",
            day_of_week="sun",
            hour=4,
            minute=0,
            id="plex_weekly_full_refresh", replace_existing=True,
        )
        scheduler.start()
        print("[scheduler] Daily tracker check scheduled for 9:00 local time")
        print("[scheduler] Daily horror recommendation scheduled for 12:00 local time")
        print("[scheduler] Rental overdue/reminder check scheduled hourly")
        print("[scheduler] Letterboxd activity check scheduled hourly")
        print("[scheduler] Plex cleanup scheduled monthly on day 1 at 10:00 local time")
        print("[scheduler] Plex incremental refresh scheduled hourly")
        print("[scheduler] Plex full refresh scheduled Sundays at 4:00 local time")

    # Warm Plex in the background so the first /rb9 or /rent call does not pay
    # the full library scan cost. Errors are logged but never block startup.
    async def _warm_plex_cache():
        try:
            await plex.warm_cache()
        except Exception as e:
            logger.log_exception("plex_warm_cache", e)
            print(f"[plex] Warm cache failed: {e}")

    asyncio.create_task(_warm_plex_cache())
    asyncio.create_task(_post_update_announcement_once())
    print("[startup] sucklingbot started successfully")


@bot.event
async def on_message(message: discord.Message):
    """Listen for guess attempts in channels with active rounds."""
    if message.author.bot:
        return

    try:
        # Existing /guess game (poster/still)
        round_obj = game.get_round(message.channel.id)
        if round_obj and not round_obj.revealed:
            if game.title_matches(message.content, round_obj.title):
                round_obj.winner_id = str(message.author.id)
                round_obj.winner_tag = str(message.author)
                if _is_speedrun_win(round_obj.started_at):
                    achievement_module.record_event(
                        str(message.author.id),
                        str(message.author),
                        "speedrun_win",
                        str(round_obj.movie_id),
                    )
                round_obj.end_event.set()
                return

        # Trivia roulette
        trivia_round = trivia_roulette.get_round(message.channel.id)
        if trivia_round and not trivia_round.revealed:
            if trivia_roulette.answer_matches(message.content, trivia_round):
                trivia_round.winner_id = str(message.author.id)
                trivia_round.winner_tag = str(message.author)
                if _is_speedrun_win(trivia_round.started_at):
                    achievement_module.record_event(
                        str(message.author.id),
                        str(message.author),
                        "speedrun_win",
                        trivia_round.category,
                    )
                trivia_round.end_event.set()
                return
            
        # Six degrees game
        six_round = sixdegrees.get_round(message.channel.id)
        if six_round and not six_round.revealed:
            # Only treat as a guess if the message contains "->" or "→"
            if "->" not in message.content and "→" not in message.content:
                return

            await _process_six_submission(message, six_round)
    except Exception as e:
        logger.log_exception("on_message", e)


def _is_speedrun_win(started_at: datetime) -> bool:
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    elapsed = datetime.now(timezone.utc) - started_at.astimezone(timezone.utc)
    return elapsed.total_seconds() <= ACHIEVEMENT_SPEEDRUN_SECONDS


async def _process_six_submission(message: discord.Message, round_obj: "sixdegrees.SixRound"):
    """Validate a /six chain submission. First valid wins."""
    chain = sixdegrees.parse_chain(message.content)
    if chain is None:
        await message.reply(
            "❌ Invalid chain format. Use: `Actor -> Film -> Actor -> Film -> Actor`",
            mention_author=False,
        )
        return

    # Show the bot is processing
    async with message.channel.typing():
        result = await sixdegrees.validate_chain(
            chain,
            expected_start=round_obj.actor_a_name,
            expected_end=round_obj.actor_b_name,
        )

    if not result.valid:
        await message.reply(f"❌ {result.error}", mention_author=False)
        return

    # Lock in the winner — first valid chain wins
    if round_obj.winner_id is not None:
        return  # someone else won between validation start and now (race condition guard)

    round_obj.winner_id = str(message.author.id)
    round_obj.winner_tag = str(message.author)
    round_obj.winning_chain = chain
    round_obj.winning_film_count = result.film_count
    round_obj.end_event.set()


# ---------- tracking ----------

def _lb_activity_summary(result: dict) -> str:
    if result.get("missing_channel"):
        return "no letterboxd activity channel is set yet."
    return (
        f"checked **{result['fetched']}/{result['accounts']}** linked account(s), "
        f"found **{result['new']}** new entry/entries, "
        f"**{result.get('recent', 0)}** within the posting window, "
        f"posted **{result['posted']}**, "
        f"compacted **{result.get('compacted', 0)}** batch(es), "
        f"seeded **{result['seeded']}**, "
        f"skipped **{result['skipped']}**."
    )


bot.suckling_restart_process = _restart_process
bot.suckling_run_lb_activity_check = run_lb_activity_check
bot.suckling_lb_activity_summary = _lb_activity_summary
bot.suckling_run_plex_cleanup = cleanup_module.run_cleanup
bot.suckling_run_unpopularity_audit = cleanup_module.run_unpopularity_audit


if __name__ == "__main__":
    _configure_console_encoding()
    logger.setup_logging()
    if not _acquire_instance_lock():
        print("[startup] another sucklingbot instance is already running; exiting")
        sys.exit(0)
    _write_pid_file()
    atexit.register(_clear_pid_file)
    print(f"[startup] sucklingbot v{version.VERSION}")
    db.init_db()
    print("Database initialized")
    macguffin_count = len(macguffin_module.load_cards())
    print(f"[macguffins] Loaded {macguffin_count} cards")
    trivia_counts = trivia_roulette.load_assets()
    if trivia_counts:
        total = sum(trivia_counts.values())
        breakdown = ", ".join(f"{k}: {v}" for k, v in trivia_counts.items())
        print(f"[trivia] Loaded {total} entries ({breakdown})")
    else:
        print("[trivia] No trivia content loaded — /play will be unavailable")
    _start_launcher_stdin_listener()
    try:
        bot.run(config.DISCORD_TOKEN)
    finally:
        _clear_pid_file()
