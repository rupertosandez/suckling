import discord
from discord import app_commands
from discord.ext import commands

import db
import embeds
import tmdb
import views
import achievements as achievement_module


MY_WATCHLIST_PAGE_SIZE = 10


def _needs_disambiguation(results: list[dict]) -> bool:
    if len(results) < 2:
        return False
    top_popularity = results[0].get("popularity", 0)
    second_popularity = results[1].get("popularity", 0)
    if top_popularity == 0:
        return True
    return second_popularity / top_popularity >= 0.1


class WatchlistCog(commands.Cog):
    """Personal film watchlist commands."""

    watchlist_group = app_commands.Group(
        name="watchlist",
        description="your personal film watchlist",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @watchlist_group.command(name="show", description="browse your personal watchlist")
    async def watchlist_show(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = str(interaction.user.id)
        entries = db.get_watchlist(user_id)
        total = len(entries)
        total_pages = max(1, -(-total // MY_WATCHLIST_PAGE_SIZE))
        embed = embeds.mywatchlist_embed(
            str(interaction.user),
            entries,
            0,
            total_pages,
            total,
        )
        view = views.MyWatchlistView(
            bot=self.bot,
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
        self,
        interaction: discord.Interaction,
        title: str,
        year: int | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        try:
            results = await tmdb.search_movie(title, year=year)
        except tmdb.TMDBError as e:
            await interaction.followup.send(
                f"⚠️ TMDB lookup failed: {e}", ephemeral=True
            )
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
            await achievement_module.award_for_user(
                self.bot,
                interaction.user,
                source_type="watchlist_add",
                source_id=str(top["id"]),
            )
            await interaction.followup.send(
                f"\U0001f4cb added **{film_name}{year_str}** to your watchlist.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"**{film_name}{year_str}** is already on your watchlist.",
                ephemeral=True,
            )

    @watchlist_group.command(name="remove", description="remove a film from your watchlist by title")
    @app_commands.describe(title="part of the film title to remove")
    async def watchlist_remove_cmd(self, interaction: discord.Interaction, title: str):
        await interaction.response.defer(ephemeral=True)

        count = db.watchlist_remove_by_title(str(interaction.user.id), title)
        if count:
            for _ in range(count):
                achievement_module.record_event(
                    str(interaction.user.id),
                    str(interaction.user),
                    "watchlist_remove",
                    title,
                )
            await achievement_module.award_for_user(
                self.bot,
                interaction.user,
                source_type="watchlist_remove",
                source_id=title,
            )
            await interaction.followup.send(
                f"\U0001f5d1\ufe0f removed **{count}** film(s) matching \"{title}\" from your watchlist.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"no films matching \"{title}\" found in your watchlist.",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(WatchlistCog(bot))
