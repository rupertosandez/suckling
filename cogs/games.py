import asyncio
import io
import random
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import db
import embeds
import game
import imageops
import logger
import picker
import sixdegrees
import tmdb
import trivia_roulette
import achievements as achievement_module


ROUND_DURATION_SECONDS = 60


async def _fetch_award_user(bot: commands.Bot, guild: discord.Guild | None, user_id: str):
    user = guild.get_member(int(user_id)) if guild else None
    if user:
        return user
    try:
        return await bot.fetch_user(int(user_id))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
        return None


class GamesCog(commands.Cog):
    """Guessing, trivia, and Six Degrees game commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="guess", description="Start a horror movie guessing round")
    @app_commands.describe(
        difficulty="Easy = full still (1 pt). Hard = cropped poster (2 pts). Default: random.",
    )
    @app_commands.choices(difficulty=[
        app_commands.Choice(name="easy (full still, 1 point)", value="easy"),
        app_commands.Choice(name="hard (cropped poster, 2 points)", value="hard"),
    ])
    async def guess(
        self,
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
            else:
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
                puzzle_bytes = image_bytes
            else:
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
        type_label = "Movie Still" if diff_val == "easy" else "Cropped Poster"
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
            new_total = None
            try:
                new_total = await asyncio.to_thread(
                    db.increment_guess_score,
                    round_obj.winner_id,
                    round_obj.winner_tag,
                    points=points,
                )
            except Exception as e:
                logger.log_exception("guess_score_save", e)
            member = await _fetch_award_user(self.bot, interaction.guild, round_obj.winner_id)
            if member:
                try:
                    await achievement_module.award_for_user(
                        self.bot,
                        member,
                        source_type="guess_win",
                        source_id=str(movie["id"]),
                    )
                except Exception as e:
                    logger.log_exception("guess_achievement_award", e)
            total_text = f" — total: **{new_total}**" if new_total is not None else ""
            reveal_embed.description = (
                f"🏆 <@{round_obj.winner_id}> got it! "
                f"(+{points} point{'s' if points > 1 else ''}{total_text})"
            )
        else:
            reveal_embed.description = "⏰ Time's up — nobody guessed it."

        await interaction.channel.send(embed=reveal_embed)

    @app_commands.command(name="play", description="Start a trivia roulette round")
    async def play(self, interaction: discord.Interaction):
        channel_id = interaction.channel.id

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
            new_total = None
            try:
                new_total = await asyncio.to_thread(
                    db.increment_guess_score,
                    round_obj.winner_id,
                    round_obj.winner_tag,
                    points=1,
                )
            except Exception as e:
                logger.log_exception("trivia_score_save", e)
            try:
                await asyncio.to_thread(
                    achievement_module.record_event,
                    round_obj.winner_id,
                    round_obj.winner_tag,
                    "trivia_win",
                    category,
                )
            except Exception as e:
                logger.log_exception("trivia_win_event", e)
            member = await _fetch_award_user(self.bot, interaction.guild, round_obj.winner_id)
            if member:
                try:
                    await achievement_module.award_for_user(
                        self.bot,
                        member,
                        source_type="trivia_win",
                        source_id=category,
                    )
                except Exception as e:
                    logger.log_exception("trivia_achievement_award", e)
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

    @app_commands.command(name="giveup", description="End the current guessing round in this channel")
    async def giveup(self, interaction: discord.Interaction):
        channel_id = interaction.channel.id

        guess_round = game.get_round(channel_id)
        if guess_round and not guess_round.revealed:
            guess_round.end_event.set()
            await interaction.response.send_message("🏳️ Revealing answer...", ephemeral=True)
            return

        six_round = sixdegrees.get_round(channel_id)
        if six_round and not six_round.revealed:
            six_round.end_event.set()
            await interaction.response.send_message("🏳️ Ending the round...", ephemeral=True)
            return

        trivia_round = trivia_roulette.get_round(channel_id)
        if trivia_round and not trivia_round.revealed:
            trivia_round.end_event.set()
            await interaction.response.send_message("🏳️ Revealing answer...", ephemeral=True)
            return

        await interaction.response.send_message(
            "No active round in this channel.", ephemeral=True
        )

    @app_commands.command(name="leaderboard", description="Show the top horror-guessing scorers")
    async def leaderboard(self, interaction: discord.Interaction):
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

    @app_commands.command(name="six", description="Start a Six Degrees of Separation round")
    async def six_command(self, interaction: discord.Interaction):
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
            new_total = None
            try:
                new_total = await asyncio.to_thread(
                    db.increment_six_score,
                    round_obj.winner_id,
                    round_obj.winner_tag,
                    points=points,
                )
            except Exception as e:
                logger.log_exception("six_score_save", e)
                new_total = "not saved"
            chain_str = " → ".join(round_obj.winning_chain)
            member = await _fetch_award_user(self.bot, interaction.guild, round_obj.winner_id)
            if member:
                try:
                    await achievement_module.award_for_user(
                        self.bot,
                        member,
                        source_type="six_win",
                        source_id=str(round_obj.actor_a_id),
                    )
                except Exception as e:
                    logger.log_exception("six_achievement_award", e)
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

    @app_commands.command(name="sixleaderboard", description="Show the top Six Degrees scorers")
    async def sixleaderboard(self, interaction: discord.Interaction):
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


async def setup(bot: commands.Bot):
    await bot.add_cog(GamesCog(bot))
