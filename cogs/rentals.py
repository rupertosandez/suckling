import asyncio
from datetime import datetime, timezone
from zoneinfo import available_timezones

import discord
from discord import app_commands
from discord.ext import commands

import db
import embeds
import logger
import macguffin as macguffin_module
import plex
import rental as rental_module
import views
import achievements as achievement_module


COMMON_TIMEZONES = (
    "America/Los_Angeles",
    "America/Denver",
    "America/Chicago",
    "America/New_York",
    "America/Phoenix",
    "America/Anchorage",
    "Pacific/Honolulu",
    "America/Toronto",
    "Europe/London",
    "Europe/Paris",
    "Europe/Berlin",
    "UTC",
)
ALL_TIMEZONES = tuple(sorted(available_timezones()))


async def _timezone_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    current = (current or "").strip().lower()
    if current:
        matches = [
            tz for tz in ALL_TIMEZONES
            if current in tz.lower()
        ]
    else:
        matches = list(COMMON_TIMEZONES)
    return [
        app_commands.Choice(name=tz, value=tz)
        for tz in matches[:25]
    ]


def _format_active_rental_options(rentals: list[dict]) -> str:
    lines = []
    for rental in rentals:
        try:
            due = datetime.fromisoformat(rental.get("due_at", ""))
            due_text = f"due <t:{int(due.timestamp())}:R>"
        except (ValueError, TypeError):
            due_text = "due time unknown"
        lines.append(
            f"`{rental['id']}` - **{rental.get('title', 'unknown')}** "
            f"({rental.get('year') or '?'}) - {due_text}"
        )
    return "\n".join(lines)


def _active_rental_limit_message(rentals: list[dict]) -> str:
    active_count = len(rentals)
    message = (
        f"you already have **{active_count}** active rentals. "
        "return one before checking out another."
    )
    if rentals:
        message += f"\n\n{_format_active_rental_options(rentals)}"
    return message


def _macguffin_weights_for_rental(rental: dict) -> dict[str, int] | None:
    if rental.get("initiated_by") in ("random", "command"):
        return {"common": 50, "rare": 40, "iconic": 10}
    return None


async def _resolve_active_rental_for_command(
    interaction: discord.Interaction,
    rental_query: str | None,
) -> dict | None:
    user_id = str(interaction.user.id)
    active = db.get_active_rentals(user_id)

    if not active:
        await interaction.followup.send(
            "you don't have an active rental. use `/rent` to grab something.",
            ephemeral=True,
        )
        return None

    if rental_query:
        matches = db.find_active_rental(user_id, rental_query)
        if len(matches) == 1:
            return matches[0]
        if matches:
            await interaction.followup.send(
                "that matched more than one active rental:\n"
                f"{_format_active_rental_options(matches)}\n\n"
                "try again with the rental id.",
                ephemeral=True,
            )
            return None
        await interaction.followup.send(
            f"couldn't find an active rental matching **{rental_query}**.",
            ephemeral=True,
        )
        return None

    if len(active) == 1:
        return active[0]

    await interaction.followup.send(
        "which rental?\n"
        f"{_format_active_rental_options(active)}\n\n"
        "run the command again with the rental id or part of the title.",
        ephemeral=True,
    )
    return None


def _rental_label(rental: dict) -> str:
    year = f" ({rental.get('year')})" if rental.get("year") else ""
    return f"{rental.get('title', 'unknown')}{year}"


async def _send_component_denied(
    interaction: discord.Interaction,
    message: str,
) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


def _parse_yes_no(value: str) -> bool | None:
    normalized = (value or "").strip().lower()
    if normalized in {"yes", "y", "true", "t", "1", "recommend", "recommended"}:
        return True
    if normalized in {"no", "n", "false", "f", "0", "not recommended"}:
        return False
    return None


class ReturnRentalSelect(discord.ui.Select):
    def __init__(self, parent: "ReturnRentalPickerView"):
        self.parent_view = parent
        options = []
        for rental_record in parent.rentals[:25]:
            label = _rental_label(rental_record)
            options.append(
                discord.SelectOption(
                    label=label[:100],
                    value=str(rental_record["id"]),
                )
            )
        super().__init__(
            placeholder="Choose a rental to return",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.parent_view.user_id:
            await _send_component_denied(interaction, "this return menu isn't for you.")
            return

        rental_record = db.get_active_rental_by_id(
            self.parent_view.user_id,
            int(self.values[0]),
        )
        if not rental_record:
            await interaction.response.edit_message(
                content="that rental is no longer active.",
                view=None,
            )
            return

        view = ReturnChoiceView(self.parent_view.cog, self.parent_view.user_id, rental_record)
        await interaction.response.edit_message(
            content=(
                f"returning **{_rental_label(rental_record)}**\n\n"
                "did you watch it?"
            ),
            view=view,
        )


class ReturnRentalPickerView(discord.ui.View):
    def __init__(self, cog: "RentalsCog", user_id: str, rentals: list[dict]):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id
        self.rentals = rentals
        self.add_item(ReturnRentalSelect(self))


class ReturnChoiceView(discord.ui.View):
    def __init__(self, cog: "RentalsCog", user_id: str, rental_record: dict):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id
        self.rental_record = rental_record

    async def _ensure_owner(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) == self.user_id:
            return True
        await _send_component_denied(interaction, "this return menu isn't for you.")
        return False

    @discord.ui.button(label="watched it", style=discord.ButtonStyle.primary)
    async def watched(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not await self._ensure_owner(interaction):
            return
        await interaction.response.send_modal(
            WatchedReturnModal(self.cog, self.rental_record["id"])
        )

    @discord.ui.button(label="didn't watch", style=discord.ButtonStyle.secondary)
    async def unwatched(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not await self._ensure_owner(interaction):
            return
        await interaction.response.send_modal(
            UnwatchedReturnModal(self.cog, self.rental_record["id"])
        )


class WatchedReturnModal(discord.ui.Modal, title="Return Watched Rental"):
    rating_input = discord.ui.TextInput(
        label="Rating (optional)",
        placeholder="1-10",
        required=False,
        max_length=2,
    )
    recommended_input = discord.ui.TextInput(
        label="Recommended?",
        placeholder="yes or no",
        required=True,
        max_length=20,
    )
    thoughts_input = discord.ui.TextInput(
        label="Thoughts (optional)",
        placeholder="Drop a short review if you want",
        required=False,
        max_length=1000,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, cog: "RentalsCog", rental_id: int):
        super().__init__()
        self.cog = cog
        self.rental_id = rental_id

    async def on_submit(self, interaction: discord.Interaction):
        rating_text = str(self.rating_input.value or "").strip()
        rating = None
        if rating_text:
            if not rating_text.isdigit() or not 1 <= int(rating_text) <= 10:
                await interaction.response.send_message(
                    "⚠️ Rating has to be a number from 1 to 10.",
                    ephemeral=True,
                )
                return
            rating = int(rating_text)

        recommend = _parse_yes_no(str(self.recommended_input.value))
        if recommend is None:
            await interaction.response.send_message(
                "⚠️ Recommended has to be `yes` or `no`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        await self.cog._complete_watched_return(
            interaction=interaction,
            rental_id=self.rental_id,
            rating=rating,
            recommend=recommend,
            thoughts=str(self.thoughts_input.value or "").strip() or None,
        )


class UnwatchedReturnModal(discord.ui.Modal, title="Return Unwatched Rental"):
    reason_input = discord.ui.TextInput(
        label="Reason (optional)",
        placeholder="Ran out of time, picked the wrong vibe, etc.",
        required=False,
        max_length=500,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, cog: "RentalsCog", rental_id: int):
        super().__init__()
        self.cog = cog
        self.rental_id = rental_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.cog._complete_unwatched_return(
            interaction=interaction,
            rental_id=self.rental_id,
            reason=str(self.reason_input.value or "").strip() or None,
        )


class RentalsCog(commands.Cog):
    """Video store rental commands and rental admin tools."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _complete_watched_return(
        self,
        interaction: discord.Interaction,
        rental_id: int,
        rating: int | None,
        recommend: bool,
        thoughts: str | None,
    ) -> None:
        user_id = str(interaction.user.id)
        rental_record = db.get_active_rental_by_id(user_id, rental_id)
        if not rental_record:
            await interaction.followup.send(
                "that rental is no longer active.",
                ephemeral=True,
            )
            return

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        late_fee = rental_module.compute_late_fee(rental_record["due_at"], now_iso)

        db.mark_rental_returned(
            rental_id=rental_record["id"],
            returned_at=now_iso,
            rating=rating,
            thoughts=thoughts,
            recommended=recommend,
            late_fee_dollars=late_fee,
        )

        updated_rental = db.get_rental_by_id(rental_record["id"])
        await rental_module.edit_thread_returned(self.bot, updated_rental)

        title = rental_record.get("title", "your film")
        late_note = f"\nlate fee: **${late_fee:.2f}**" if late_fee > 0 else ""
        rec_note = "recommended" if recommend else "not recommended"
        rating_note = f"rating: {rating}/10, " if rating is not None else ""

        await interaction.followup.send(
            f"✅ **{title}** returned. {rating_note}{rec_note}.{late_note}\n"
            f"-# review posted to the forum.",
            ephemeral=True,
        )
        try:
            card = await asyncio.to_thread(
                macguffin_module.drop_macguffin,
                user_id,
                str(interaction.user),
                f"return:{rental_record.get('initiated_by', 'selected')}",
                _macguffin_weights_for_rental(rental_record),
            )
            claimed = await asyncio.to_thread(db.get_claimed_macguffin_ids)
            total = len(macguffin_module.CARDS)
            drop_embed = embeds.macguffin_drop_embed(
                card,
                interaction.user.mention,
                len(claimed),
                total,
            )
            if interaction.channel:
                await interaction.channel.send(embed=drop_embed)
        except macguffin_module.MacGuffinPoolEmpty:
            pass
        except Exception as e:
            logger.log_exception("macguffin_return_drop", e)

        await achievement_module.award_for_user(
            self.bot,
            interaction.user,
            source_type="rental_return",
            source_id=str(rental_record["id"]),
            rental=updated_rental,
        )

    async def _complete_unwatched_return(
        self,
        interaction: discord.Interaction,
        rental_id: int,
        reason: str | None,
    ) -> None:
        rental_record = db.get_active_rental_by_id(str(interaction.user.id), rental_id)
        if not rental_record:
            await interaction.followup.send(
                "that rental is no longer active.",
                ephemeral=True,
            )
            return

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        late_fee = rental_module.compute_late_fee(rental_record["due_at"], now_iso)

        db.mark_rental_returned_unwatched(
            rental_id=rental_record["id"],
            returned_at=now_iso,
            reason=reason,
            late_fee_dollars=late_fee,
        )

        updated_rental = db.get_rental_by_id(rental_record["id"])
        await rental_module.edit_thread_returned_unwatched(self.bot, updated_rental)

        late_note = f"\nlate fee: **${late_fee:.2f}**" if late_fee > 0 else ""
        await interaction.followup.send(
            f"↩ **{rental_record['title']}** returned unwatched.{late_note}\n"
            "-# no review posted, no achievements or macguffin drop.",
            ephemeral=True,
        )

    @app_commands.command(name="rent", description="rent from rb9 - roll random, pick one, or ask an admin")
    async def rent(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)

        active_rentals = db.get_active_rentals(user_id)
        active_count = len(active_rentals)
        if active_count >= rental_module.MAX_ACTIVE_RENTALS_PER_USER:
            await interaction.response.send_message(
                _active_rental_limit_message(active_rentals),
                ephemeral=True,
            )
            return

        warning_view = views.RentWarningView(
            bot=self.bot,
            user_id=user_id,
            user_name=str(interaction.user),
        )
        await interaction.response.send_message(
            "📼 **choose your rental path**\n\n"
            f"you currently have **{active_count}/"
            f"{rental_module.MAX_ACTIVE_RENTALS_PER_USER}** rentals active. "
            "once a rental is confirmed, it's due by 9 pm on the fifth day.\n\n"
            "**roll random** gives you up to 2 rerolls and boosted macguffin odds "
            "when you return it. **pick a movie** lets you choose from rb9. "
            "**ask an admin** posts a recommendation request.",
            view=warning_view,
            ephemeral=True,
        )

    @app_commands.command(
        name="timezone",
        description="set your timezone for rental due dates",
    )
    @app_commands.describe(
        timezone_name="IANA timezone, like America/Los_Angeles or Europe/London",
        clear="clear your saved timezone and use the server default",
    )
    @app_commands.autocomplete(timezone_name=_timezone_autocomplete)
    async def timezone(
        self,
        interaction: discord.Interaction,
        timezone_name: str | None = None,
        clear: bool = False,
    ):
        user_id = str(interaction.user.id)
        if clear:
            db.clear_user_timezone(user_id)
            await interaction.response.send_message(
                "cleared your rental timezone. i'll use the server default for future rentals.",
                ephemeral=True,
            )
            return

        if timezone_name is None:
            saved = db.get_user_timezone(user_id)
            if saved:
                await interaction.response.send_message(
                    f"your rental timezone is **{saved}**.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "you don't have a rental timezone set yet. "
                    f"future rentals use the server default: **{rental_module.default_timezone_name()}**.",
                    ephemeral=True,
                )
            return

        normalized = rental_module.validate_timezone(timezone_name)
        if normalized is None:
            await interaction.response.send_message(
                "i don't recognize that timezone. try one like "
                "`America/Los_Angeles`, `America/New_York`, or `Europe/London`.",
                ephemeral=True,
            )
            return

        db.set_user_timezone(user_id, normalized)
        await interaction.response.send_message(
            f"set your rental timezone to **{normalized}**. "
            "future rentals will be due at 9 pm in that timezone on the fifth day.",
            ephemeral=True,
        )

    @app_commands.command(name="return", description="start the private return flow")
    async def return_film(
        self,
        interaction: discord.Interaction,
    ):
        user_id = str(interaction.user.id)

        active = db.get_active_rentals(user_id)
        if not active:
            await interaction.response.send_message(
                "you don't have an active rental. use `/rent` to grab something.",
                ephemeral=True,
            )
            return

        if len(active) == 1:
            rental_record = active[0]
            view = ReturnChoiceView(self, user_id, rental_record)
            await interaction.response.send_message(
                f"returning **{_rental_label(rental_record)}**\n\n"
                "did you watch it?",
                view=view,
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "which rental are you returning?",
            view=ReturnRentalPickerView(self, user_id, active),
            ephemeral=True,
        )

    @app_commands.command(name="myrental", description="check your current rental and time remaining")
    async def myrental(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        rentals = db.get_active_rentals(user_id)

        if not rentals:
            await interaction.response.send_message(
                "you don't have anything checked out right now. use `/rent` to grab something.",
                ephemeral=True,
            )
            return

        embed = (
            embeds.rental_status_embed(rentals[0])
            if len(rentals) == 1
            else embeds.rental_status_list_embed(
                rentals,
                str(interaction.user),
                rental_module.MAX_ACTIVE_RENTALS_PER_USER,
            )
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="extend", description="extend an active rental by 24 hours")
    @app_commands.describe(
        rental="rental id or part of the title, if you have more than one active rental",
    )
    async def extend(self, interaction: discord.Interaction, rental: str | None = None):
        await interaction.response.defer(ephemeral=True)
        rental_record = await _resolve_active_rental_for_command(interaction, rental)
        if not rental_record:
            return

        _, message = await rental_module.extend_rental(
            bot=self.bot,
            user_id=str(interaction.user.id),
            rental_id=rental_record["id"],
        )
        await interaction.followup.send(message, ephemeral=True)

    @app_commands.command(name="latefees", description="see who owes the store money")
    async def latefees(self, interaction: discord.Interaction):
        await interaction.response.defer()
        rows = db.get_late_fees_leaderboard(limit=10)
        embed = embeds.late_fees_embed(rows)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="rentalstats", description="your rental history and stats")
    @app_commands.describe(user="optional: check another user's stats")
    async def rentalstats(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ):
        await interaction.response.defer()
        target = user or interaction.user
        history = db.get_user_rental_history(str(target.id))
        total_pages = max(1, -(-len(history) // embeds.RENTAL_HISTORY_PAGE_SIZE))
        embed = embeds.rental_stats_embed(history, str(target), page=0, total_pages=total_pages)
        view = (
            views.RentalHistoryView(str(target), history)
            if total_pages > 1
            else None
        )
        await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(
        name="setreviews",
        description="set the forum channel for rental reviews (admin only)",
    )
    @app_commands.describe(channel="the forum channel where rental reviews will post")
    @app_commands.default_permissions(manage_guild=True)
    async def set_reviews(
        self,
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

    @app_commands.command(
        name="setrentalrequests",
        description="set where admin rental recommendation requests post (admin only)",
    )
    @app_commands.describe(channel="the channel where rental recommendation requests should post")
    @app_commands.default_permissions(manage_guild=True)
    async def set_rental_requests(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        perms = channel.permissions_for(interaction.guild.me)
        if not perms.send_messages:
            await interaction.response.send_message(
                f"⚠️ i need **send messages** permission in {channel.mention}.",
                ephemeral=True,
            )
            return

        db.set_rental_request_channel_id(channel.id)
        await interaction.response.send_message(
            f"✅ rental recommendation requests will post in {channel.mention}.",
            ephemeral=True,
        )

    @app_commands.command(
        name="cancelrental",
        description="cancel a user's active rental with no late fee (admin only)",
    )
    @app_commands.describe(
        user="the user whose rental to cancel",
        rental="rental id or part of the title, if they have more than one active rental",
        reason="optional reason (shown in the forum thread)",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def cancel_rental(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        rental: str | None = None,
        reason: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        active = db.get_active_rentals(str(user.id))
        if not active:
            await interaction.followup.send(
                f"**{user}** doesn't have an active rental.", ephemeral=True
            )
            return
        if rental:
            matches = db.find_active_rental(str(user.id), rental)
        else:
            matches = active
        if not matches:
            await interaction.followup.send(
                f"couldn't find an active rental for **{user}** matching **{rental}**.",
                ephemeral=True,
            )
            return
        if len(matches) != 1:
            await interaction.followup.send(
                f"which rental for **{user}**?\n"
                f"{_format_active_rental_options(matches)}\n\n"
                "try again with the rental id.",
                ephemeral=True,
            )
            return
        rental_record = matches[0]

        db.cancel_rental_by_id(rental_record["id"])
        await rental_module.edit_thread_cancelled(self.bot, rental_record, reason)

        reason_str = f" reason: {reason}" if reason else ""
        await rental_module._send_dm(
            self.bot,
            str(user.id),
            f"📼 your rental of **{rental_record['title']}** was cancelled by an admin.{reason_str}",
        )

        await interaction.followup.send(
            f"✅ cancelled **{rental_record['title']}** for **{user}**.{reason_str}",
            ephemeral=True,
        )

    @app_commands.command(
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
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        title: str,
        year: int | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        active_rentals = db.get_active_rentals(str(user.id))
        active_count = len(active_rentals)
        if active_count >= rental_module.MAX_ACTIVE_RENTALS_PER_USER:
            await interaction.followup.send(
                f"**{user}** already has **{active_count}** active rentals.\n\n"
                f"{_format_active_rental_options(active_rentals)}",
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
        user_timezone = db.get_user_timezone(str(user.id))
        due_at = rental_module.compute_due_at(now, user_timezone)
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
            initiated_by="admin_recommended",
        )

        thread_ok = await rental_module.create_forum_thread(
            bot=self.bot,
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
            self.bot,
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


async def setup(bot: commands.Bot):
    await bot.add_cog(RentalsCog(bot))
