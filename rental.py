"""
Rental system logic.

Handles forum thread creation/editing, late fee calculation, overdue
notifications, and 12-hour reminder DMs. All Discord API calls take
the bot client as a parameter — this module never imports bot.py.
"""
from datetime import datetime, timezone, timedelta

import discord

import db
import embeds
import logger


RENTAL_DURATION_HOURS = 48
LATE_FEE_PER_DAY = 1.0  # dollars


# ---------- core helpers ----------

def compute_due_at(rented_at: datetime) -> datetime:
    return rented_at + timedelta(hours=RENTAL_DURATION_HOURS)


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


# ---------- forum thread management ----------

async def _get_forum_channel(bot: discord.Client) -> discord.ForumChannel | None:
    channel_id = db.get_reviews_channel_id()
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


def _get_applied_tags(
    forum: discord.ForumChannel,
    recommend: bool | None = None,
) -> list[discord.ForumTag]:
    """Build the list of ForumTag objects to apply based on stored tag IDs."""
    tags = []
    rental_tag_id = db.get_rental_tag_id()
    rec_tag_id = db.get_recommendation_tag_id()

    for tag in forum.available_tags:
        if rental_tag_id and tag.id == rental_tag_id:
            tags.append(tag)
        if recommend and rec_tag_id and tag.id == rec_tag_id:
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
    applied_tags = _get_applied_tags(forum)

    try:
        thread, message = await forum.create_thread(
            name=thread_name,
            embed=embed,
            applied_tags=applied_tags,
        )
        db.set_rental_thread(rental_id, thread.id, message.id)
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

        # Update tags (add recommendation tag if applicable) — title stays the same
        forum = await _get_forum_channel(bot)
        if forum:
            applied_tags = _get_applied_tags(forum, recommend=bool(rental.get("recommended")))
            await thread.edit(applied_tags=applied_tags)

    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
        print(f"[rental] Failed to edit thread after return: {e}")


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


# ---------- DM helpers ----------

async def _send_dm(bot: discord.Client, user_id: str, content: str) -> None:
    """Send a DM to a user. Silently swallows errors (DMs disabled, etc.)."""
    try:
        user = await bot.fetch_user(int(user_id))
        await user.send(content)
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
            await _send_dm(
                bot,
                r["user_id"],
                f"⏰ heads up - **{r['title']}** is due in about **{hours_left} hour(s)**.\n"
                f"watch it and use `/return` before the late fees kick in!",
            )
            db.mark_reminder_sent(r["id"])
        except Exception as e:
            logger.log_exception("rental_reminder_notify", e)