import asyncio
import io
import os
import random
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
import picker
import imageops
import game
import logger
import plex
import version
import sixdegrees
import trivia_roulette
import rental as rental_module
import letterboxd as lb_module

MY_WATCHLIST_PAGE_SIZE = 10
LB_ACTIVITY_POST_LIMIT = 20

intents = discord.Intents.default()
intents.message_content = True


class SucklingBot(commands.Bot):
    """Bot subclass that tracks startup time and closes the shared TMDB session on shutdown."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started_at = datetime.now(timezone.utc)

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


ROUND_DURATION_SECONDS = 60
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
        await post_daily_recommendation(bot)
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
    new_items = [
        item for item in candidate_items
        if item["entry_key"] not in seen_keys
    ]
    result["new"] = len(new_items)

    if seed_only:
        db.record_lb_activity_seen_many(
            (
                item["entry_key"],
                item["lb_username"],
                item["entry"].get("film_title", "Unknown"),
                False,
            )
            for item in new_items
        )
        result["seeded"] = len(new_items)
        return result

    if not post:
        return result

    new_items.sort(key=lambda item: item["entry"].get("watch_date", ""))
    post_items = new_items[:limit]
    skipped_items = new_items[limit:]

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


# ---------- helpers ----------

def _needs_disambiguation(results: list[dict]) -> bool:
    if len(results) < 2:
        return False
    top_popularity = results[0].get("popularity", 0)
    second_popularity = results[1].get("popularity", 0)
    if top_popularity == 0:
        return True
    return second_popularity / top_popularity >= 0.1


async def post_daily_recommendation(bot: discord.Client) -> bool:
    channel_id = db.get_daily_rec_channel_id()
    if not channel_id:
        print("[daily-rec] No channel configured — skipping")
        return False

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden) as e:
            print(f"[daily-rec] Couldn't access channel: {e}")
            return False

    excluded = db.recent_rec_ids(within_days=30)
    movie = await picker.pick_random(exclude_ids=excluded)
    if not movie:
        print("[daily-rec] No suitable film found")
        return False

    try:
        details = await tmdb.get_movie_details(movie["id"])
        providers = await tmdb.get_watch_providers(movie["id"], region="US")
    except tmdb.TMDBError as e:
        print(f"[daily-rec] TMDB error: {e}")
        return False

    release_date_dr = details.get("release_date") or ""
    plex_year_dr = int(release_date_dr[:4]) if release_date_dr[:4].isdigit() else None
    plex_avail_dr = await plex.check_availability(details.get("title"), year=plex_year_dr)
    embed = embeds.daily_rec_embed(details, providers, plex_available=bool(plex_avail_dr))
    poster_url_dr = tmdb.poster_url(details.get("poster_path"))
    daily_view = views.FilmCardView(
        bot=bot,
        title=details.get("title", ""),
        year=plex_year_dr,
        tmdb_id=details.get("id"),
        poster_url=poster_url_dr,
        plex_available=bool(plex_avail_dr),
    )

    try:
        await channel.send(embed=embed, view=daily_view)
        db.record_daily_rec(movie["id"], details.get("title", "Unknown"))
        print(f"[daily-rec] Posted: {details.get('title')}")
        return True
    except discord.HTTPException as e:
        print(f"[daily-rec] Failed to post: {e}")
        return False


# ---------- core commands ----------

@bot.tree.command(name="suck", description="suck up a movie and see where to watch it")
@app_commands.describe(
    title="The movie title to search for",
    year="Optional: filter by release year if there are multiple matches",
)
async def suck(interaction: discord.Interaction, title: str, year: int | None = None):
    await interaction.response.defer()

    try:
        results = await tmdb.search_movie(title, year=year)
    except tmdb.TMDBError as e:
        await interaction.followup.send(f"Sorry, TMDB lookup failed: {e}")
        return

    if not results:
        msg = f"No results found for **{title}**"
        if year:
            msg += f" ({year})"
        msg += "."
        await interaction.followup.send(msg)
        return

    if year is not None or not _needs_disambiguation(results):
        top = results[0]
        try:
            details = await tmdb.get_movie_details(top["id"])
            providers = await tmdb.get_watch_providers(top["id"], region="US")
        except tmdb.TMDBError as e:
            await interaction.followup.send(f"Sorry, couldn't load details: {e}")
            return

        release_date = details.get("release_date") or ""
        plex_year = int(release_date[:4]) if release_date[:4].isdigit() else None
        plex_available = await plex.check_availability(details.get("title"), year=plex_year)

        embed = embeds.movie_embed(
            details, providers, in_theaters=False, plex_available=plex_available
        )
        poster_url = tmdb.poster_url(details.get("poster_path"))
        film_view = views.FilmCardView(
            bot=bot,
            title=details.get("title", ""),
            year=plex_year,
            tmdb_id=details.get("id"),
            poster_url=poster_url,
            plex_available=bool(plex_available),
        )
        await interaction.followup.send(embed=embed, view=film_view)
    else:
        view = views.MovieSelectView(results, bot=bot)
        await interaction.followup.send(
            f"Found multiple matches for **{title}**. Pick one:",
            view=view,
        )


# ---------- tracking ----------

@bot.tree.command(
    name="setannouncements",
    description="Set the channel where horror release alerts will post (admin only)",
)
@app_commands.describe(channel="The channel to post announcements in")
@app_commands.default_permissions(manage_guild=True)
async def set_announcements(interaction: discord.Interaction, channel: discord.TextChannel):
    perms = channel.permissions_for(interaction.guild.me)
    if not perms.send_messages or not perms.embed_links:
        await interaction.response.send_message(
            f"⚠️ I don't have permission to send messages or embeds in {channel.mention}. "
            "Please grant me those permissions first.",
            ephemeral=True,
        )
        return

    db.set_announcement_channel_id(channel.id)
    await interaction.response.send_message(
        f"✅ Horror release alerts will now post in {channel.mention}.",
        ephemeral=True,
    )


@bot.tree.command(
    name="setdaily",
    description="Set the channel where daily horror recommendations post (admin only)",
)
@app_commands.describe(channel="The channel to post daily recommendations in")
@app_commands.default_permissions(manage_guild=True)
async def set_daily(interaction: discord.Interaction, channel: discord.TextChannel):
    perms = channel.permissions_for(interaction.guild.me)
    if not perms.send_messages or not perms.embed_links:
        await interaction.response.send_message(
            f"⚠️ I don't have permission to send messages or embeds in {channel.mention}. "
            "Please grant me those permissions first.",
            ephemeral=True,
        )
        return

    db.set_daily_rec_channel_id(channel.id)
    await interaction.response.send_message(
        f"✅ Daily horror recommendations will now post in {channel.mention} at noon.",
        ephemeral=True,
    )


@bot.tree.command(
    name="setlbactivity",
    description="Set the channel where linked Letterboxd activity posts (admin only)",
)
@app_commands.describe(channel="The channel to post Letterboxd activity in")
@app_commands.default_permissions(manage_guild=True)
async def set_lb_activity(interaction: discord.Interaction, channel: discord.TextChannel):
    perms = channel.permissions_for(interaction.guild.me)
    if not perms.send_messages or not perms.embed_links:
        await interaction.response.send_message(
            f"I don't have permission to send messages or embeds in {channel.mention}. "
            "Please grant me those permissions first.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    db.set_lb_activity_channel_id(channel.id)
    seed_result = await run_lb_activity_check(post=False, seed_only=True)
    db.set_lb_activity_enabled(True)
    await interaction.followup.send(
        f"Letterboxd activity will now post in {channel.mention}.\n"
        "I seeded the current feeds first so old watches won't spam the channel: "
        f"{_lb_activity_summary(seed_result)}",
        ephemeral=True,
    )


def _lb_activity_summary(result: dict) -> str:
    if result.get("missing_channel"):
        return "no letterboxd activity channel is set yet."
    return (
        f"checked **{result['fetched']}/{result['accounts']}** linked account(s), "
        f"found **{result['new']}** new entry/entries, "
        f"posted **{result['posted']}**, "
        f"seeded **{result['seeded']}**, "
        f"skipped **{result['skipped']}**."
    )


@bot.tree.command(
    name="track",
    description="Add a movie to the watchlist — get notified when it streams",
)
@app_commands.describe(
    title="The movie title to track",
    year="Optional: filter by release year if there are multiple matches",
)
async def track(interaction: discord.Interaction, title: str, year: int | None = None):
    await interaction.response.defer()

    try:
        results = await tmdb.search_movie(title, year=year)
    except tmdb.TMDBError as e:
        await interaction.followup.send(f"Sorry, TMDB lookup failed: {e}")
        return

    if not results:
        msg = f"No results found for **{title}**"
        if year:
            msg += f" ({year})"
        msg += "."
        await interaction.followup.send(msg)
        return

    user_tag = str(interaction.user)

    if year is not None or not _needs_disambiguation(results):
        top = results[0]
        movie_title = top.get("title", "Unknown")
        release_date = top.get("release_date", "")
        movie_year = release_date[:4] if release_date else "—"

        added = db.add_tracked_movie(top["id"], movie_title, user_tag)
        if not added:
            await interaction.followup.send(
                f"**{movie_title} ({movie_year})** is already on the tracked list."
            )
            return

        msg = await views._build_track_response(top["id"], movie_title, movie_year)
        await interaction.followup.send(msg)
    else:
        view = views.TrackSelectView(results, added_by=user_tag)
        await interaction.followup.send(
            f"Found multiple matches for **{title}**. Pick which one to track:",
            view=view,
        )


@bot.tree.command(name="untrack", description="Remove a movie from the watchlist")
@app_commands.describe(title="The movie title to untrack")
async def untrack(interaction: discord.Interaction, title: str):
    await interaction.response.defer()

    tracked = db.list_tracked_movies()
    title_lower = title.lower()
    matches = [m for m in tracked if title_lower in m["title"].lower()]

    if not matches:
        await interaction.followup.send(
            f"No tracked movie matches **{title}**. Use `/tracked` to see the list."
        )
        return

    if len(matches) > 1:
        names = ", ".join(f"**{m['title']}**" for m in matches[:5])
        await interaction.followup.send(
            f"Multiple tracked movies match **{title}**: {names}. Be more specific."
        )
        return

    match = matches[0]
    db.remove_tracked_movie(match["tmdb_id"])
    await interaction.followup.send(f"✅ Stopped tracking **{match['title']}**.")


@bot.tree.command(name="tracked", description="Show all movies on the watchlist")
async def tracked(interaction: discord.Interaction):
    movies = db.list_tracked_movies()

    if not movies:
        await interaction.response.send_message(
            "No movies are being tracked yet. Add some with `/track`.",
            ephemeral=True,
        )
        return

    lines = [f"• **{m['title']}** — added by {m['added_by']}" for m in movies[:25]]
    extra = len(movies) - 25
    if extra > 0:
        lines.append(f"…and {extra} more.")

    embed = discord.Embed(
        title=f"Tracked Movies ({len(movies)})",
        description="\n".join(lines),
        color=0x8B0000,
    )
    await interaction.response.send_message(embed=embed)


# ---------- recommendations ----------

@bot.tree.command(name="roll", description="Get a random horror movie recommendation")
@app_commands.describe(
    decade="Optional: filter by decade (e.g. '1980s', '2010s')",
    runtime="Optional: short (<90min), medium (90-120min), or long (>120min)",
)
@app_commands.choices(runtime=[
    app_commands.Choice(name="short (under 90 min)", value="short"),
    app_commands.Choice(name="medium (90-120 min)", value="medium"),
    app_commands.Choice(name="long (over 120 min)", value="long"),
])
async def roll(
    interaction: discord.Interaction,
    decade: str | None = None,
    runtime: app_commands.Choice[str] | None = None,
):
    await interaction.response.defer()

    runtime_val = runtime.value if runtime else None
    movie = await picker.pick_random(decade=decade, runtime=runtime_val)
    if not movie:
        await interaction.followup.send(
            "🤷 Couldn't find anything matching those filters. Try loosening them."
        )
        return

    try:
        details = await tmdb.get_movie_details(movie["id"])
        providers = await tmdb.get_watch_providers(movie["id"], region="US")
    except tmdb.TMDBError as e:
        await interaction.followup.send(f"Sorry, TMDB lookup failed: {e}")
        return

    release_date = details.get("release_date") or ""
    plex_year = int(release_date[:4]) if release_date[:4].isdigit() else None
    plex_available = await plex.check_availability(details.get("title"), year=plex_year)
    embed = embeds.roll_embed(details, providers, plex_available=bool(plex_available))
    poster_url = tmdb.poster_url(details.get("poster_path"))
    film_view = views.FilmCardView(
        bot=bot,
        title=details.get("title", ""),
        year=plex_year,
        tmdb_id=details.get("id"),
        poster_url=poster_url,
        plex_available=bool(plex_available),
    )
    await interaction.followup.send(embed=embed, view=film_view)


# ---------- guessing game ----------

@bot.tree.command(name="guess", description="Start a horror movie guessing round")
@app_commands.describe(
    difficulty="Easy = full still (1 pt). Hard = cropped poster (2 pts). Default: random.",
)
@app_commands.choices(difficulty=[
    app_commands.Choice(name="easy (full still, 1 point)", value="easy"),
    app_commands.Choice(name="hard (cropped poster, 2 points)", value="hard"),
])
async def guess(
    interaction: discord.Interaction,
    difficulty: app_commands.Choice[str] | None = None,
):
    channel_id = interaction.channel.id

    if game.get_round(channel_id):
        await interaction.response.send_message(
            "🎬 A round is already active in this channel! Wait for it to finish or use `/giveup`.",
            ephemeral=True,
        )
        return
    
    if trivia_roulette.get_round(channel_id):
        await interaction.response.send_message(
            "🎲 A trivia round is active in this channel. Wait for it to finish or use `/giveup`.",
            ephemeral=True,
        )
        return

    await interaction.response.defer()

    diff_val = difficulty.value if difficulty else random.choice(["easy", "hard"])

    # Easy = full still (backdrop). Hard = cropped poster.
    movie = None
    image_url = None

    for attempt in range(8):
        candidate = await picker.pick_random()
        if not candidate:
            continue

        if diff_val == "easy":
            try:
                images = await tmdb.get_movie_images(candidate["id"])
            except tmdb.TMDBError:
                continue
            backdrop_url = tmdb.pick_backdrop_url(images)
            if backdrop_url:
                movie = candidate
                image_url = backdrop_url
                break
        else:  # hard
            if candidate.get("poster_path"):
                movie = candidate
                image_url = tmdb.poster_url(candidate["poster_path"])
                break

    if not movie or not image_url:
        await interaction.followup.send("🤷 Couldn't find a suitable image. Try again.")
        return

    image_bytes = await imageops.download_image(image_url)
    if not image_bytes:
        await interaction.followup.send("🤷 Couldn't download the image. Try again.")
        return

    try:
        if diff_val == "easy":
            # Full still, no processing
            puzzle_bytes = image_bytes
        else:
            # Cropped poster (medium-style crop from old code)
            puzzle_bytes = imageops.make_puzzle(image_bytes, difficulty="medium")
    except Exception as e:
        await interaction.followup.send(f"🤷 Couldn't process the image: {e}")
        return

    title = movie.get("title", "Unknown")
    points = 2 if diff_val == "hard" else 1
    round_obj = game.GuessRound(
        channel_id=channel_id,
        movie_id=movie["id"],
        title=title,
        started_at=datetime.now(timezone.utc),
        started_by=str(interaction.user),
        end_event=asyncio.Event(),
        difficulty=diff_val,
    )
    if not game.start_round(round_obj):
        await interaction.followup.send(
            "🎬 A round just started in this channel, wait for it to finish.",
            ephemeral=True,
        )
        return

    file = discord.File(io.BytesIO(puzzle_bytes), filename="puzzle.jpg")
    type_label = "movie still" if diff_val == "easy" else "cropped poster"
    intro_embed = discord.Embed(
        title=f"🎬 Guess this {type_label}!",
        description=(
            f"You have **{ROUND_DURATION_SECONDS} seconds**. "
            "Reply in chat with the title — first correct guess wins.\n\n"
            f"*Difficulty: {diff_val} · Worth **{points}** point{'s' if points > 1 else ''}*"
        ),
        color=0x8B0000,
    )
    intro_embed.set_image(url="attachment://puzzle.jpg")

    await interaction.followup.send(embed=intro_embed, file=file)

    try:
        await asyncio.wait_for(round_obj.end_event.wait(), timeout=ROUND_DURATION_SECONDS)
    except asyncio.TimeoutError:
        pass

    round_obj.revealed = True
    game.end_round(channel_id)

    reveal_embed = discord.Embed(
        title=f"The answer was: **{title}**",
        url=f"https://www.themoviedb.org/movie/{movie['id']}",
        color=0x8B0000,
    )
    reveal_embed.set_image(url=image_url)

    if round_obj.winner_id:
        new_total = db.increment_guess_score(
            round_obj.winner_id, round_obj.winner_tag, points=points
        )
        reveal_embed.description = (
            f"🏆 <@{round_obj.winner_id}> got it! "
            f"(+{points} point{'s' if points > 1 else ''} — total: **{new_total}**)"
        )
    else:
        reveal_embed.description = "⏰ Time's up — nobody guessed it."

    await interaction.channel.send(embed=reveal_embed)


@bot.tree.command(name="play", description="Start a trivia roulette round")
async def play(interaction: discord.Interaction):
    channel_id = interaction.channel.id

    # Cross-game guards
    if trivia_roulette.get_round(channel_id):
        await interaction.response.send_message(
            "🎲 A trivia round is already active in this channel. Wait for it to finish or use `/giveup`.",
            ephemeral=True,
        )
        return
    if game.get_round(channel_id):
        await interaction.response.send_message(
            "🎬 A /guess round is active in this channel. Wait for it to finish or use `/giveup`.",
            ephemeral=True,
        )
        return
    if sixdegrees.get_round(channel_id):
        await interaction.response.send_message(
            "🎬 A /six round is active in this channel. Wait for it to finish or use `/giveup`.",
            ephemeral=True,
        )
        return

    pick = trivia_roulette.pick_random_entry()
    if pick is None:
        await interaction.response.send_message(
            "⚠️ No trivia content loaded — check the assets folder.",
            ephemeral=True,
        )
        return

    category, entry = pick

    await interaction.response.defer()

    round_obj = trivia_roulette.TriviaRound(
        channel_id=channel_id,
        category=category,
        prompt=entry["prompt"],
        answer=entry["answer"],
        year=entry.get("year"),
        aliases=entry.get("aliases", []),
        started_at=datetime.now(timezone.utc),
        started_by=str(interaction.user),
        end_event=asyncio.Event(),
    )
    if not trivia_roulette.start_round(round_obj):
        await interaction.followup.send(
            "🎲 A round just started in this channel, wait for it to finish.",
            ephemeral=True,
        )
        return

    intro_embed = embeds.trivia_prompt_embed(
        category=category,
        prompt=entry["prompt"],
        started_by=str(interaction.user),
    )
    await interaction.followup.send(embed=intro_embed)

    try:
        await asyncio.wait_for(
            round_obj.end_event.wait(),
            timeout=trivia_roulette.ROUND_DURATION_SECONDS,
        )
    except asyncio.TimeoutError:
        pass

    round_obj.revealed = True
    trivia_roulette.end_round(channel_id)

    if round_obj.winner_id:
        new_total = db.increment_guess_score(
            round_obj.winner_id, round_obj.winner_tag, points=1
        )
        reveal_embed = embeds.trivia_reveal_embed(
            category=category,
            answer=round_obj.answer,
            year=round_obj.year,
            winner_tag=round_obj.winner_tag,
            new_total=new_total,
        )
    else:
        reveal_embed = embeds.trivia_reveal_embed(
            category=category,
            answer=round_obj.answer,
            year=round_obj.year,
        )

    await interaction.channel.send(embed=reveal_embed)

@bot.tree.command(name="giveup", description="End the current guessing round in this channel")
async def giveup(interaction: discord.Interaction):
    channel_id = interaction.channel.id

    # Check for an active poster/still guess round
    guess_round = game.get_round(channel_id)
    if guess_round and not guess_round.revealed:
        guess_round.end_event.set()
        await interaction.response.send_message("🏳️ Revealing answer...", ephemeral=True)
        return

    # Check for an active six degrees round
    six_round = sixdegrees.get_round(channel_id)
    if six_round and not six_round.revealed:
        six_round.end_event.set()
        await interaction.response.send_message("🏳️ Ending the round...", ephemeral=True)
        return

    # Check for an active trivia roulette round
    trivia_round = trivia_roulette.get_round(channel_id)
    if trivia_round and not trivia_round.revealed:
        trivia_round.end_event.set()
        await interaction.response.send_message("🏳️ Revealing answer...", ephemeral=True)
        return

    await interaction.response.send_message(
        "No active round in this channel.", ephemeral=True
    )


@bot.tree.command(name="leaderboard", description="Show the top horror-guessing scorers")
async def leaderboard(interaction: discord.Interaction):
    scores = db.get_leaderboard(limit=10)

    if not scores:
        await interaction.response.send_message(
            "🏆 No scores yet! Start a round with `/guess`!"
        )
        return

    lines = []
    medals = ["🥇", "🥈", "🥉"]
    for i, score in enumerate(scores):
        prefix = medals[i] if i < 3 else f"#{i+1}"
        lines.append(
            f"{prefix} **{score['user_tag']}** — {score['points']} point(s) "
            f"({score['wins']} win{'s' if score['wins'] != 1 else ''})"
        )

    embed = discord.Embed(
        title="🏆 Horror Guess Leaderboard",
        description="\n".join(lines),
        color=0x8B0000,
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="six", description="Start a Six Degrees of Separation round")
async def six_command(interaction: discord.Interaction):
    channel_id = interaction.channel.id

    if sixdegrees.get_round(channel_id):
        await interaction.response.send_message(
            "🎬 A Six Degrees round is already active! Wait for it to finish.",
            ephemeral=True,
        )
        return

    if trivia_roulette.get_round(channel_id):
            await interaction.response.send_message(
                "🎲 A trivia round is active in this channel. Wait for it to finish or use `/giveup`.",
                ephemeral=True,
            )
            return

    await interaction.response.defer()

    actors = await sixdegrees.pick_two_actors()
    if not actors:
        await interaction.followup.send("🤷 Couldn't load actor pool. Try again.")
        return

    actor_a, actor_b = actors
    round_obj = sixdegrees.SixRound(
        channel_id=channel_id,
        actor_a_id=actor_a["id"],
        actor_a_name=actor_a["name"],
        actor_b_id=actor_b["id"],
        actor_b_name=actor_b["name"],
        started_at=datetime.now(timezone.utc),
        end_event=asyncio.Event(),
    )
    if not sixdegrees.start_round(round_obj):
        await interaction.followup.send(
            "🎬 A round just started — wait for it to finish.", ephemeral=True
        )
        return

    minutes = sixdegrees.ROUND_DURATION_SECONDS // 60
    intro = discord.Embed(
        title="🎬 Six Degrees of Separation",
        description=(
            f"**Connect:** {actor_a['name']} ↔ {actor_b['name']}\n\n"
            f"You have **{minutes} minutes**. Submit a chain in chat:\n"
            f"`Actor -> Film -> Actor -> Film -> ...`\n\n"
            f"First valid chain wins. Shorter chains earn more points "
            f"(1 film = 5 pts, 2 = 4, 3 = 3, 4 = 2, 5+ = 1)."
        ),
        color=0x8B0000,
    )
    await interaction.followup.send(embed=intro)

    try:
        await asyncio.wait_for(
            round_obj.end_event.wait(),
            timeout=sixdegrees.ROUND_DURATION_SECONDS,
        )
    except asyncio.TimeoutError:
        pass

    round_obj.revealed = True
    sixdegrees.end_round(channel_id)

    if round_obj.winner_id and round_obj.winning_chain:
        points = sixdegrees.points_for(round_obj.winning_film_count)
        new_total = db.increment_six_score(
            round_obj.winner_id, round_obj.winner_tag, points=points
        )
        chain_str = " → ".join(round_obj.winning_chain)
        win_embed = discord.Embed(
            title=f"🏆 {round_obj.winner_tag} wins!",
            description=(
                f"**Chain ({round_obj.winning_film_count} film(s)):**\n{chain_str}\n\n"
                f"**+{points} point(s)** — total: **{new_total}**"
            ),
            color=0x8B0000,
        )
        await interaction.channel.send(embed=win_embed)
    else:
        await interaction.channel.send(
            f"⏰ Time's up. Nobody connected **{actor_a['name']}** to **{actor_b['name']}**."
        )


@bot.tree.command(name="sixleaderboard", description="Show the top Six Degrees scorers")
async def sixleaderboard(interaction: discord.Interaction):
    scores = db.get_six_leaderboard(limit=10)

    if not scores:
        await interaction.response.send_message(
            "🏆 No scores yet — start a round with `/six`!"
        )
        return

    lines = []
    medals = ["🥇", "🥈", "🥉"]
    for i, score in enumerate(scores):
        prefix = medals[i] if i < 3 else f"#{i+1}"
        lines.append(
            f"{prefix} **{score['user_tag']}** — {score['points']} point(s) "
            f"({score['wins']} win{'s' if score['wins'] != 1 else ''})"
        )

    embed = discord.Embed(
        title="🏆 Six Degrees Leaderboard",
        description="\n".join(lines),
        color=0x8B0000,
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="info", description="show info about the bot")
async def info(interaction: discord.Interaction):
    await interaction.response.defer()

    uptime_seconds = (datetime.now(timezone.utc) - bot.started_at).total_seconds()
    guild_count = len(bot.guilds)

    logo = discord.File("assets/logo.png", filename="logo.png")
    embed = embeds.info_embed(version.VERSION, uptime_seconds, guild_count)

    await interaction.followup.send(embed=embed, file=logo)


# ---------- rb9 library ----------

@bot.tree.command(name="rb9", description="Pick a random movie from the RB9 library")
async def rb9_pick(interaction: discord.Interaction):
    await interaction.response.defer()

    try:
        movie = await plex.pick_random_movie()
    except plex.PlexError as e:
        await interaction.followup.send(f"⚠️ rb9 error: {e}", ephemeral=True)
        return
    except Exception as e:
        await interaction.followup.send(
            f"⚠️ Unexpected error: {e}", ephemeral=True
        )
        return

    if not movie:
        await interaction.followup.send("📀 No movies found in the library.")
        return

    embed = embeds.rb9_pick_embed(movie)
    film_view = views.FilmCardView(
        bot=bot,
        title=movie.get("title", ""),
        year=movie.get("year"),
        poster_url=movie.get("thumb_url"),
        plex_available=True,
        plex_movie=movie,
    )
    await interaction.followup.send(embed=embed, view=film_view)


@bot.tree.command(name="rb9stats", description="Overall stats for the rb9 library")
async def rb9stats(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        stats = await plex.get_library_summary()
    except plex.PlexError as e:
        await interaction.followup.send(f"⚠️ rb9 error: {e}", ephemeral=True)
        return
    embed = embeds.rb9_stats_embed(stats)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="rb9biggest", description="The longest film in the rb9 library")
async def rb9biggest(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        movie = await plex.get_longest_movie()
    except plex.PlexError as e:
        await interaction.followup.send(f"⚠️ rb9 error: {e}", ephemeral=True)
        return
    if not movie:
        await interaction.followup.send("📀 No films with runtime data found.")
        return
    embed = embeds.rb9_single_movie_embed(movie, "Longest film in the library", "🦣")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="rb9shortest", description="The shortest film in the rb9 library")
async def rb9shortest(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        movie = await plex.get_shortest_movie()
    except plex.PlexError as e:
        await interaction.followup.send(f"⚠️ rb9 error: {e}", ephemeral=True)
        return
    if not movie:
        await interaction.followup.send("📀 No films with runtime data found.")
        return
    embed = embeds.rb9_single_movie_embed(movie, "Shortest film in the library", "🐭")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="rb9oldest", description="The oldest film in the rb9 library")
async def rb9oldest(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        movie = await plex.get_oldest_movie()
    except plex.PlexError as e:
        await interaction.followup.send(f"⚠️ rb9 error: {e}", ephemeral=True)
        return
    if not movie:
        await interaction.followup.send("📀 No films with year data found.")
        return
    embed = embeds.rb9_single_movie_embed(movie, "Oldest film in the library", "🦴")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="rb9newest", description="The most recently added film in the rb9 library")
async def rb9newest(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        movie = await plex.get_newest_movie()
    except plex.PlexError as e:
        await interaction.followup.send(f"⚠️ rb9 error: {e}", ephemeral=True)
        return
    if not movie:
        await interaction.followup.send("📀 No films found.")
        return
    embed = embeds.rb9_single_movie_embed(movie, "Most recently added", "✨")
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="rb9totalruntime", description="How long it'd take to watch the entire rb9 library")
async def rb9totalruntime(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        stats = await plex.get_total_runtime()
    except plex.PlexError as e:
        await interaction.followup.send(f"⚠️ rb9 error: {e}", ephemeral=True)
        return
    embed = embeds.rb9_total_runtime_embed(stats)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="rb9decade", description="Films per decade in the rb9 library")
async def rb9decade(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        decades = await plex.get_decade_breakdown()
    except plex.PlexError as e:
        await interaction.followup.send(f"⚠️ rb9 error: {e}", ephemeral=True)
        return
    embed = embeds.rb9_decade_embed(decades)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="rb9genre", description="Top genres in the rb9 library")
async def rb9genre(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        genres = await plex.get_genre_breakdown(top_n=10)
    except plex.PlexError as e:
        await interaction.followup.send(f"⚠️ rb9 error: {e}", ephemeral=True)
        return
    embed = embeds.rb9_genre_embed(genres)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="rb9randomscene", description="A random film + backdrop from the rb9 library")
async def rb9randomscene(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        scene = await plex.get_random_scene()
    except plex.PlexError as e:
        await interaction.followup.send(f"⚠️ rb9 error: {e}", ephemeral=True)
        return
    if not scene:
        await interaction.followup.send("📀 No films with art data found.")
        return
    embed = embeds.rb9_random_scene_embed(scene)
    scene_view = views.FilmCardView(
        bot=bot,
        title=scene.get("title", ""),
        year=scene.get("year"),
        poster_url=scene.get("thumb_url"),
        plex_available=True,
        plex_movie=scene,
    )
    await interaction.followup.send(embed=embed, view=scene_view)


# ---------- rentals ----------

@bot.tree.command(name="rent", description="rent a random movie from the rb9 library — 48 hours to watch it")
async def rent(interaction: discord.Interaction):
    user_id = str(interaction.user.id)

    # Fast pre-check: already has an active rental?
    existing = db.get_active_rental(user_id)
    if existing:
        title = existing.get("title", "a film")
        due_at_iso = existing.get("due_at", "")
        try:
            due = datetime.fromisoformat(due_at_iso)
            due_ts = int(due.timestamp())
            due_str = f" (due <t:{due_ts}:R>)"
        except (ValueError, TypeError):
            due_str = ""
        await interaction.response.send_message(
            f"you already have **{title}** checked out{due_str}. "
            "use `/return` to return it before renting something new.",
            ephemeral=True,
        )
        return

    warning_view = views.RentWarningView(
        bot=bot,
        user_id=user_id,
        user_name=str(interaction.user),
    )
    await interaction.response.send_message(
        "📼 **heads up before you rent**\n\n"
        "once you confirm a rental, the 48-hour clock starts immediately. "
        "you can re-roll up to **2 times** if you get something you've seen, "
        "but after that the film is locked in.\n\n"
        "ready to grab something from the shelf?",
        view=warning_view,
        ephemeral=True,
    )


@bot.tree.command(name="return", description="return your current rental and post a review to the forum")
@app_commands.describe(
    rating="your rating out of 10 (1-10)",
    recommend="would you recommend this to the group?",
    thoughts="your review (optional but encouraged)",
)
async def return_film(
    interaction: discord.Interaction,
    rating: int,
    recommend: bool,
    thoughts: str | None = None,
):
    await interaction.response.defer(ephemeral=True)

    user_id = str(interaction.user.id)

    if not 1 <= rating <= 10:
        await interaction.followup.send(
            "⚠️ rating has to be between 1 and 10.", ephemeral=True
        )
        return

    rental = db.get_active_rental(user_id)
    if not rental:
        await interaction.followup.send(
            "you don't have an active rental. use `/rent` to grab something.",
            ephemeral=True,
        )
        return

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    late_fee = rental_module.compute_late_fee(rental["due_at"], now_iso)

    db.mark_rental_returned(
        rental_id=rental["id"],
        returned_at=now_iso,
        rating=rating,
        thoughts=thoughts,
        recommended=recommend,
        late_fee_dollars=late_fee,
    )

    # Reload the updated rental record so edit_thread_returned has all fields
    updated_rental = db.get_rental_by_id(rental["id"])

    # Edit the forum thread
    await rental_module.edit_thread_returned(bot, updated_rental)

    title = rental.get("title", "your film")
    late_note = f"\nlate fee: **${late_fee:.2f}**" if late_fee > 0 else ""
    rec_note = "recommended" if recommend else "not recommended"

    await interaction.followup.send(
        f"✅ **{title}** returned. rating: {rating}/10, {rec_note}.{late_note}\n"
        f"-# review posted to the forum.",
        ephemeral=True,
    )


@bot.tree.command(name="myrental", description="check your current rental and time remaining")
async def myrental(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    rental = db.get_active_rental(user_id)

    if not rental:
        await interaction.response.send_message(
            "you don't have anything checked out right now. use `/rent` to grab something.",
            ephemeral=True,
        )
        return

    embed = embeds.rental_status_embed(rental)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="extend", description="extend your active rental by 24 hours")
async def extend(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    rental = db.get_active_rental(user_id)
    if not rental:
        await interaction.response.send_message(
            "you don't have an active rental to extend.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    _, message = await rental_module.extend_rental(
        bot=bot,
        user_id=user_id,
        rental_id=rental["id"],
    )
    await interaction.followup.send(message, ephemeral=True)


@bot.tree.command(name="latefees", description="see who owes the store money")
async def latefees(interaction: discord.Interaction):
    await interaction.response.defer()
    rows = db.get_late_fees_leaderboard(limit=10)
    embed = embeds.late_fees_embed(rows)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="rentalstats", description="your rental history and stats")
@app_commands.describe(user="optional: check another user's stats")
async def rentalstats(
    interaction: discord.Interaction,
    user: discord.Member | None = None,
):
    await interaction.response.defer()
    target = user or interaction.user
    history = db.get_user_rental_history(str(target.id))
    embed = embeds.rental_stats_embed(history, str(target))
    await interaction.followup.send(embed=embed)


# ---------- admin ----------

@bot.tree.command(
    name="setreviews",
    description="set the forum channel for rental reviews (admin only)",
)
@app_commands.describe(channel="the forum channel where rental reviews will post")
@app_commands.default_permissions(manage_guild=True)
async def set_reviews(
    interaction: discord.Interaction,
    channel: discord.ForumChannel,
):
    perms = channel.permissions_for(interaction.guild.me)
    if not perms.create_public_threads or not perms.send_messages_in_threads:
        await interaction.response.send_message(
            f"⚠️ i need **create public threads** and **send messages in threads** "
            f"permissions in {channel.mention}. grant those first then try again.",
            ephemeral=True,
        )
        return

    db.set_reviews_channel_id(channel.id)

    # Auto-detect tags
    rental_tag = next(
        (t for t in channel.available_tags if t.name.lower() == "rental"), None
    )
    rec_tag = next(
        (t for t in channel.available_tags
         if t.name.lower() in ("recommendation", "recommended", "recommend")),
        None,
    )

    if rental_tag:
        db.set_rental_tag_id(rental_tag.id)
    if rec_tag:
        db.set_recommendation_tag_id(rec_tag.id)

    tag_note = ""
    if not rental_tag:
        tag_note += "\n⚠️ no **rental** tag found on this forum. create it in the forum settings and run `/setreviews` again."
    if not rec_tag:
        tag_note += "\n⚠️ no **recommendation** tag found. create it in the forum settings and run `/setreviews` again."

    found = []
    if rental_tag:
        found.append("rental")
    if rec_tag:
        found.append("recommendation")
    found_str = f" tags found: {', '.join(found)}." if found else ""

    await interaction.response.send_message(
        f"✅ rental reviews will post in {channel.mention}.{found_str}{tag_note}",
        ephemeral=True,
    )


@bot.tree.command(
    name="cancelrental",
    description="cancel a user's active rental with no late fee (admin only)",
)
@app_commands.describe(
    user="the user whose rental to cancel",
    reason="optional reason (shown in the forum thread)",
)
@app_commands.default_permissions(manage_guild=True)
async def cancel_rental(
    interaction: discord.Interaction,
    user: discord.Member,
    reason: str | None = None,
):
    await interaction.response.defer(ephemeral=True)

    rental = db.get_active_rental(str(user.id))
    if not rental:
        await interaction.followup.send(
            f"**{user}** doesn't have an active rental.", ephemeral=True
        )
        return

    db.cancel_rental_by_id(rental["id"])
    await rental_module.edit_thread_cancelled(bot, rental, reason)

    # DM the user
    reason_str = f" reason: {reason}" if reason else ""
    await rental_module._send_dm(
        bot,
        str(user.id),
        f"📼 your rental of **{rental['title']}** was cancelled by an admin.{reason_str}",
    )

    await interaction.followup.send(
        f"✅ cancelled **{rental['title']}** for **{user}**.{reason_str}",
        ephemeral=True,
    )


@bot.tree.command(
    name="assignrental",
    description="assign an rb9 rental to a user (admin only)",
)
@app_commands.describe(
    user="the user to assign the rental to",
    title="the exact rb9 library title",
    year="optional release year to disambiguate",
)
@app_commands.default_permissions(manage_guild=True)
async def assign_rental(
    interaction: discord.Interaction,
    user: discord.Member,
    title: str,
    year: int | None = None,
):
    await interaction.response.defer(ephemeral=True)

    existing = db.get_active_rental(str(user.id))
    if existing:
        await interaction.followup.send(
            f"**{user}** already has **{existing['title']}** checked out.",
            ephemeral=True,
        )
        return

    if not db.get_reviews_channel_id():
        await interaction.followup.send(
            "the reviews forum hasn't been configured yet. run `/setreviews` first.",
            ephemeral=True,
        )
        return

    try:
        movie = await plex.find_movie_by_title(title, year=year)
    except plex.PlexError as e:
        await interaction.followup.send(f"rb9 error: {e}", ephemeral=True)
        return

    if not movie:
        year_note = f" ({year})" if year else ""
        await interaction.followup.send(
            f"couldn't find **{title}{year_note}** in the rb9 library.",
            ephemeral=True,
        )
        return

    now = datetime.now(timezone.utc)
    due_at = rental_module.compute_due_at(now)
    rental_id = db.create_rental(
        user_id=str(user.id),
        user_name=str(user),
        plex_key=movie["rating_key"],
        title=movie["title"],
        year=movie.get("year"),
        poster_url=movie.get("thumb_url"),
        rented_at=now.isoformat(),
        due_at=due_at.isoformat(),
        rerolls_used=0,
        initiated_by="admin",
    )

    thread_ok = await rental_module.create_forum_thread(
        bot=bot,
        rental_id=rental_id,
        movie=movie,
        user_tag=str(user),
        due_at=due_at,
    )

    due_ts = int(due_at.timestamp())
    thread_note = ""
    if thread_ok:
        rental = db.get_rental_by_id(rental_id)
        if rental and rental.get("thread_id"):
            thread_note = f" thread: <#{rental['thread_id']}>."

    await rental_module._send_dm(
        bot,
        str(user.id),
        f"you've been assigned **{movie['title']} ({movie.get('year', '?')})** "
        f"from the rb9 library. it's due <t:{due_ts}:F> (<t:{due_ts}:R>). "
        "use `/return` when you're done.",
    )

    await interaction.followup.send(
        f"assigned **{movie['title']} ({movie.get('year', '?')})** to **{user}**. "
        f"due <t:{due_ts}:R>.{thread_note}",
        ephemeral=True,
    )


@bot.tree.command(
    name="version",
    description="Show the bot's current version (admin only)",
)
@app_commands.default_permissions(manage_guild=True)
async def version_command(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"🎬 **sucklingbot** v{version.VERSION}",
        ephemeral=True,
    )


@bot.tree.command(
    name="restart",
    description="Restart the bot process (admin only)",
)
@app_commands.default_permissions(manage_guild=True)
async def restart_command(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Restarting sucklingbot...",
        ephemeral=True,
    )
    asyncio.create_task(_restart_process())


@bot.tree.command(
    name="toggle",
    description="Enable or disable an auto-posting feature (admin only)",
)
@app_commands.describe(
    feature="Which feature to toggle",
    enabled="True to enable, False to disable",
)
@app_commands.choices(feature=[
    app_commands.Choice(name="streaming announcements", value="announcements"),
    app_commands.Choice(name="daily recommendation", value="daily"),
    app_commands.Choice(name="letterboxd activity", value="lb_activity"),
])
@app_commands.default_permissions(manage_guild=True)
async def toggle(
    interaction: discord.Interaction,
    feature: app_commands.Choice[str],
    enabled: bool,
):
    if feature.value == "lb_activity":
        await interaction.response.defer(ephemeral=True)
        channel_id = db.get_lb_activity_channel_id()
        if enabled and not channel_id:
            db.set_lb_activity_enabled(False)
            await interaction.followup.send(
                "No Letterboxd activity channel is set yet. Use `/setlbactivity` first.",
                ephemeral=True,
            )
            return

        seed_note = ""
        if enabled:
            seed_result = await run_lb_activity_check(post=False, seed_only=True)
            seed_note = f" Seeded current feeds first: {_lb_activity_summary(seed_result)}"

        db.set_lb_activity_enabled(enabled)
        await interaction.followup.send(
            f"{'Enabled' if enabled else 'Disabled'} **letterboxd activity**.{seed_note}",
            ephemeral=True,
        )
        return

    if feature.value == "announcements":
        db.set_announcements_enabled(enabled)
        channel_id = db.get_announcement_channel_id()
        channel_note = ""
        if enabled and not channel_id:
            channel_note = " ⚠️ No announcement channel set yet — use `/setannouncements`."
        await interaction.response.send_message(
            f"{'✅ Enabled' if enabled else '🔕 Disabled'} **streaming announcements**.{channel_note}",
            ephemeral=True,
        )
    elif feature.value == "daily":
        db.set_daily_rec_enabled(enabled)
        channel_id = db.get_daily_rec_channel_id()
        channel_note = ""
        if enabled and not channel_id:
            channel_note = " ⚠️ No daily-rec channel set yet — use `/setdaily`."
        await interaction.response.send_message(
            f"{'✅ Enabled' if enabled else '🔕 Disabled'} **daily horror recommendation**.{channel_note}",
            ephemeral=True,
        )


@bot.tree.command(
    name="checknow",
    description="Manually trigger the streaming check, dry-run (admin only)",
)
@app_commands.default_permissions(manage_guild=True)
async def checknow(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    result = await tracker.run_check(bot=bot, dry_run=True)
    await interaction.followup.send(result.to_discord_summary(), ephemeral=True)


@bot.tree.command(
    name="checknowlive",
    description="Manually trigger the streaming check and POST announcements (admin only)",
)
@app_commands.default_permissions(manage_guild=True)
async def checknowlive(interaction: discord.Interaction):
    channel_id = db.get_announcement_channel_id()
    if not channel_id:
        await interaction.response.send_message(
            "⚠️ No announcement channel set. Use `/setannouncements` first.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    result = await tracker.run_check(bot=bot, dry_run=False)
    await interaction.followup.send(result.to_discord_summary(), ephemeral=True)


@bot.tree.command(
    name="dailynow",
    description="Manually trigger today's horror recommendation post (admin only)",
)
@app_commands.default_permissions(manage_guild=True)
async def dailynow(interaction: discord.Interaction):
    channel_id = db.get_daily_rec_channel_id()
    if not channel_id:
        await interaction.response.send_message(
            "⚠️ No daily-rec channel set. Use `/setdaily` first.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    ok = await post_daily_recommendation(bot)
    if ok:
        await interaction.followup.send("✅ Daily recommendation posted.", ephemeral=True)
    else:
        await interaction.followup.send(
            "⚠️ Failed to post — see PowerShell for details.", ephemeral=True
        )


@bot.tree.command(
    name="lbactivitynow",
    description="Manually check linked Letterboxd activity (admin only)",
)
@app_commands.describe(post="True to post new activity; false only reports the count")
@app_commands.default_permissions(manage_guild=True)
async def lbactivitynow(interaction: discord.Interaction, post: bool = False):
    if post and not db.get_lb_activity_channel_id():
        await interaction.response.send_message(
            "No Letterboxd activity channel is set. Use `/setlbactivity` first.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    result = await run_lb_activity_check(post=post)
    verb = "Posted" if post else "Dry run complete"
    await interaction.followup.send(
        f"{verb}. {_lb_activity_summary(result)}",
        ephemeral=True,
    )


@bot.tree.command(
    name="cachestats",
    description="Show cache size and optionally clear it (admin only)",
)
@app_commands.describe(clear="Set true to clear the cache")
@app_commands.default_permissions(manage_guild=True)
async def cachestats(interaction: discord.Interaction, clear: bool = False):
    import cache as cache_mod
    size_before = cache_mod.size()
    if clear:
        cache_mod.clear()
        picker.force_refresh_pool()
        await interaction.response.send_message(
            f"🗑️ Cache cleared (was {size_before} entries). Roll pool also refreshed.",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            f"📦 Cache currently holds **{size_before}** entries.",
            ephemeral=True,
        )




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


# ---------- personal watchlist commands ----------

watchlist_group = app_commands.Group(name="watchlist", description="your personal film watchlist")


@watchlist_group.command(name="show", description="browse your personal watchlist")
async def watchlist_show(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    user_id = str(interaction.user.id)
    entries = db.get_watchlist(user_id)
    total = len(entries)
    total_pages = max(1, -(-total // MY_WATCHLIST_PAGE_SIZE))
    embed = embeds.mywatchlist_embed(str(interaction.user), entries, 0, total_pages, total)
    view = views.MyWatchlistView(
        bot=bot,
        user_id=user_id,
        user_tag=str(interaction.user),
        entries=entries,
    )
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


@watchlist_group.command(name="add", description="add a film to your watchlist by title")
@app_commands.describe(
    title="the film title to add",
    year="optional: filter by year if there are multiple matches",
)
async def watchlist_add_cmd(
    interaction: discord.Interaction, title: str, year: int | None = None
):
    await interaction.response.defer(ephemeral=True)

    try:
        results = await tmdb.search_movie(title, year=year)
    except tmdb.TMDBError as e:
        await interaction.followup.send(f"⚠️ TMDB lookup failed: {e}", ephemeral=True)
        return

    if not results:
        await interaction.followup.send(
            f"no results found for **{title}**.", ephemeral=True
        )
        return

    if _needs_disambiguation(results) and year is None:
        view = views.WatchlistAddSelectView(results, str(interaction.user.id))
        await interaction.followup.send(
            f"found multiple matches for **{title}** - pick one:",
            view=view,
            ephemeral=True,
        )
        return

    top = results[0]
    release_date = top.get("release_date") or ""
    film_year = int(release_date[:4]) if release_date[:4].isdigit() else None
    poster_url = tmdb.poster_url(top.get("poster_path"))

    added = db.watchlist_add(
        user_id=str(interaction.user.id),
        title=top.get("title", title),
        year=film_year,
        tmdb_id=top["id"],
        poster_url=poster_url,
        source="manual",
    )

    year_str = f" ({film_year})" if film_year else ""
    film_name = top.get("title", title)
    if added:
        await interaction.followup.send(
            f"\U0001f4cb added **{film_name}{year_str}** to your watchlist.", ephemeral=True
        )
    else:
        await interaction.followup.send(
            f"**{film_name}{year_str}** is already on your watchlist.", ephemeral=True
        )


@watchlist_group.command(name="remove", description="remove a film from your watchlist by title")
@app_commands.describe(title="part of the film title to remove")
async def watchlist_remove_cmd(interaction: discord.Interaction, title: str):
    count = db.watchlist_remove_by_title(str(interaction.user.id), title)
    if count:
        await interaction.response.send_message(
            f"\U0001f5d1️ removed **{count}** film(s) matching \"{title}\" from your watchlist.",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            f"no films matching \"{title}\" found in your watchlist.",
            ephemeral=True,
        )


bot.tree.add_command(watchlist_group)
if __name__ == "__main__":
    logger.setup_logging()
    print(f"[startup] sucklingbot v{version.VERSION}")
    db.init_db()
    print("Database initialized")
    trivia_counts = trivia_roulette.load_assets()
    if trivia_counts:
        total = sum(trivia_counts.values())
        breakdown = ", ".join(f"{k}: {v}" for k, v in trivia_counts.items())
        print(f"[trivia] Loaded {total} entries ({breakdown})")
    else:
        print("[trivia] No trivia content loaded — /play will be unavailable")
    _start_launcher_stdin_listener()
    bot.run(config.DISCORD_TOKEN)
