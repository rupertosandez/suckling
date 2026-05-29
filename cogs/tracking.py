from typing import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands

import db
import tmdb
import views
import achievements as achievement_module


def _needs_disambiguation(results: list[dict]) -> bool:
    if len(results) < 2:
        return False
    top_popularity = results[0].get("popularity", 0)
    second_popularity = results[1].get("popularity", 0)
    if top_popularity == 0:
        return True
    return second_popularity / top_popularity >= 0.1


class TrackingCog(commands.Cog):
    """Streaming watchlist and auto-posting setup commands."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        run_lb_activity_check: Callable[..., Awaitable[dict]],
        lb_activity_summary: Callable[[dict], str],
    ):
        self.bot = bot
        self.run_lb_activity_check = run_lb_activity_check
        self.lb_activity_summary = lb_activity_summary

    @app_commands.command(
        name="setannouncements",
        description="Set the channel where horror release alerts will post (admin only)",
    )
    @app_commands.describe(channel="The channel to post announcements in")
    @app_commands.default_permissions(manage_guild=True)
    async def set_announcements(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
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

    @app_commands.command(
        name="setdaily",
        description="Set the channel where daily horror recommendations post (admin only)",
    )
    @app_commands.describe(channel="The channel to post daily recommendations in")
    @app_commands.default_permissions(manage_guild=True)
    async def set_daily(self, interaction: discord.Interaction, channel: discord.TextChannel):
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

    @app_commands.command(
        name="setlbactivity",
        description="Set the channel where linked Letterboxd activity posts (admin only)",
    )
    @app_commands.describe(channel="The channel to post Letterboxd activity in")
    @app_commands.default_permissions(manage_guild=True)
    async def set_lb_activity(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
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
        seed_result = await self.run_lb_activity_check(post=False, seed_only=True)
        db.set_lb_activity_enabled(True)
        await interaction.followup.send(
            f"Letterboxd activity will now post in {channel.mention}.\n"
            "I seeded the current feeds first so old watches won't spam the channel: "
            f"{self.lb_activity_summary(seed_result)}",
            ephemeral=True,
        )

    @app_commands.command(
        name="track",
        description="Add a movie to the watchlist — get notified when it streams",
    )
    @app_commands.describe(
        title="The movie title to track",
        year="Optional: filter by release year if there are multiple matches",
    )
    async def track(
        self,
        interaction: discord.Interaction,
        title: str,
        year: int | None = None,
    ):
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
        user_id = str(interaction.user.id)

        if year is not None or not _needs_disambiguation(results):
            top = results[0]
            movie_title = top.get("title", "Unknown")
            release_date = top.get("release_date", "")
            movie_year = release_date[:4] if release_date else "—"

            added = db.add_tracked_movie(top["id"], movie_title, user_tag, user_id)
            if not added:
                await interaction.followup.send(
                    f"**{movie_title} ({movie_year})** is already on the tracked list."
                )
                return

            msg = await views._build_track_response(top["id"], movie_title, movie_year)
            await achievement_module.award_for_user(
                self.bot,
                interaction.user,
                source_type="track",
                source_id=str(top["id"]),
            )
            await interaction.followup.send(msg)
        else:
            view = views.TrackSelectView(results, added_by=user_tag, added_by_id=user_id)
            await interaction.followup.send(
                f"Found multiple matches for **{title}**. Pick which one to track:",
                view=view,
            )

    @app_commands.command(name="untrack", description="Remove a movie from the watchlist")
    @app_commands.describe(title="The movie title to untrack")
    async def untrack(self, interaction: discord.Interaction, title: str):
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

    @app_commands.command(name="tracked", description="Show all movies on the watchlist")
    async def tracked(self, interaction: discord.Interaction):
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


async def setup(bot: commands.Bot):
    await bot.add_cog(
        TrackingCog(
            bot,
            run_lb_activity_check=bot.suckling_run_lb_activity_check,
            lb_activity_summary=bot.suckling_lb_activity_summary,
        )
    )
