import discord
from discord import app_commands
from discord.ext import commands

import embeds
import plex
import views


class RB9Cog(commands.Cog):
    """Plex-backed Return by 9 library commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="rb9", description="Pick a random movie from the RB9 library")
    async def rb9_pick(self, interaction: discord.Interaction):
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
            bot=self.bot,
            title=movie.get("title", ""),
            year=movie.get("year"),
            poster_url=movie.get("thumb_url"),
            plex_available=True,
            plex_movie=movie,
        )
        await interaction.followup.send(embed=embed, view=film_view)

    @app_commands.command(name="rb9stats", description="Overall stats for the rb9 library")
    async def rb9stats(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            stats = await plex.get_library_summary()
        except plex.PlexError as e:
            await interaction.followup.send(f"⚠️ rb9 error: {e}", ephemeral=True)
            return
        embed = embeds.rb9_stats_embed(stats)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="rb9biggest", description="The longest film in the rb9 library")
    async def rb9biggest(self, interaction: discord.Interaction):
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

    @app_commands.command(name="rb9shortest", description="The shortest film in the rb9 library")
    async def rb9shortest(self, interaction: discord.Interaction):
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

    @app_commands.command(name="rb9oldest", description="The oldest film in the rb9 library")
    async def rb9oldest(self, interaction: discord.Interaction):
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

    @app_commands.command(name="rb9newest", description="The most recently added film in the rb9 library")
    async def rb9newest(self, interaction: discord.Interaction):
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

    @app_commands.command(name="rb9totalruntime", description="How long it'd take to watch the entire rb9 library")
    async def rb9totalruntime(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            stats = await plex.get_total_runtime()
        except plex.PlexError as e:
            await interaction.followup.send(f"⚠️ rb9 error: {e}", ephemeral=True)
            return
        embed = embeds.rb9_total_runtime_embed(stats)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="rb9decade", description="Films per decade in the rb9 library")
    async def rb9decade(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            decades = await plex.get_decade_breakdown()
        except plex.PlexError as e:
            await interaction.followup.send(f"⚠️ rb9 error: {e}", ephemeral=True)
            return
        embed = embeds.rb9_decade_embed(decades)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="rb9genre", description="Top genres in the rb9 library")
    async def rb9genre(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            genres = await plex.get_genre_breakdown(top_n=10)
        except plex.PlexError as e:
            await interaction.followup.send(f"⚠️ rb9 error: {e}", ephemeral=True)
            return
        embed = embeds.rb9_genre_embed(genres)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="rb9randomscene", description="A random film + backdrop from the rb9 library")
    async def rb9randomscene(self, interaction: discord.Interaction):
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
            bot=self.bot,
            title=scene.get("title", ""),
            year=scene.get("year"),
            poster_url=scene.get("thumb_url"),
            plex_available=True,
            plex_movie=scene,
        )
        await interaction.followup.send(embed=embed, view=scene_view)


async def setup(bot: commands.Bot):
    await bot.add_cog(RB9Cog(bot))
