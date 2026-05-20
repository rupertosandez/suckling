import asyncio
import os
import re
import signal
import sys
import threading
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import tmdb
import embeds
import views
import db
import tracker
import game
import logger
import plex
import version
import sixdegrees
import trivia_roulette
import rental as rental_module
import letterboxd as lb_module
import macguffin as macguffin_module

LB_ACTIVITY_POST_LIMIT = 20
LB_ACTIVITY_WINDOW_MINUTES = 60
COG_EXTENSIONS = (
    "cogs.discovery",
    "cogs.admin",
    "cogs.games",
    "cogs.macguffins",
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
        await super().close()


bot = SucklingBot(command_prefix="!", intents=intents)
scheduler = AsyncIOScheduler()
_shutdown_started = False
_bot_loop: asyncio.AbstractEventLoop | None = None


UPDATE_ANNOUNCEMENT_CHANNEL_ID = 1446966452669255761


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
        print("[shutdown] closed sucklingbot")
        sys.exit(0)


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

    channel = bot.get_channel(UPDATE_ANNOUNCEMENT_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(UPDATE_ANNOUNCEMENT_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden) as e:
            print(f"[startup-update] Couldn't access channel: {e}")
            return

    embed = discord.Embed(
        description=(
            f"yo check me out! i've been updated!!! v{current_version} 💪\n\n"
            "[ view changelog ](https://rupertosandez.github.io/sucklingsite/changelog/)"
        ),
        color=0x8B0000,
    )

    try:
        await channel.send(embed=embed)
        db.set_last_update_announced_version(current_version)
        print(f"[startup-update] Posted update announcement for v{current_version}")
    except discord.HTTPException as e:
        print(f"[startup-update] Failed to post update announcement: {e}")


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

    for item in post_items:
        embed = embeds.lb_activity_embed(
            item["lb_username"],
            item["entry"],
            discord_tag=item["discord_tag"],
        )
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as e:
            result["errors"] += 1
            logger.log_exception("lb_activity_post", e)
            continue

        db.record_lb_activity_seen(
            item["entry_key"],
            item["lb_username"],
            item["entry"].get("film_title", "Unknown"),
            posted=True,
        )
        result["posted"] += 1

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
        scheduler.start()
        print("[scheduler] Daily tracker check scheduled for 9:00 local time")
        print("[scheduler] Daily horror recommendation scheduled for 12:00 local time")
        print("[scheduler] Rental overdue/reminder check scheduled hourly")
        print("[scheduler] Letterboxd activity check scheduled hourly")

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
                round_obj.end_event.set()
                return

        # Trivia roulette
        trivia_round = trivia_roulette.get_round(message.channel.id)
        if trivia_round and not trivia_round.revealed:
            if trivia_roulette.answer_matches(message.content, trivia_round):
                trivia_round.winner_id = str(message.author.id)
                trivia_round.winner_tag = str(message.author)
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
        f"seeded **{result['seeded']}**, "
        f"skipped **{result['skipped']}**."
    )


@bot.tree.command(name="info", description="show info about the bot")
async def info(interaction: discord.Interaction):
    await interaction.response.defer()

    uptime_seconds = (datetime.now(timezone.utc) - bot.started_at).total_seconds()
    guild_count = len(bot.guilds)

    logo = discord.File("assets/logo.png", filename="logo.png")
    embed = embeds.info_embed(version.VERSION, uptime_seconds, guild_count)

    await interaction.followup.send(embed=embed, file=logo)


# ---------- letterboxd commands ----------

lb_group = app_commands.Group(name="lb", description="letterboxd integration")


def _film_key(item: dict) -> str:
    title = item.get("film_title") or item.get("title") or ""
    year = item.get("year")
    normalized = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    return f"{normalized}:{year or ''}"


def _resolve_lb_target(
    interaction: discord.Interaction,
    user: discord.Member | None,
    username: str | None,
) -> tuple[str | None, str | None, str | None]:
    if user is not None:
        lb_user = db.get_lb_username(str(user.id))
        if not lb_user:
            return None, None, f"**{user.display_name}** hasn't linked a letterboxd account."
        return lb_user, user.display_name, None

    if username is not None:
        return username, username, None

    lb_user = db.get_lb_username(str(interaction.user.id))
    if not lb_user:
        return (
            None,
            None,
            "you haven't linked a letterboxd account yet. use `/lb link <username>` to connect one.",
        )
    return lb_user, interaction.user.display_name, None


def _resolve_explicit_lb_target(
    user: discord.Member | None,
    username: str | None,
    label: str,
) -> tuple[str | None, str | None, str | None]:
    if user is not None and username is not None:
        return None, None, f"pick either `{label}_user` or `{label}_username`, not both."

    if user is not None:
        lb_user = db.get_lb_username(str(user.id))
        if not lb_user:
            return None, None, f"**{user.display_name}** hasn't linked a letterboxd account."
        return lb_user, user.display_name, None

    if username is not None:
        return username, username, None

    return None, None, f"pick `{label}_user` or `{label}_username`."


def _tastecheck_payload(
    user_a: str,
    label_a: str,
    diary_a: list[dict],
    watchlist_a: list[dict],
    user_b: str,
    label_b: str,
    diary_b: list[dict],
    watchlist_b: list[dict],
) -> dict:
    diary_by_key_a = {_film_key(entry): entry for entry in diary_a}
    diary_by_key_b = {_film_key(entry): entry for entry in diary_b}
    shared_keys = set(diary_by_key_a) & set(diary_by_key_b)

    shared = []
    rated_diffs = []
    for key in shared_keys:
        left = diary_by_key_a[key]
        right = diary_by_key_b[key]
        diff = None
        if left.get("rating") is not None and right.get("rating") is not None:
            diff = abs(float(left["rating"]) - float(right["rating"]))
            rated_diffs.append(diff)
        shared.append({
            "title": left.get("film_title", "Unknown"),
            "year": left.get("year"),
            "left_rating": left.get("rating"),
            "right_rating": right.get("rating"),
            "left_stars": left.get("stars", ""),
            "right_stars": right.get("stars", ""),
            "diff": diff,
            "link": left.get("link") or right.get("link") or "",
        })

    shared.sort(key=lambda item: (item["diff"] is None, item["diff"] or 0, item["title"]))
    agreements = [item for item in shared if item["diff"] is not None]
    disagreements = sorted(
        agreements,
        key=lambda item: (item["diff"], item["title"]),
        reverse=True,
    )

    watchlist_by_key_a = {_film_key(film): film for film in watchlist_a}
    watchlist_by_key_b = {_film_key(film): film for film in watchlist_b}
    shared_watchlist = [
        watchlist_by_key_a[key]
        for key in sorted(
            set(watchlist_by_key_a) & set(watchlist_by_key_b),
            key=lambda k: watchlist_by_key_a[k].get("film_title", ""),
        )
    ]

    base_score = 35 if shared else 15
    overlap_score = min(30, len(shared) * 6)
    watchlist_score = min(20, len(shared_watchlist) * 4)
    rating_score = 0
    avg_diff = None
    if rated_diffs:
        avg_diff = sum(rated_diffs) / len(rated_diffs)
        rating_score = max(0, round(35 * (1 - (avg_diff / 5))))

    score = max(0, min(100, base_score + overlap_score + watchlist_score + rating_score))
    if not shared and not shared_watchlist:
        score = 10

    if score >= 85:
        label = "video store soulmates"
    elif score >= 70:
        label = "double-feature material"
    elif score >= 50:
        label = "solid shelf neighbors"
    elif score >= 30:
        label = "interesting programming meeting"
    else:
        label = "chaotic rental energy"

    return {
        "user_a": user_a,
        "user_b": user_b,
        "label_a": label_a,
        "label_b": label_b,
        "score": score,
        "label": label,
        "shared": shared,
        "agreements": agreements[:3],
        "disagreements": disagreements[:3],
        "shared_watchlist": shared_watchlist[:8],
        "shared_count": len(shared),
        "rated_overlap_count": len(rated_diffs),
        "shared_watchlist_count": len(shared_watchlist),
        "avg_diff": avg_diff,
    }


@lb_group.command(name="link", description="link your letterboxd account to the bot")
@app_commands.describe(username="your letterboxd username")
async def lb_link(interaction: discord.Interaction, username: str):
    await interaction.response.defer(ephemeral=True)
    try:
        valid = await lb_module.validate_username(username)
    except lb_module.LetterboxdError as e:
        await interaction.followup.send(
            f"⚠️ couldn't reach letterboxd right now: {e}", ephemeral=True
        )
        return

    if not valid:
        await interaction.followup.send(
            f"❌ couldn't find a public letterboxd account for **{username}**. "
            "check the username and make sure the account is public.",
            ephemeral=True,
        )
        return

    db.link_lb_account(str(interaction.user.id), username)
    await interaction.followup.send(
        f"✅ linked your letterboxd account: **{username}**\n"
        "use `/lb profile` to see your recent watches.",
        ephemeral=True,
    )


@lb_group.command(name="unlink", description="unlink your letterboxd account")
async def lb_unlink(interaction: discord.Interaction):
    removed = db.unlink_lb_account(str(interaction.user.id))
    if removed:
        await interaction.response.send_message(
            "✅ letterboxd account unlinked.", ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "you don't have a linked letterboxd account.", ephemeral=True
        )


@lb_group.command(
    name="profile",
    description="see recent letterboxd watches for yourself or another member",
)
@app_commands.describe(
    user="a server member (uses their linked lb account)",
    username="or enter a letterboxd username directly",
)
async def lb_profile(
    interaction: discord.Interaction,
    user: discord.Member | None = None,
    username: str | None = None,
):
    await interaction.response.defer()

    if user is not None:
        lb_user = db.get_lb_username(str(user.id))
        discord_tag = str(user)
        if not lb_user:
            await interaction.followup.send(
                f"**{user.display_name}** hasn't linked a letterboxd account. "
                "they can use `/lb link` to connect one.",
            )
            return
    elif username is not None:
        lb_user = username
        discord_tag = None
    else:
        lb_user = db.get_lb_username(str(interaction.user.id))
        discord_tag = str(interaction.user)
        if not lb_user:
            await interaction.followup.send(
                "you haven't linked a letterboxd account yet. "
                "use `/lb link <username>` to connect one.",
            )
            return

    try:
        entries = await lb_module.get_diary(lb_user)
    except lb_module.LetterboxdError as e:
        msg = str(e)
        if "not_found" in msg:
            await interaction.followup.send(f"❌ no letterboxd account found for **{lb_user}**.")
        elif "private" in msg:
            await interaction.followup.send(f"❌ **{lb_user}**'s letterboxd account is private.")
        else:
            await interaction.followup.send(f"⚠️ couldn't fetch letterboxd data: {e}")
        return

    embed = embeds.lb_profile_embed(lb_user, entries, discord_tag=discord_tag)
    await interaction.followup.send(embed=embed)


@lb_group.command(
    name="watchlist",
    description="browse a letterboxd watchlist, roll from it, or import it",
)
@app_commands.describe(
    user="a server member (uses their linked lb account)",
    username="or enter a letterboxd username directly",
)
async def lb_watchlist_cmd(
    interaction: discord.Interaction,
    user: discord.Member | None = None,
    username: str | None = None,
):
    await interaction.response.defer()

    if user is not None:
        lb_user = db.get_lb_username(str(user.id))
        if not lb_user:
            await interaction.followup.send(
                f"**{user.display_name}** hasn't linked a letterboxd account."
            )
            return
    elif username is not None:
        lb_user = username
    else:
        lb_user = db.get_lb_username(str(interaction.user.id))
        if not lb_user:
            await interaction.followup.send(
                "you haven't linked a letterboxd account yet. "
                "use `/lb link <username>` to connect one.",
            )
            return

    try:
        films = await lb_module.get_watchlist(lb_user)
    except lb_module.LetterboxdError as e:
        msg = str(e)
        if "not_found" in msg:
            await interaction.followup.send(f"❌ no letterboxd account found for **{lb_user}**.")
        elif "private" in msg:
            await interaction.followup.send(f"❌ **{lb_user}**'s watchlist is private.")
        else:
            await interaction.followup.send(f"⚠️ couldn't fetch watchlist: {e}")
        return

    total_pages = max(1, -(-len(films) // 5))
    embed = embeds.lb_watchlist_embed(lb_user, films, page=0, total_pages=total_pages)
    view = views.LBWatchlistView(
        bot=bot,
        lb_username=lb_user,
        films=films,
        requesting_user_id=str(interaction.user.id),
        requesting_user_tag=str(interaction.user),
    )
    await interaction.followup.send(embed=embed, view=view)


@lb_group.command(
    name="group",
    description="see what everyone in the server has been watching lately",
)
async def lb_group_cmd(interaction: discord.Interaction):
    await interaction.response.defer()

    accounts = db.get_all_lb_accounts()
    if not accounts:
        await interaction.followup.send(
            "no one has linked a letterboxd account yet. use `/lb link` to be first."
        )
        return

    activity = []
    for account in accounts:
        uid = account["user_id"]
        lb_user = account["lb_username"]
        try:
            member = interaction.guild.get_member(int(uid))
            discord_tag = member.display_name if member else lb_user
        except Exception:
            discord_tag = lb_user

        try:
            entries = await lb_module.get_diary(lb_user)
            activity.append({
                "discord_tag": discord_tag,
                "lb_username": lb_user,
                "entries": entries,
            })
        except lb_module.LetterboxdError:
            continue

    embed = embeds.lb_group_embed(activity)
    await interaction.followup.send(embed=embed)


@lb_group.command(
    name="tastecheck",
    description="compare recent letterboxd taste between two people",
)
@app_commands.describe(
    a_user="first server member",
    b_user="second server member",
    a_username="or enter the first letterboxd username directly",
    b_username="or enter the second letterboxd username directly",
)
async def lb_tastecheck(
    interaction: discord.Interaction,
    a_user: discord.Member | None = None,
    b_user: discord.Member | None = None,
    a_username: str | None = None,
    b_username: str | None = None,
):
    await interaction.response.defer()

    lb_a, label_a, err = _resolve_explicit_lb_target(a_user, a_username, "a")
    if err:
        await interaction.followup.send(err)
        return

    lb_b, label_b, err = _resolve_explicit_lb_target(b_user, b_username, "b")
    if err:
        await interaction.followup.send(err)
        return

    try:
        diary_a, diary_b = await asyncio.gather(
            lb_module.get_diary(lb_a),
            lb_module.get_diary(lb_b),
        )
    except lb_module.LetterboxdError as e:
        await interaction.followup.send(f"⚠️ couldn't fetch letterboxd diaries: {e}")
        return

    watchlist_a = []
    watchlist_b = []
    watchlist_note = None
    try:
        watchlist_a, watchlist_b = await asyncio.gather(
            lb_module.get_watchlist(lb_a),
            lb_module.get_watchlist(lb_b),
        )
    except lb_module.LetterboxdError:
        watchlist_note = "watchlist overlap skipped because at least one watchlist could not be fetched."

    payload = _tastecheck_payload(
        user_a=lb_a,
        label_a=label_a,
        diary_a=diary_a,
        watchlist_a=watchlist_a,
        user_b=lb_b,
        label_b=label_b,
        diary_b=diary_b,
        watchlist_b=watchlist_b,
    )
    embed = embeds.lb_tastecheck_embed(payload, watchlist_note=watchlist_note)
    await interaction.followup.send(embed=embed)


bot.tree.add_command(lb_group)


bot.suckling_restart_process = _restart_process
bot.suckling_run_lb_activity_check = run_lb_activity_check
bot.suckling_lb_activity_summary = _lb_activity_summary


if __name__ == "__main__":
    logger.setup_logging()
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
    bot.run(config.DISCORD_TOKEN)
