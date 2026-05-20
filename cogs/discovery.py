import discord
from discord import app_commands
from discord.ext import commands

import db
import embeds
import picker
import plex
import tmdb
import views


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


class DiscoveryCog(commands.Cog):
    """Movie lookup and random recommendation commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="suck", description="suck up a movie and see where to watch it")
    @app_commands.describe(
        title="The movie title to search for",
        year="Optional: filter by release year if there are multiple matches",
    )
    async def suck(self, interaction: discord.Interaction, title: str, year: int | None = None):
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
                bot=self.bot,
                title=details.get("title", ""),
                year=plex_year,
                tmdb_id=details.get("id"),
                poster_url=poster_url,
                plex_available=bool(plex_available),
            )
            await interaction.followup.send(embed=embed, view=film_view)
        else:
            view = views.MovieSelectView(results, bot=self.bot)
            await interaction.followup.send(
                f"Found multiple matches for **{title}**. Pick one:",
                view=view,
            )

    @app_commands.command(name="roll", description="Get a random horror movie recommendation")
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
        self,
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
            bot=self.bot,
            title=details.get("title", ""),
            year=plex_year,
            tmdb_id=details.get("id"),
            poster_url=poster_url,
            plex_available=bool(plex_available),
        )
        await interaction.followup.send(embed=embed, view=film_view)


async def setup(bot: commands.Bot):
    bot.suckling_post_daily_recommendation = post_daily_recommendation
    await bot.add_cog(DiscoveryCog(bot))
