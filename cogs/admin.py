import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands

import db
import embeds
import picker
import tracker
import version


LB_LINKED_PAGE_SIZE = 10

FAQ_SECTIONS = [
    (
        "Movie Lookup & Recommendations",
        "Find a movie, check availability, or let Suckling pick something.",
        [
            (
                "Core Commands",
                "`/suck <title>` - movie lookup\n"
                "`/roll` - random recommendation",
                True,
            ),
            (
                "Filters",
                "`year` narrows title matches\n"
                "`decade` and `runtime` shape `/roll`",
                True,
            ),
            (
                "Movie Cards",
                "**+ watchlist** saves privately\n"
                "**rent this** appears for RB9 movies",
                False,
            ),
            (
                "Examples",
                "`/suck The Substance`\n"
                "`/suck Halloween year:1978`\n"
                "`/roll decade:1980s runtime:short`",
                False,
            ),
        ],
    ),
    (
        "RB9 Rentals",
        "Check out RB9 movies, post reviews, and build rental history.",
        [
            (
                "Start",
                "`/rent` - roll random, pick a title, or ask an admin\n"
                "Up to **3 active rentals** at once",
                False,
            ),
            (
                "Due Date",
                "Due at **9:00 PM on the fifth day**\n"
                "`/timezone` sets your local due time",
                True,
            ),
            (
                "Return",
                "`/return` posts your review and records the watch\n"
                "Returns can unlock achievements or MacGuffins",
                True,
            ),
            (
                "Useful Commands",
                "`/myrental` - active rentals\n"
                "`/extend` - one 24-hour extension\n"
                "`/rentalstats` - history\n"
                "`/latefees` - cosmetic leaderboard",
                False,
            ),
        ],
    ),
    (
        "Watchlists & Streaming Tracking",
        "Save movies for yourself or track releases for the whole server.",
        [
            (
                "Private Watchlist",
                "`/watchlist add <title>`\n"
                "`/watchlist show`\n"
                "`/watchlist remove <title>`",
                True,
            ),
            (
                "Server Tracking",
                "`/track <title>`\n"
                "`/untrack <title>`\n"
                "`/tracked`",
                True,
            ),
            (
                "When To Use Each",
                "**Watchlist:** personal queue\n"
                "**Tracking:** alert the server when something first hits digital",
                False,
            ),
            (
                "Shortcut",
                "Use **+ watchlist** on movie cards to save a film without typing.",
                False,
            ),
        ],
    ),
    (
        "Letterboxd",
        "Connect Letterboxd for profiles, watchlists, group activity, and taste checks.",
        [
            (
                "Connect",
                "`/lb link <username>`\n"
                "`/lb unlink`",
                True,
            ),
            (
                "Browse",
                "`/lb profile` - recent diary\n"
                "`/lb watchlist` - watchlist browser",
                True,
            ),
            (
                "Community",
                "`/lb group` - linked member activity\n"
                "`/lb tastecheck` - compare two accounts",
                False,
            ),
            (
                "Note",
                "Accounts must be public. Linked activity may post to the server activity channel if enabled.",
                False,
            ),
        ],
    ),
    (
        "Games, Achievements & MacGuffins",
        "Play movie games, show off badges, and collect unique movie objects.",
        [
            (
                "Games",
                "`/guess` - image guessing\n"
                "`/play` - trivia roulette\n"
                "`/six` - six degrees challenge\n"
                "`/giveup` - end a round",
                False,
            ),
            (
                "Scores",
                "`/leaderboard`\n"
                "`/sixleaderboard`",
                True,
            ),
            (
                "Achievements",
                "`/achievements` shows your shelf\n"
                "`/achievementdisplay` pins up to **3** visible badges",
                True,
            ),
            (
                "MacGuffins",
                "`/claimguffin` - starter card\n"
                "`/myguffins` - collection\n"
                "`/giftguffin` - give one away\n"
                "Returns can drop more.",
                False,
            ),
        ],
    ),
]


def _admin_channel_label(channel_id: int | None) -> str:
    return f"<#{channel_id}>" if channel_id else "not set"


def _enabled_label(enabled: bool) -> str:
    return "on" if enabled else "off"


def _format_linked_at(value: str | None) -> str:
    if not value:
        return "unknown date"
    try:
        linked_at = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return value
    return f"<t:{int(linked_at.timestamp())}:d>"


def _faq_index_embed(section_links: dict[str, str] | None = None) -> discord.Embed:
    section_links = section_links or {}
    embed = discord.Embed(
        title="Suckling FAQ",
        description=(
            "Suckling is the Return by 9 movie bot. It can look up films, "
            "recommend movies, manage RB9 rentals, track streaming releases, "
            "connect Letterboxd accounts, run games, and hand out achievements.\n\n"
            "Use the links below to jump to each section."
        ),
        color=0x8B0000,
    )

    section_lines = []
    for index, (title, _, _) in enumerate(FAQ_SECTIONS, start=1):
        url = section_links.get(title)
        label = f"{index}. {title}"
        section_lines.append(f"[{label}]({url})" if url else label)

    embed.add_field(
        name="Sections",
        value="\n".join(section_lines),
        inline=False,
    )
    embed.set_footer(text="All commands are slash commands. Type / and look for Suckling.")
    return embed


def _faq_section_embed(title: str, description: str, fields: list[tuple[str, str, bool]]) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=0x8B0000,
    )
    for name, value, inline in fields:
        embed.add_field(name=name, value=value, inline=inline)
    return embed


def _faq_thread_starter_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Suckling FAQ",
        description="Open the thread below for a quick guide to Suckling's main features.",
        color=0x8B0000,
    )
    return embed


class AdminCog(commands.Cog):
    """Admin dashboard, toggles, and manual maintenance commands."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        restart_process: Callable[[], Awaitable[None]],
        post_daily_recommendation: Callable[[discord.Client], Awaitable[bool]],
        run_lb_activity_check: Callable[..., Awaitable[dict]],
        lb_activity_summary: Callable[[dict], str],
        run_plex_cleanup: Callable[..., Awaitable[object]],
        run_unpopularity_audit: Callable[..., Awaitable[object]],
    ):
        self.bot = bot
        self.restart_process = restart_process
        self.post_daily_recommendation = post_daily_recommendation
        self.run_lb_activity_check = run_lb_activity_check
        self.lb_activity_summary = lb_activity_summary
        self.run_plex_cleanup = run_plex_cleanup
        self.run_unpopularity_audit = run_unpopularity_audit

    @app_commands.command(
        name="botstatus",
        description="Show the admin dashboard for bot settings and health",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def botstatus(self, interaction: discord.Interaction):
        import cache as cache_mod

        await interaction.response.defer(ephemeral=True)

        announcement_channel_id = db.get_announcement_channel_id()
        daily_channel_id = db.get_daily_rec_channel_id()
        lb_activity_channel_id = db.get_lb_activity_channel_id()
        reviews_channel_id = db.get_reviews_channel_id()
        rental_request_channel_id = db.get_rental_request_channel_id()

        announcements_enabled = db.is_announcements_enabled()
        daily_rec_enabled = db.is_daily_rec_enabled()
        lb_activity_enabled = db.is_lb_activity_enabled()

        warnings = []
        if announcements_enabled and not announcement_channel_id:
            warnings.append("streaming announcements are on, but no channel is set")
        if daily_rec_enabled and not daily_channel_id:
            warnings.append("daily recommendation is on, but no channel is set")
        if lb_activity_enabled and not lb_activity_channel_id:
            warnings.append("letterboxd activity is on, but no channel is set")
        if not reviews_channel_id:
            warnings.append("rental reviews forum is not set")
        if not rental_request_channel_id:
            warnings.append("rental recommendation request channel is not set")

        status = {
            "version": version.VERSION,
            "uptime_seconds": (datetime.now(timezone.utc) - self.bot.started_at).total_seconds(),
            "latency_ms": self.bot.latency * 1000,
            "cache_size": cache_mod.size(),
            "tracked_count": db.tracked_movie_count(),
            "lb_account_count": db.lb_account_count(),
            "active_rental_count": db.active_rental_count(),
            "overdue_rental_count": db.overdue_active_rental_count(),
            "reviews_channel": _admin_channel_label(reviews_channel_id),
            "rental_request_channel": _admin_channel_label(rental_request_channel_id),
            "announcement_channel": _admin_channel_label(announcement_channel_id),
            "daily_channel": _admin_channel_label(daily_channel_id),
            "lb_activity_channel": _admin_channel_label(lb_activity_channel_id),
            "announcements_enabled": _enabled_label(announcements_enabled),
            "daily_rec_enabled": _enabled_label(daily_rec_enabled),
            "lb_activity_enabled": _enabled_label(lb_activity_enabled),
            "warnings": warnings,
        }
        await interaction.followup.send(
            embed=embeds.bot_status_embed(status),
            ephemeral=True,
        )

    @app_commands.command(
        name="lblinked",
        description="List linked Letterboxd accounts (admin only)",
    )
    @app_commands.describe(page="page number, if there are many linked accounts")
    @app_commands.default_permissions(manage_guild=True)
    async def lblinked(self, interaction: discord.Interaction, page: int = 1):
        await interaction.response.defer(ephemeral=True)

        rows = sorted(
            db.get_all_lb_accounts(),
            key=lambda row: row.get("lb_username", "").lower(),
        )
        total = len(rows)
        total_pages = max(1, -(-total // LB_LINKED_PAGE_SIZE))
        page_index = min(max(page, 1), total_pages) - 1
        page_rows = rows[
            page_index * LB_LINKED_PAGE_SIZE:
            (page_index + 1) * LB_LINKED_PAGE_SIZE
        ]

        accounts = []
        guild = interaction.guild
        for row in page_rows:
            user_id = row["user_id"]
            member = None
            try:
                member = guild.get_member(int(user_id)) if guild else None
            except (TypeError, ValueError):
                member = None

            accounts.append({
                "discord_label": member.display_name if member else f"user id {user_id}",
                "lb_username": row["lb_username"],
                "linked_at_display": _format_linked_at(row.get("linked_at")),
                "in_server": member is not None,
            })

        await interaction.followup.send(
            embed=embeds.lb_linked_embed(
                accounts,
                page=page_index,
                total_pages=total_pages,
                total=total,
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="version",
        description="Show the bot's current version (admin only)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def version_command(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"🎬 **sucklingbot** v{version.VERSION}",
            ephemeral=True,
        )

    @app_commands.command(
        name="restart",
        description="Restart the bot process (admin only)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def restart_command(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "restart initiated.",
            ephemeral=True,
        )
        asyncio.create_task(self.restart_process())

    @app_commands.command(
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
        self,
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
                seed_result = await self.run_lb_activity_check(post=False, seed_only=True)
                seed_note = f" Seeded current feeds first: {self.lb_activity_summary(seed_result)}"

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

    @app_commands.command(
        name="checknow",
        description="Manually trigger the streaming check, dry-run (admin only)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def checknow(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        result = await tracker.run_check(bot=self.bot, dry_run=True)
        await interaction.followup.send(result.to_discord_summary(), ephemeral=True)

    @app_commands.command(
        name="checknowlive",
        description="Manually trigger the streaming check and POST announcements (admin only)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def checknowlive(self, interaction: discord.Interaction):
        channel_id = db.get_announcement_channel_id()
        if not channel_id:
            await interaction.response.send_message(
                "⚠️ No announcement channel set. Use `/setannouncements` first.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        result = await tracker.run_check(bot=self.bot, dry_run=False)
        await interaction.followup.send(result.to_discord_summary(), ephemeral=True)

    @app_commands.command(
        name="dailynow",
        description="Manually trigger today's horror recommendation post (admin only)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def dailynow(self, interaction: discord.Interaction):
        channel_id = db.get_daily_rec_channel_id()
        if not channel_id:
            await interaction.response.send_message(
                "⚠️ No daily-rec channel set. Use `/setdaily` first.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        ok = await self.post_daily_recommendation(self.bot)
        if ok:
            await interaction.followup.send("✅ Daily recommendation posted.", ephemeral=True)
        else:
            await interaction.followup.send(
                "⚠️ Failed to post — see PowerShell for details.", ephemeral=True
            )

    @app_commands.command(
        name="postfaq",
        description="Post the Suckling FAQ as a thread in a channel (admin only)",
    )
    @app_commands.describe(
        channel="The channel where the FAQ thread should be created",
        thread_name="Optional name for the FAQ thread",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def postfaq(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        thread_name: str = "Suckling FAQ",
    ):
        perms = channel.permissions_for(interaction.guild.me)
        if (
            not perms.send_messages
            or not perms.embed_links
            or not perms.create_public_threads
            or not perms.send_messages_in_threads
        ):
            await interaction.response.send_message(
                f"I need permission to send messages, embed links, create public threads, "
                f"and send in threads in {channel.mention}.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            starter = await channel.send(
                embed=_faq_thread_starter_embed(),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            thread = await starter.create_thread(
                name=thread_name[:100],
                auto_archive_duration=10080,
            )
            index_message = await thread.send(embed=_faq_index_embed())
            section_links = {}
            for title, description, fields in FAQ_SECTIONS:
                message = await thread.send(
                    embed=_faq_section_embed(title, description, fields),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                section_links[title] = message.jump_url

            await index_message.edit(embed=_faq_index_embed(section_links))
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"Failed to post the FAQ thread in {channel.mention}: {e}",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"Posted the Suckling FAQ thread in {channel.mention}: {thread.mention}",
            ephemeral=True,
        )

    @app_commands.command(
        name="lbactivitynow",
        description="Manually check linked Letterboxd activity (admin only)",
    )
    @app_commands.describe(post="True to post new activity; false only reports the count")
    @app_commands.default_permissions(manage_guild=True)
    async def lbactivitynow(self, interaction: discord.Interaction, post: bool = False):
        if post and not db.get_lb_activity_channel_id():
            await interaction.response.send_message(
                "No Letterboxd activity channel is set. Use `/setlbactivity` first.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        result = await self.run_lb_activity_check(post=post)
        verb = "Posted" if post else "Dry run complete"
        await interaction.followup.send(
            f"{verb}. {self.lb_activity_summary(result)}",
            ephemeral=True,
        )

    @app_commands.command(
        name="plexcleanupnow",
        description="Run the Plex cleanup check for debugging (admin only)",
    )
    @app_commands.describe(post="True to post candidates; false only shows a private summary")
    @app_commands.default_permissions(manage_guild=True)
    async def plexcleanupnow(self, interaction: discord.Interaction, post: bool = False):
        if post and not db.get_announcement_channel_id():
            await interaction.response.send_message(
                "No announcement channel is set. Use `/setannouncements` first.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            result = await self.run_plex_cleanup(bot=self.bot, dry_run=not post)
        except Exception as e:
            await interaction.followup.send(
                f"Plex cleanup check failed: {e}",
                ephemeral=True,
            )
            return
        await interaction.followup.send(result.to_discord_summary(), ephemeral=True)

    @app_commands.command(
        name="plexunpopular",
        description="Show low-watch, low-rated Plex titles (admin only)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def plexunpopular(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            result = await self.run_unpopularity_audit(limit=10)
        except Exception as e:
            await interaction.followup.send(
                f"Plex unpopularity audit failed: {e}",
                ephemeral=True,
            )
            return
        await interaction.followup.send(result.to_discord_summary(), ephemeral=True)

    @app_commands.command(
        name="cachestats",
        description="Show cache size and optionally clear it (admin only)",
    )
    @app_commands.describe(clear="Set true to clear the cache")
    @app_commands.default_permissions(manage_guild=True)
    async def cachestats(self, interaction: discord.Interaction, clear: bool = False):
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


async def setup(bot: commands.Bot):
    await bot.add_cog(
        AdminCog(
            bot,
            restart_process=bot.suckling_restart_process,
            post_daily_recommendation=bot.suckling_post_daily_recommendation,
            run_lb_activity_check=bot.suckling_run_lb_activity_check,
            lb_activity_summary=bot.suckling_lb_activity_summary,
            run_plex_cleanup=bot.suckling_run_plex_cleanup,
            run_unpopularity_audit=bot.suckling_run_unpopularity_audit,
        )
    )
