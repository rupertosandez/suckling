from __future__ import annotations

import asyncio
import re

import discord
from discord import app_commands
from discord.ext import commands

import achievements as achievement_module
import config
import db


def _normalize_lookup_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _achievement_choices(
    ids: list[str],
    current: str,
) -> list[app_commands.Choice[str]]:
    current = (current or "").lower()
    choices = []
    for achievement_id in ids:
        achievement = achievement_module.ACHIEVEMENT_BY_ID.get(achievement_id)
        if not achievement:
            continue
        label = achievement_module.display_name(achievement)
        if current and current not in label.lower() and current not in achievement_id:
            continue
        choices.append(app_commands.Choice(name=label[:100], value=achievement_id))
    return choices[:25]


async def _earned_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    earned = sorted(
        db.get_earned_achievement_ids(str(interaction.user.id)),
        key=lambda achievement_id: achievement_module.ACHIEVEMENT_BY_ID[achievement_id].name
        if achievement_id in achievement_module.ACHIEVEMENT_BY_ID
        else achievement_id,
    )
    return _achievement_choices(earned, current)


async def _displayed_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    displayed = [row["achievement_id"] for row in db.get_displayed_achievements(str(interaction.user.id))]
    return _achievement_choices(displayed, current)


def _achievement_line(user_id: str, achievement_id: str, *, earned: bool) -> str:
    achievement = achievement_module.ACHIEVEMENT_BY_ID.get(achievement_id)
    if not achievement:
        return ""
    name = achievement_module.display_name(achievement)
    if earned:
        return f"**{name}**\n{achievement.description}"
    progress = achievement_module.progress_for(user_id, achievement_id)
    progress_note = ""
    if progress:
        value, threshold = progress
        progress_note = f" ({min(value, threshold)}/{threshold})"
    return f"**{name}**\n{achievement.hint}{progress_note}"


def _profile_embed(member: discord.User | discord.Member, *, viewer_id: str | None = None) -> discord.Embed:
    user_id = str(member.id)
    earned_rows = db.get_earned_achievements(user_id)
    earned_ids = {row["achievement_id"] for row in earned_rows}
    displayed_ids = [row["achievement_id"] for row in db.get_displayed_achievements(user_id)]

    embed = discord.Embed(
        title="Achievement Shelf",
        color=achievement_module.ROLE_COLOR,
    )
    embed.set_author(
        name=f"{member.display_name}'s achievements",
        icon_url=member.display_avatar.url,
    )
    embed.description = (
        f"**{len(earned_ids)} / {len(achievement_module.ACHIEVEMENTS)}** unlocked\n"
        f"**{len(displayed_ids)} / {achievement_module.MAX_DISPLAYED_ACHIEVEMENTS}** visible badge roles"
    )

    if displayed_ids:
        lines = [_achievement_line(user_id, achievement_id, earned=True) for achievement_id in displayed_ids]
        embed.add_field(name="Displayed Badges", value="\n\n".join(filter(None, lines)), inline=False)
    else:
        embed.add_field(name="Displayed Badges", value="None pinned yet.", inline=False)

    recent_lines = []
    for row in earned_rows[:5]:
        achievement = achievement_module.ACHIEVEMENT_BY_ID.get(row["achievement_id"])
        if not achievement:
            continue
        recent_lines.append(
            f"**{achievement_module.display_name(achievement)}** - "
            f"<t:{int(_timestamp(row['earned_at']))}:R>"
        )
    if recent_lines:
        embed.add_field(name="Recent Unlocks", value="\n".join(recent_lines), inline=False)

    if viewer_id == user_id:
        next_lines = []
        for achievement in achievement_module.ACHIEVEMENTS:
            if achievement.id in earned_ids:
                continue
            next_lines.append(_achievement_line(user_id, achievement.id, earned=False))
            if len(next_lines) >= 5:
                break
        if next_lines:
            embed.add_field(name="Next Up", value="\n\n".join(next_lines), inline=False)
        embed.set_footer(text="Pin up to 3 badges with /achievementdisplay")

    return embed


def _timestamp(value: str) -> float:
    try:
        return datetime_from_iso(value).timestamp()
    except ValueError:
        return 0


def datetime_from_iso(value: str):
    from datetime import datetime, timezone

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _find_achievement_in_embed(embed: discord.Embed) -> achievement_module.Achievement | None:
    parts = [
        embed.title or "",
        embed.description or "",
        embed.author.name if embed.author else "",
    ]
    for field in embed.fields:
        parts.append(field.name or "")
        parts.append(field.value or "")
    haystack = _normalize_lookup_text(" ".join(parts))
    for achievement in achievement_module.ACHIEVEMENTS:
        if _normalize_lookup_text(achievement.name) in haystack:
            return achievement
        if _normalize_lookup_text(achievement_module.display_name(achievement)) in haystack:
            return achievement
    return None


def _extract_rental_title(embed: discord.Embed) -> str | None:
    text = embed.description or ""
    patterns = (
        r"\*\*rental:\*\*\s*\*([^*]+)\*",
        r"\*\*rental\*\*\s*\n\s*\*([^*]+)\*",
        r"returned\s+\*([^*]+)\*\.?",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _is_achievement_unlock_embed(embed: discord.Embed) -> bool:
    text = " ".join([
        embed.title or "",
        embed.description or "",
        " ".join(field.name or "" for field in embed.fields),
        " ".join(field.value or "" for field in embed.fields),
    ]).lower()
    return "achievement unlocked" in text or "earned a new badge" in text


class AchievementsCog(commands.Cog):
    """Achievement profile, display, feed, and admin commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="achievements", description="view achievement badges")
    @app_commands.describe(user="optional: whose achievements to view")
    async def achievements(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ):
        target = user or interaction.user
        embed = _profile_embed(target, viewer_id=str(interaction.user.id))
        await interaction.response.send_message(embed=embed, ephemeral=user is None)

    @app_commands.command(name="achievementdisplay", description="pin one earned achievement as a visible badge role")
    @app_commands.describe(
        achievement="the achievement badge to display",
        replace="optional: displayed badge to replace if your shelf is full",
    )
    @app_commands.autocomplete(achievement=_earned_autocomplete, replace=_displayed_autocomplete)
    async def achievement_display(
        self,
        interaction: discord.Interaction,
        achievement: str,
        replace: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        user_id = str(interaction.user.id)
        target = achievement_module.ACHIEVEMENT_BY_ID.get(achievement)
        if not target:
            await interaction.followup.send("i don't know that achievement.", ephemeral=True)
            return
        if achievement not in db.get_earned_achievement_ids(user_id):
            await interaction.followup.send("you haven't unlocked that badge yet.", ephemeral=True)
            return

        displayed = [row["achievement_id"] for row in db.get_displayed_achievements(user_id)]
        if achievement in displayed:
            await interaction.followup.send(
                f"**{achievement_module.display_name(target)}** is already displayed.", ephemeral=True
            )
            return
        if replace:
            if replace not in displayed:
                await interaction.followup.send("that replacement badge isn't currently displayed.", ephemeral=True)
                return
            displayed[displayed.index(replace)] = achievement
        elif len(displayed) >= achievement_module.MAX_DISPLAYED_ACHIEVEMENTS:
            names = ", ".join(
                achievement_module.display_name(achievement_module.ACHIEVEMENT_BY_ID[item])
                for item in displayed
                if item in achievement_module.ACHIEVEMENT_BY_ID
            )
            await interaction.followup.send(
                "your badge shelf is full. run this again with `replace`, or hide one first.\n"
                f"currently displayed: {names}",
                ephemeral=True,
            )
            return
        else:
            displayed.append(achievement)

        db.set_displayed_achievements(user_id, displayed)
        ok, message = await achievement_module.sync_member_roles(interaction.user)
        if ok:
            await interaction.followup.send(
                f"displaying **{achievement_module.display_name(target)}**.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(message, ephemeral=True)

    @app_commands.command(name="achievementhide", description="remove a visible achievement badge role")
    @app_commands.describe(achievement="the displayed achievement badge to hide")
    @app_commands.autocomplete(achievement=_displayed_autocomplete)
    async def achievement_hide(self, interaction: discord.Interaction, achievement: str):
        await interaction.response.defer(ephemeral=True)
        user_id = str(interaction.user.id)
        displayed = [row["achievement_id"] for row in db.get_displayed_achievements(user_id)]
        if achievement not in displayed:
            await interaction.followup.send("that badge isn't currently displayed.", ephemeral=True)
            return
        displayed.remove(achievement)
        db.set_displayed_achievements(user_id, displayed)
        ok, message = await achievement_module.sync_member_roles(interaction.user)
        name = achievement_module.ACHIEVEMENT_BY_ID.get(achievement)
        if ok:
            await interaction.followup.send(
                f"hid **{achievement_module.display_name(name) if name else achievement}**.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(message, ephemeral=True)

    @app_commands.command(name="achievementclear", description="remove all visible achievement badge roles")
    async def achievement_clear(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        db.set_displayed_achievements(str(interaction.user.id), [])
        ok, message = await achievement_module.sync_member_roles(interaction.user)
        await interaction.followup.send("cleared your displayed badges." if ok else message, ephemeral=True)

    @app_commands.command(name="achievementboard", description="see community achievement activity")
    async def achievement_board(self, interaction: discord.Interaction):
        recent = db.get_recent_achievement_unlocks(limit=5)
        leaders = db.get_achievement_counts_by_user(limit=5)
        rarity = db.get_achievement_rarity_counts()

        embed = discord.Embed(title="Achievement Board", color=achievement_module.ROLE_COLOR)
        if recent:
            lines = []
            for row in recent:
                achievement = achievement_module.ACHIEVEMENT_BY_ID.get(row["achievement_id"])
                if achievement:
                    lines.append(
                        f"**{row['user_tag']}** unlocked "
                        f"**{achievement_module.display_name(achievement)}**"
                    )
            embed.add_field(name="Newest Unlocks", value="\n".join(lines), inline=False)
        if leaders:
            lines = [
                f"#{index + 1} **{row['user_tag']}** - {row['total']}"
                for index, row in enumerate(leaders)
            ]
            embed.add_field(name="Top Collectors", value="\n".join(lines), inline=False)
        rare = sorted(
            (
                (achievement_id, count)
                for achievement_id, count in rarity.items()
                if achievement_id in achievement_module.ACHIEVEMENT_BY_ID
            ),
            key=lambda item: (item[1], achievement_module.ACHIEVEMENT_BY_ID[item[0]].name),
        )[:5]
        if rare:
            lines = [
                f"**{achievement_module.display_name(achievement_module.ACHIEVEMENT_BY_ID[achievement_id])}** - {count}"
                for achievement_id, count in rare
            ]
            embed.add_field(name="Rarest Badges", value="\n".join(lines), inline=False)
        if not embed.fields:
            embed.description = "No achievement unlocks yet."
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="setfeed", description="set the channel for Suckling feed posts (admin only)")
    @app_commands.describe(channel="the channel where achievements should post")
    @app_commands.default_permissions(manage_guild=True)
    async def set_feed(self, interaction: discord.Interaction, channel: discord.TextChannel):
        perms = channel.permissions_for(interaction.guild.me)
        if not perms.send_messages or not perms.embed_links:
            await interaction.response.send_message(
                f"i need send messages and embed links in {channel.mention}.",
                ephemeral=True,
            )
            return
        db.set_feed_channel_id(channel.id)
        await interaction.response.send_message(
            f"suckling feed will post in {channel.mention}.",
            ephemeral=True,
        )

    @app_commands.command(
        name="achievementcatalog",
        description="post the achievement catalog link in a channel (admin only)",
    )
    @app_commands.describe(channel="the channel where the achievement catalog link should post")
    @app_commands.default_permissions(manage_guild=True)
    async def achievement_catalog(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        perms = channel.permissions_for(interaction.guild.me)
        if not perms.send_messages or not perms.embed_links:
            await interaction.response.send_message(
                f"i need send messages and embed links in {channel.mention}.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        embed = discord.Embed(
            title="Achievement Catalog",
            description=(
                f"Browse all **{len(achievement_module.catalog_entries())}** earnable badges, "
                "grouped by category.\n\n"
                "You can pin up to **3** unlocked achievements as visible Discord badge roles."
            ),
            color=achievement_module.ROLE_COLOR,
            url=config.ACHIEVEMENT_CATALOG_URL,
        )
        embed.set_footer(text="Use /achievements to see your own shelf")
        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(
                label="View Full Catalog",
                url=config.ACHIEVEMENT_CATALOG_URL,
            )
        )
        try:
            await channel.send(
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"failed to post achievement catalog in {channel.mention}: {e}",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"posted the achievement catalog link in {channel.mention}.",
            ephemeral=True,
        )

    @app_commands.command(name="achievementrescan", description="backfill achievements from bot history (admin only)")
    @app_commands.describe(user="optional: only rescan one member")
    @app_commands.default_permissions(manage_guild=True)
    async def achievement_rescan(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ):
        await interaction.response.defer(ephemeral=True)
        if user:
            users = [(str(user.id), str(user), user)]
        else:
            users = []
            candidate_user_ids = await asyncio.to_thread(
                db.get_all_achievement_candidate_user_ids
            )
            for user_id in candidate_user_ids:
                member = interaction.guild.get_member(int(user_id))
                if member is None:
                    try:
                        member = await interaction.guild.fetch_member(int(user_id))
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        member = None
                users.append((user_id, str(member) if member else user_id, member))

        awarded = 0
        announced = 0
        for user_id, user_tag, member in users:
            unlocked = await asyncio.to_thread(
                achievement_module.evaluate_user,
                user_id,
                user_tag,
                source_type="rescan",
            )
            awarded += len(unlocked)
            if unlocked and member:
                await achievement_module.post_unlocks(self.bot, member, unlocked)
                announced += len(unlocked)
        await interaction.followup.send(
            f"rescan complete. awarded **{awarded}** new achievement(s); "
            f"posted **{announced}** to the feed.",
            ephemeral=True,
        )

    @app_commands.command(name="achievementsyncroles", description="sync displayed achievement roles (admin only)")
    @app_commands.describe(user="the member whose visible badge roles should be synced")
    @app_commands.default_permissions(manage_guild=True)
    async def achievement_sync_roles(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ):
        await interaction.response.defer(ephemeral=True)
        ok, message = await achievement_module.sync_member_roles(user)
        await interaction.followup.send(message, ephemeral=True)

    @app_commands.command(
        name="achievementrefreshfeed",
        description="refresh recent achievement feed embeds (admin only)",
    )
    @app_commands.describe(limit="how many recent feed messages to scan, max 500")
    @app_commands.default_permissions(manage_guild=True)
    async def achievement_refresh_feed(
        self,
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, 500] = 100,
    ):
        await interaction.response.defer(ephemeral=True)

        channel_id = db.get_feed_channel_id()
        if not channel_id:
            await interaction.followup.send(
                "the feed channel is not configured yet. use `/setfeed` first.",
                ephemeral=True,
            )
            return

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None
        if not channel or not hasattr(channel, "history"):
            await interaction.followup.send(
                "i couldn't read the configured feed channel.",
                ephemeral=True,
            )
            return

        scanned = 0
        updated = 0
        skipped = 0
        async for message in channel.history(limit=limit):
            scanned += 1
            if message.author.id != self.bot.user.id or not message.embeds:
                continue

            embed = message.embeds[0]
            if not _is_achievement_unlock_embed(embed):
                continue

            achievement = _find_achievement_in_embed(embed)
            if not achievement:
                skipped += 1
                continue

            user = message.mentions[0] if message.mentions else None
            user_label = (
                str(user)
                if user
                else (embed.author.name if embed.author else "someone")
            )
            user_mention = user.mention if user else None
            icon_url = None
            if user:
                icon_url = user.display_avatar.url
            elif embed.author and embed.author.icon_url:
                icon_url = embed.author.icon_url

            refreshed = achievement_module.unlock_embed(
                achievement,
                user_label=user_label,
                user_mention=user_mention,
                icon_url=icon_url,
                rental_title=_extract_rental_title(embed),
            )
            try:
                await message.edit(embed=refreshed)
                updated += 1
            except (discord.Forbidden, discord.HTTPException):
                skipped += 1

        await interaction.followup.send(
            f"feed refresh complete. scanned **{scanned}**, updated **{updated}**, "
            f"skipped **{skipped}**.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AchievementsCog(bot))
