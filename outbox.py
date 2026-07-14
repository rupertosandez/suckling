"""
Portal outbox worker (sucklingweb spec 18, M-R-a).

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

M-R-a ships the 'ping' action only (the contract proof). rent/return
arrive at M-R-b/c, the roll protocol at M-R-d.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import db

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


def _handle_ping(row: dict) -> tuple[str, str]:
    """Returns (status, result_message)."""
    return "done", "pong. the clerk is in."


_HANDLERS = {
    "ping": _handle_ping,
}


def process_pending_sync() -> dict:
    """One worker tick, synchronous (call via db.run/asyncio.to_thread).
    Claims each pending slip individually (UPDATE guarded on
    status='pending') so a restart mid-batch can't double-execute."""
    global _table_missing_logged
    try:
        rows = db.get_pending_rental_requests()
    except Exception as exc:
        # The web app owns this table's migration; before it has run there
        # is nothing to consume. Log once, not every 5 seconds.
        if not _table_missing_logged:
            print(f"[outbox] web_rental_requests unavailable ({exc.__class__.__name__}) - waiting for the portal migration")
            _table_missing_logged = True
        return {"processed": 0, "skipped": 0}
    _table_missing_logged = False

    processed = 0
    skipped = 0
    for row in rows:
        request_id = int(row["id"])
        if _too_old(row):
            db.complete_rental_request(
                request_id, "expired",
                "the clerk never came back - try again later, or rent in discord.",
            )
            skipped += 1
            continue
        if not db.claim_rental_request(request_id):
            continue  # portal stamped it expired between fetch and claim
        handler = _HANDLERS.get(str(row["action"]))
        if handler is None:
            db.complete_rental_request(
                request_id, "failed",
                "the clerk doesn't know how to handle that request yet.",
            )
            skipped += 1
            continue
        try:
            status, message = handler(row)
            db.complete_rental_request(request_id, status, message)
            processed += 1
        except Exception as exc:
            print(f"[outbox] request {request_id} ({row['action']}) failed: {exc.__class__.__name__}: {exc}")
            db.complete_rental_request(
                request_id, "failed",
                "something went wrong behind the counter - try again, or rent in discord.",
            )
            skipped += 1
    return {"processed": processed, "skipped": skipped}


async def process_pending() -> None:
    """Scheduler entry point - runs the tick off the event loop (the P0
    lesson: no inline db work on the gateway loop)."""
    result = await db.run(process_pending_sync)
    if result["processed"] or result["skipped"]:
        print(f"[outbox] tick: {result['processed']} processed, {result['skipped']} skipped/expired")
