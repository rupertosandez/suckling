"""
Rental system logic.

Handles forum thread creation/editing, late fee calculation, overdue
notifications, and 12-hour reminder DMs. All Discord API calls take
the bot client as a parameter — this module never imports bot.py.
"""
import asyncio
from datetime import datetime, time, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord

import config
import db
import embeds
import logger


RENTAL_DURATION_DAYS = 5
RENTAL_DUE_HOUR_LOCAL = 21
RENTAL_EXTENSION_HOURS = 24
MAX_ACTIVE_RENTALS_PER_USER = 3
MAX_RENTAL_EXTENSIONS = 1
LATE_FEE_PER_DAY = 1.0  # dollars


# ---------- core helpers ----------

def validate_timezone(timezone_name: str) -> str | None:
    timezone_name = (timezone_name or "").strip()
    if not timezone_name:
        return None
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return None
    return timezone_name


def _rental_timezone(timezone_name: str | None = None) -> ZoneInfo:
    if timezone_name:
        try:
            return ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            pass
    try:
        return ZoneInfo(config.BOT_TIMEZONE)
    except ZoneInfoNotFoundError:
        return ZoneInfo("America/Los_Angeles")


def default_timezone_name() -> str:
    return _rental_timezone().key


def compute_due_at(rented_at: datetime, timezone_name: str | None = None) -> datetime:
    if rented_at.tzinfo is None:
        rented_at = rented_at.replace(tzinfo=timezone.utc)

    local_tz = _rental_timezone(timezone_name)
    rented_local = rented_at.astimezone(local_tz)
    due_local_date = rented_local.date() + timedelta(days=RENTAL_DURATION_DAYS)
    due_local = datetime.combine(
        due_local_date,
        time(hour=RENTAL_DUE_HOUR_LOCAL),
        tzinfo=local_tz,
    )
    return due_local.astimezone(timezone.utc)


def rental_window_label() -> str:
    return f"{RENTAL_DURATION_DAYS} days, due by 9 pm"


def compute_late_fee(due_at_iso: str, returned_at_iso: str) -> float:
    """
    Return late fee in dollars. $1 per started day overdue.
    Returns 0.0 if returned on time.
    """
    try:
        due = datetime.fromisoformat(due_at_iso)
        returned = datetime.fromisoformat(returned_at_iso)
    except ValueError:
        return 0.0

    if returned <= due:
        return 0.0

    delta = returned - due
    days_late = int(delta.total_seconds() // 86400) + 1  # ceil: any part of a day counts
    return days_late * LATE_FEE_PER_DAY


def compute_extended_due_at(due_at_iso: str) -> datetime | None:
    try:
        current_due = datetime.fromisoformat(due_at_iso)
    except (ValueError, TypeError):
        return None

    now = datetime.now(timezone.utc)
    if current_due.tzinfo is None:
        current_due = current_due.replace(tzinfo=timezone.utc)
    return max(current_due, now) + timedelta(hours=RENTAL_EXTENSION_HOURS)


async def extend_rental(
    bot: discord.Client,
    user_id: str,
    rental_id: int,
) -> tuple[bool, str]:
    rental = await asyncio.to_thread(db.get_rental_by_id, rental_id)
    if not rental or rental.get("status") != "active":
        return False, "that rental is no longer active."
    if str(rental.get("user_id")) != str(user_id):
        return False, "only the person renting this film can extend it."
    if rental.get("extensions_used", 0) >= MAX_RENTAL_EXTENSIONS:
        return False, "you already used the extension for this rental."

    new_due = compute_extended_due_at(rental.get("due_at", ""))
    if new_due is None:
        return False, "i couldn't read the current due date for that rental."

    extended = await asyncio.to_thread(
        db.extend_rental_due_at,
        rental_id=rental_id,
        due_at=new_due.isoformat(),
        max_extensions=MAX_RENTAL_EXTENSIONS,
    )
    if not extended:
        return False, "that rental could not be extended."

    updated = await asyncio.to_thread(db.get_rental_by_id, rental_id)
    if updated:
        await edit_thread_due_at(bot, updated)

    due_ts = int(new_due.timestamp())
    return (
        True,
        f"extended **{rental['title']}** by {RENTAL_EXTENSION_HOURS} hours. "
        f"new due time: <t:{due_ts}:F> (<t:{due_ts}:R>).",
    )


# ---------- forum thread management ----------

async def _get_forum_channel(bot: discord.Client) -> discord.ForumChannel | None:
    channel_id = await asyncio.to_thread(db.get_reviews_channel_id)
    if not channel_id:
        return None
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden):
            return None
    if not isinstance(channel, discord.ForumChannel):
        return None
    return channel


async def _get_applied_tags(
    forum: discord.ForumChannel,
    recommend: bool | None = None,
    has_review: bool = False,
) -> list[discord.ForumTag]:
    """Build the list of ForumTag objects to apply based on stored tag IDs."""
    tags = []
    tag_ids = await asyncio.to_thread(db.get_rental_forum_tag_ids)
    rental_tag_id = tag_ids["rental_tag_id"]
    rec_tag_id = tag_ids["recommendation_tag_id"]
    review_tag_id = tag_ids["review_tag_id"]

    for tag in forum.available_tags:
        if rental_tag_id and tag.id == rental_tag_id:
            tags.append(tag)
        if recommend and rec_tag_id and tag.id == rec_tag_id:
            tags.append(tag)
        if has_review and review_tag_id and tag.id == review_tag_id:
            tags.append(tag)

    return tags


async def create_forum_thread(
    bot: discord.Client,
    rental_id: int,
    movie: dict,
    user_tag: str,
    due_at: datetime,
) -> bool:
    """
    Create the forum thread for a confirmed rental. Stores thread_id and
    message_id back into the DB. Returns True on success.
    """
    forum = await _get_forum_channel(bot)
    if forum is None:
        return False

    thread_name = f"{movie['title']} ({movie.get('year', '?')})"
    # Discord thread names have a 100-char limit
    if len(thread_name) > 100:
        thread_name = thread_name[:97] + "..."

    embed = embeds.rental_confirmed_embed(movie, user_tag, due_at)
    applied_tags = await _get_applied_tags(forum)

    try:
        thread, message = await forum.create_thread(
            name=thread_name,
            embed=embed,
            applied_tags=applied_tags,
        )
        await asyncio.to_thread(db.set_rental_thread, rental_id, thread.id, message.id)
        return True
    except (discord.HTTPException, discord.Forbidden) as e:
        print(f"[rental] Failed to create forum thread: {e}")
        return False


async def edit_thread_returned(
    bot: discord.Client,
    rental: dict,
) -> None:
    """Edit the forum thread's starter message to show the completed review."""
    thread_id = rental.get("thread_id")
    message_id = rental.get("message_id")
    if not thread_id or not message_id:
        return

    try:
        thread = bot.get_channel(int(thread_id))
        if thread is None:
            thread = await bot.fetch_channel(int(thread_id))

        msg = await thread.fetch_message(int(message_id))

        movie = {
            "title": rental["title"],
            "year": rental.get("year"),
            "poster_url": rental.get("poster_url"),
        }
        late_fee = rental.get("late_fee_dollars", 0.0)
        embed = embeds.rental_review_embed(
            movie=movie,
            user_tag=rental["user_name"],
            rating=rental["rating"],
            thoughts=rental.get("thoughts"),
            recommend=bool(rental.get("recommended")),
            returned_at_iso=rental["returned_at"],
            late_fee=late_fee,
        )
        await msg.edit(embed=embed)

        # Update tags (add recommendation/review tags if applicable) — title stays the same
        forum = await _get_forum_channel(bot)
        if forum:
            applied_tags = await _get_applied_tags(
                forum,
                recommend=bool(rental.get("recommended")),
                has_review=bool((rental.get("thoughts") or "").strip()),
            )
            await thread.edit(applied_tags=applied_tags)

    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
        print(f"[rental] Failed to edit thread after return: {e}")


async def edit_thread_returned_unwatched(
    bot: discord.Client,
    rental: dict,
) -> None:
    """Edit the forum thread's starter message to show an unwatched return."""
    thread_id = rental.get("thread_id")
    message_id = rental.get("message_id")
    if not thread_id or not message_id:
        return

    try:
        thread = bot.get_channel(int(thread_id))
        if thread is None:
            thread = await bot.fetch_channel(int(thread_id))

        msg = await thread.fetch_message(int(message_id))

        movie = {
            "title": rental["title"],
            "year": rental.get("year"),
            "poster_url": rental.get("poster_url"),
        }
        embed = embeds.rental_unwatched_return_embed(
            movie=movie,
            user_tag=rental["user_name"],
            returned_at_iso=rental["returned_at"],
            late_fee=rental.get("late_fee_dollars", 0.0),
            reason=rental.get("thoughts"),
        )
        await msg.edit(embed=embed)

        forum = await _get_forum_channel(bot)
        if forum:
            await thread.edit(applied_tags=await _get_applied_tags(forum, recommend=False))

    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
        print(f"[rental] Failed to edit thread after unwatched return: {e}")


async def edit_thread_cancelled(
    bot: discord.Client,
    rental: dict,
    reason: str | None = None,
) -> None:
    """Edit the forum thread's starter message to mark the rental as cancelled."""
    thread_id = rental.get("thread_id")
    message_id = rental.get("message_id")
    if not thread_id or not message_id:
        return

    try:
        thread = bot.get_channel(int(thread_id))
        if thread is None:
            thread = await bot.fetch_channel(int(thread_id))

        msg = await thread.fetch_message(int(message_id))

        movie = {
            "title": rental["title"],
            "year": rental.get("year"),
            "poster_url": rental.get("poster_url"),
        }
        embed = embeds.rental_cancelled_embed(movie, rental["user_name"], reason)
        await msg.edit(embed=embed)

    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
        print(f"[rental] Failed to edit thread after cancel: {e}")


async def edit_thread_due_at(
    bot: discord.Client,
    rental: dict,
) -> None:
    """Refresh the forum thread opener after a rental due date changes."""
    thread_id = rental.get("thread_id")
    message_id = rental.get("message_id")
    if not thread_id or not message_id:
        return

    try:
        thread = bot.get_channel(int(thread_id))
        if thread is None:
            thread = await bot.fetch_channel(int(thread_id))

        msg = await thread.fetch_message(int(message_id))
        due_at = datetime.fromisoformat(rental["due_at"])
        due_ts = int(due_at.timestamp())
        if msg.embeds:
            embed = discord.Embed.from_dict(msg.embeds[0].to_dict())
            existing_fields = [
                field
                for field in embed.fields
                if field.name.lower() not in ("due back", "extension")
            ]
            embed.clear_fields()
            for field in existing_fields:
                embed.add_field(
                    name=field.name,
                    value=field.value,
                    inline=field.inline,
                )
            embed.add_field(
                name="Due Back",
                value=f"<t:{due_ts}:F> (<t:{due_ts}:R>)",
                inline=False,
            )
        else:
            movie = {
                "title": rental["title"],
                "year": rental.get("year"),
                "summary": "",
                "thumb_url": rental.get("poster_url"),
            }
            embed = embeds.rental_confirmed_embed(movie, rental["user_name"], due_at)
        if rental.get("extensions_used", 0):
            embed.add_field(
                name="Extension",
                value=f"+{RENTAL_EXTENSION_HOURS} hours",
                inline=True,
            )
        await msg.edit(embed=embed)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError) as e:
        print(f"[rental] Failed to edit thread after extension: {e}")


# ---------- DM helpers ----------

class RentalExtensionView(discord.ui.View):
    """One-click 24-hour rental extension for reminder DMs."""

    def __init__(self, rental_id: int, user_id: str):
        super().__init__(timeout=7 * 24 * 3600)
        self.rental_id = rental_id
        self.user_id = str(user_id)

    @discord.ui.button(label="extend 24h", style=discord.ButtonStyle.primary)
    async def extend_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        ok, message = await extend_rental(
            bot=interaction.client,
            user_id=str(interaction.user.id),
            rental_id=self.rental_id,
        )
        button.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(message)
        if ok:
            self.stop()


async def _send_dm(
    bot: discord.Client,
    user_id: str,
    content: str,
    view: discord.ui.View | None = None,
) -> None:
    """Send a DM to a user. Silently swallows errors (DMs disabled, etc.)."""
    try:
        user = await bot.fetch_user(int(user_id))
        await user.send(content, view=view)
    except (discord.Forbidden, discord.HTTPException, discord.NotFound):
        pass


# ---------- scheduled jobs ----------

async def check_overdue(bot: discord.Client) -> None:
    """
    DM users whose rentals are overdue and haven't been notified yet.
    Called hourly by the scheduler.
    """
    rentals = db.get_overdue_active_rentals()
    if not rentals:
        return

    print(f"[rental] {len(rentals)} overdue rental(s) to notify")
    now = datetime.now(timezone.utc)

    for r in rentals:
        try:
            due = datetime.fromisoformat(r["due_at"])
            days_late = max(1, int((now - due).total_seconds() // 86400) + 1)
            fee_so_far = days_late * LATE_FEE_PER_DAY
            await _send_dm(
                bot,
                r["user_id"],
                f"📼 **{r['title']}** is overdue! late fees are accruing - "
                f"currently at **${fee_so_far:.2f}** and counting.\n"
                f"use `/return` to close it out whenever you're done.",
            )
            db.mark_overdue_notified(r["id"])
        except Exception as e:
            logger.log_exception("rental_overdue_notify", e)


async def check_reminders(bot: discord.Client) -> None:
    """
    DM users who have less than 12 hours left on their rental and haven't
    been reminded yet. Called hourly by the scheduler.
    """
    rentals = db.get_reminder_due_rentals()
    if not rentals:
        return

    print(f"[rental] {len(rentals)} rental reminder(s) to send")
    now = datetime.now(timezone.utc)

    for r in rentals:
        try:
            due = datetime.fromisoformat(r["due_at"])
            remaining_seconds = (due - now).total_seconds()
            hours_left = max(1, int(remaining_seconds // 3600))
            can_extend = r.get("extensions_used", 0) < MAX_RENTAL_EXTENSIONS
            extension_note = ""
            extension_view = None
            if can_extend:
                extension_note = (
                    f"\n\nneed more time? use the button below for a one-time "
                    f"{RENTAL_EXTENSION_HOURS}-hour extension."
                )
                extension_view = RentalExtensionView(
                    rental_id=r["id"],
                    user_id=r["user_id"],
                )
            await _send_dm(
                bot,
                r["user_id"],
                f"⏰ heads up - **{r['title']}** is due in about **{hours_left} hour(s)**.\n"
                f"watch it and use `/return` before the late fees kick in!"
                f"{extension_note}",
                view=extension_view,
            )
            db.mark_reminder_sent(r["id"])
        except Exception as e:
            logger.log_exception("rental_reminder_notify", e)
