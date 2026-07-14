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

Actions: ping (M-R-a), rent (M-R-b, the pick-a-movie path), return
(M-R-c, both branches via rental.execute_watched_return /
execute_unwatched_return), and the roll protocol (M-R-d: roll ->
offered row; roll_accept / roll_reroll answer an offer via
parent_request_id). All executed through the same cores the Discord
buttons and modals call.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import discord

import config
import db
import plex
import rental as rental_module

ROLL_MAX_REROLLS = 2
# Accepting an offer older than this fails politely - the tape was never
# held, so nothing needs releasing.
OFFER_MAX_AGE_MINUTES = 30
NOTIFY_CHANNEL = "portal_outbox"

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


async def _rental_thread_channel(bot: discord.Client | None, rental: dict | None):
    """The rental's forum thread, for announcing a portal return's
    macguffin drop where the club can see it. Best-effort."""
    if bot is None or not rental or not rental.get("thread_id"):
        return None
    try:
        channel = bot.get_channel(int(rental["thread_id"]))
        if channel is None:
            channel = await bot.fetch_channel(int(rental["thread_id"]))
        return channel
    except (discord.HTTPException, ValueError):
        return None


async def _handle_return(bot: discord.Client | None, row: dict) -> tuple[str, str, int | None]:
    """Spec 18 M-R-c: both branches, through the same cores the Discord
    return modals call - fees, thread edit, the 50% macguffin roll,
    achievements all fire identically."""
    user_id = str(row["discord_id"])
    rental_id = row.get("rental_id")
    if not rental_id:
        return "failed", "that request slip had no rental on it - try again from your tapes.", None

    if not row.get("watched"):
        result = await rental_module.execute_unwatched_return(
            bot=bot, user_id=user_id, rental_id=int(rental_id), reason=None,
        )
        if not result["ok"]:
            return "failed", str(result["error"]).replace("**", ""), None
        late_fee = result["late_fee"]
        message = f"{result['rental']['title']} returned unwatched - no review, no drop, no judgment."
        if late_fee > 0:
            message += f" late fee: ${late_fee:.2f}."
        return "done", message, int(rental_id)

    rating = row.get("rating")
    recommended = row.get("recommended")
    user = None
    if bot is not None:
        try:
            user = bot.get_user(int(user_id)) or await bot.fetch_user(int(user_id))
        except (discord.HTTPException, ValueError):
            user = None

    rental = await db.run(db.get_rental_by_id, int(rental_id))
    announce_channel = await _rental_thread_channel(bot, rental)
    result = await rental_module.execute_watched_return(
        bot=bot,
        user=user,
        user_id=user_id,
        user_tag=str(user) if user else await _display_name(bot, user_id),
        rental_id=int(rental_id),
        rating=int(rating) if rating is not None else None,
        recommend=bool(recommended) if recommended is not None else None,
        thoughts=row.get("thoughts"),
        announce_channel=announce_channel,
    )
    if not result["ok"]:
        return "failed", str(result["error"]).replace("**", ""), None

    late_fee = result["late_fee"]
    message = f"{result['rental']['title']} returned - review posted to your thread."
    if late_fee > 0:
        message += f" late fee: ${late_fee:.2f}."
    if result["dropped_card"]:
        message += f" and hey - a macguffin dropped: {result['dropped_card'].get('name', 'check discord')}."
    return "done", message, int(rental_id)


async def _roll_chain_exclusions(row: dict) -> tuple[set[str], list[dict]]:
    """Walk parent links to collect every film already offered in this
    roll chain (a reroll must not re-offer them), returning the exclusion
    keys and the chain rows (root first is not guaranteed; callers only
    count)."""
    chain: list[dict] = []
    exclude: set[str] = set()
    current = row
    for _ in range(ROLL_MAX_REROLLS + 2):  # bounded walk; chains are short
        parent_id = current.get("parent_request_id")
        if not parent_id:
            break
        parent = await db.run(db.get_rental_request, int(parent_id))
        if not parent:
            break
        chain.append(parent)
        if parent.get("offer_plex_key"):
            exclude.add(str(parent["offer_plex_key"]))
        current = parent
    return exclude, chain


async def _write_offer(bot: discord.Client | None, row: dict, rerolls_remaining: int, exclude: set[str]) -> tuple[str, str, int | None]:
    user_id = str(row["discord_id"])
    count = await rental_module.active_rental_count(user_id)
    if count >= rental_module.MAX_ACTIVE_RENTALS_PER_USER:
        return "failed", rental_module.active_rental_limit_message(count).replace("**", ""), None

    rented = await db.run(db.get_user_rented_plex_keys, user_id)
    try:
        movie = await plex.pick_random_for_rental(set(rented) | exclude)
    except plex.PlexError as exc:
        return "failed", f"couldn't reach the library right now ({exc}) - try again in a bit.", None
    if movie is None:
        return "failed", "looks like you've rented everything in the library - nothing left to offer.", None

    await db.run(
        db.set_rental_request_offer,
        int(row["id"]), str(movie["rating_key"]), movie["title"], movie.get("year"),
        rerolls_remaining,
    )
    # status already written by set_rental_request_offer; the sentinel
    # tells process_pending not to complete_rental_request over it
    return "offered", "", None


async def _handle_roll(bot: discord.Client | None, row: dict) -> tuple[str, str, int | None]:
    return await _write_offer(bot, row, ROLL_MAX_REROLLS, set())


async def _validated_parent_offer(row: dict) -> tuple[dict | None, str | None]:
    """Shared roll_accept/roll_reroll validation. Returns (parent, None)
    or (None, member-facing error)."""
    parent_id = row.get("parent_request_id")
    if not parent_id:
        return None, "that request wasn't answering any offer - roll again."
    parent = await db.run(db.get_rental_request, int(parent_id))
    if not parent or str(parent["discord_id"]) != str(row["discord_id"]):
        return None, "that offer isn't yours to answer - roll again."
    if parent.get("status") != "offered" or not parent.get("offer_plex_key"):
        return None, "that offer never happened - roll again."
    siblings = await db.run(db.get_rental_request_children, int(parent_id))
    answered = [s for s in siblings if int(s["id"]) != int(row["id"])]
    if answered:
        return None, "that offer was already answered - roll again."
    return parent, None


def _offer_stale(parent: dict) -> bool:
    try:
        offered_at = datetime.fromisoformat(str(parent.get("processed_at")))
    except (TypeError, ValueError):
        return True
    return datetime.now(timezone.utc) - offered_at > timedelta(minutes=OFFER_MAX_AGE_MINUTES)


async def _handle_roll_reroll(bot: discord.Client | None, row: dict) -> tuple[str, str, int | None]:
    parent, error = await _validated_parent_offer(row)
    if error:
        return "failed", error, None
    rerolls_remaining = int(parent.get("rerolls_remaining") or 0)
    if rerolls_remaining <= 0:
        return "failed", "no rerolls left on that one - take it or roll fresh.", None
    exclude, _ = await _roll_chain_exclusions(row)
    exclude.add(str(parent["offer_plex_key"]))
    return await _write_offer(bot, row, rerolls_remaining - 1, exclude)


async def _handle_roll_accept(bot: discord.Client | None, row: dict) -> tuple[str, str, int | None]:
    parent, error = await _validated_parent_offer(row)
    if error:
        return "failed", error, None
    if _offer_stale(parent):
        return "failed", "that tape went back on the shelf - roll again.", None

    movie_row = await db.run(db.get_plex_movie_by_key, str(parent["offer_plex_key"]))
    if not movie_row:
        return "failed", "that tape isn't on the shelf anymore - roll again.", None
    movie = plex._hydrate_cached_movie(movie_row)

    _, chain = await _roll_chain_exclusions(row)
    rerolls_used = ROLL_MAX_REROLLS - int(parent.get("rerolls_remaining") or 0)

    user_id = str(row["discord_id"])
    result = await rental_module.execute_confirmed_rental(
        bot=bot,
        movie=movie,
        user_id=user_id,
        user_name=await _display_name(bot, user_id),
        rerolls_used=rerolls_used,
        initiated_by="random",
    )
    if not result["ok"]:
        return "failed", str(result["error"]).replace("**", ""), None

    title = f"{movie['title']} ({movie.get('year', '?')})"
    due_at = result["due_at"]
    due_local = f"{due_at.strftime('%A %B')} {due_at.day}"
    message = f"tape's yours - {title} is due back by 9 pm on {due_local}. rolled tapes get better macguffin odds on return."
    if not result["thread_ok"]:
        message += " (the discord thread didn't post - tell the maintainer.)"
    return "done", message, result["rental_id"]


_HANDLERS = {
    "ping": _handle_ping,
    "rent": _handle_rent,
    "return": _handle_return,
    "roll": _handle_roll,
    "roll_accept": _handle_roll_accept,
    "roll_reroll": _handle_roll_reroll,
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
            # "offered" rows were already written wholesale by
            # set_rental_request_offer inside the handler
            if status != "offered":
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


async def listen_for_slips(bot: discord.Client) -> None:
    """Spec 18 M-R-d: LISTEN on the shared Postgres so a freshly filed
    slip wakes the worker in well under a second instead of waiting out
    the 5s poll - the poll stays on as the permanent fallback, so a
    dropped listener degrades to sluggish, never to broken. No-op on
    sqlite dev (no NOTIFY there)."""
    if not config.DATABASE_URL:
        return
    import psycopg

    while True:
        try:
            aconn = await psycopg.AsyncConnection.connect(config.DATABASE_URL, autocommit=True)
            try:
                await aconn.execute(f"LISTEN {NOTIFY_CHANNEL}")
                print(f"[outbox] listening for portal notifies on '{NOTIFY_CHANNEL}'")
                async for _notify in aconn.notifies():
                    await process_pending(bot)
            finally:
                await aconn.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[outbox] listener dropped ({exc.__class__.__name__}: {exc}) - retrying in 30s (poll continues)")
            await asyncio.sleep(30)
