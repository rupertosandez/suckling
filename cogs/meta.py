from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import config
import embeds
import version


class MetaCog(commands.Cog):
    """Bot metadata and general utility commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="info", description="show info about the bot")
    async def info(self, interaction: discord.Interaction):
        await interaction.response.defer()

        uptime_seconds = (datetime.now(timezone.utc) - self.bot.started_at).total_seconds()
        guild_count = len(self.bot.guilds)

        logo = discord.File(config.LOGO_PATH, filename="logo.png")
        embed = embeds.info_embed(version.VERSION, uptime_seconds, guild_count)

        await interaction.followup.send(embed=embed, file=logo)


async def setup(bot: commands.Bot):
    await bot.add_cog(MetaCog(bot))
