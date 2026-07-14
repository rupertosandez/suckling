"""
Portal outbox worker (sucklingweb spec 18).

The portal files request slips into web_rental_requests (a web-owned
table it migrates via Alembic - this bot never creates it). This worker
is the single consumer: it claims pending slips oldest-first and executes
them through the bot's own code paths, which is the whole point of the
outbox - forum threads, macguffin odds, and rental limits only ever run
here, never in the web app.

Write contract (C1 amendment in sucklingweb/spec/02-constraints.md): this
bot UPDATEs only status, result_message, result_rental_id, processed_at,
offer_plex_key, offer_title, offer_year, rerolls_remaining. It never
inserts or deletes request rows. result_message is member-facing copy in
the bot's voice - lowercase, casual, no em dashes.

Actions: ping (M-R-a), rent (M-R-b, the pick-a-movie path via
rental.execute_confirmed_rental - the same core the Discord buttons
call). return arrives at M-R-c, the roll protocol at M-R-d.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord

import config
import db
import plex
import rental as rental_module

# A slip older than this was already declared dead to the member by the
# portal's 15-minute expiry; don't surprise them by executing it late.
PENDING_MAX_AGE_MINUTES = 15

_table_missing_logged = False


def _too_old(row: dict) -> bool:
    try:
        created = datetime.fromisoformat(str(row["created_at"]))
    except ValueError:
        return True
    return datetime.now(timezone.utc) - created > timedelta(minutes=PENDING_MAX_AGE_MINUTES)


async def _display_name(bot: discord.Client | None, user_id: str) -> str:
    """The rentals row wants a user_name; slash commands get it from the
    interaction, the worker resolves it from the guild (cache first,
    fetch as fallback) and degrades to the raw id."""
    if bot is not None:
        try:
            guild = bot.get_guild(config.GUILD_ID)
            if guild:
                member = guild.get_member(int(user_id))
                if member is None:
                    member = await guild.fetch_member(int(user_id))
                if member:
                    return str(member)
        except (discord.HTTPException, ValueError):
            pass
    return f"member {user_id}"


async def _handle_ping(bot: discord.Client | None, row: dict) -> tuple[str, str, int | None]:
    """Handlers return (status, result_message, result_rental_id)."""
    return "done", "pong. the clerk is in.", None


async def _handle_rent(bot: discord.Client | None, row: dict) -> tuple[str, str, int | None]:
    """The pick-a-movie path (spec 18 M-R-b). initiated_by='selected',
    exactly like the Discord pick flow, so macguffin weights and
    achievement events stay identical - portal provenance is this request
    row itself, never a new initiated_by value."""
    plex_key = str(row.get("plex_key") or "").strip()
    if not plex_key:
        return "failed", "that request slip had no film on it - try again from a film page.", None

    movie_row = await db.run(db.get_plex_movie_by_key, plex_key)
    if not movie_row:
        return "failed", "that tape isn't on the shelf anymore - pick something else.", None
    movie = plex._hydrate_cached_movie(movie_row)

    user_id = str(row["discord_id"])
    user_name = await _display_name(bot, user_id)
    result = await rental_module.execute_confirmed_rental(
        bot=bot,
        movie=movie,
        user_id=user_id,
        user_name=user_name,
        rerolls_used=0,
        initiated_by="selected",
    )
    if not result["ok"]:
        # Already member-facing copy (cap message, unconfigured forum).
        # Strip discord markdown bold - the portal renders it as plain text.
        return "failed", str(result["error"]).replace("**", ""), None

    title = f"{movie['title']} ({movie.get('year', '?')})"
    due_at = result["due_at"]
    # day formatted manually: strftime's no-pad flag isn't portable to
    # Windows, where this bot lives
    due_local = f"{due_at.strftime('%A %B')} {due_at.day}"
    message = f"tape's yours - {title} is due back by 9 pm on {due_local}."
    if not result["thread_ok"]:
        message += " (the discord thread didn't post - tell the maintainer.)"
    return "done", message, result["rental_id"]


_HANDLERS = {
    "ping": _handle_ping,
    "rent": _handle_rent,
}


async def process_pending(bot: discord.Client | None = None) -> None:
    """One worker tick (scheduler entry point). DB reads/writes run off
    the event loop via db.run (the P0 lesson); the handlers themselves are
    async because rental execution awaits Discord calls. Claims are
    UPDATE-guarded on status='pending' so a restart mid-batch can't
    double-execute."""
    global _table_missing_logged
    try:
        rows = await db.run(db.get_pending_rental_requests)
    except Exception as exc:
        # The web app owns this table's migration; before it has run there
        # is nothing to consume. Log once, not every 5 seconds.
        if not _table_missing_logged:
            print(f"[outbox] web_rental_requests unavailable ({exc.__class__.__name__}) - waiting for the portal migration")
            _table_missing_logged = True
        return
    _table_missing_logged = False

    processed = 0
    skipped = 0
    for row in rows:
        request_id = int(row["id"])
        if _too_old(row):
            await db.run(
                db.complete_rental_request, request_id, "expired",
                "the clerk never came back - try again later, or rent in discord.",
            )
            skipped += 1
            continue
        if not await db.run(db.claim_rental_request, request_id):
            continue  # portal stamped it expired between fetch and claim
        handler = _HANDLERS.get(str(row["action"]))
        if handler is None:
            await db.run(
                db.complete_rental_request, request_id, "failed",
                "the clerk doesn't know how to handle that request yet.",
            )
            skipped += 1
            continue
        try:
            status, message, rental_id = await handler(bot, row)
            await db.run(db.complete_rental_request, request_id, status, message, rental_id)
            processed += 1
        except Exception as exc:
            print(f"[outbox] request {request_id} ({row['action']}) failed: {exc.__class__.__name__}: {exc}")
            await db.run(
                db.complete_rental_request, request_id, "failed",
                "something went wrong behind the counter - try again, or rent in discord.",
            )
            skipped += 1
    if processed or skipped:
        print(f"[outbox] tick: {processed} processed, {skipped} skipped/expired")
