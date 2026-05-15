import asyncio
import io
import random
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
        await super().close()


bot = SucklingBot(command_prefix="!", intents=intents)
scheduler = AsyncIOScheduler()


ROUND_DURATION_SECONDS = 60


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


async def _scheduled_rental_check():
    """Hourly job: send overdue DMs and 12-hour reminder DMs."""
    try:
        await rental_module.check_overdue(bot)
        await rental_module.check_reminders(bot)
    except Exception as e:
        logger.log_exception("scheduled_rental_check", e)
        print(f"[scheduler] Rental check failed: {e}")


@bot.event
async def on_ready():
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
        scheduler.start()
        print("[scheduler] Daily tracker check scheduled for 9:00 local time")
        print("[scheduler] Daily horror recommendation scheduled for 12:00 local time")
        print("[scheduler] Rental overdue/reminder check scheduled hourly")


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

    embed = embeds.daily_rec_embed(details, providers)

    try:
        await channel.send(embed=embed)
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

        await interaction.followup.send(embed=embed)
    else:
        view = views.MovieSelectView(results)
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

    embed = embeds.roll_embed(details, providers)
    await interaction.followup.send(embed=embed)


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
    await interaction.followup.send(embed=embed)


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
    await interaction.followup.send(embed=embed)


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
])
@app_commands.default_permissions(manage_guild=True)
async def toggle(
    interaction: discord.Interaction,
    feature: app_commands.Choice[str],
    enabled: bool,
):
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
    bot.run(config.DISCORD_TOKEN)