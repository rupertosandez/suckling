from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import db


SMOKE_USER_ID = "__smoke_user__"
SMOKE_TMDB_ID = 999999001
SMOKE_PLEX_KEY = "__smoke_plex_key__"
SMOKE_MACGUFFIN_ID = "__smoke_macguffin__"
SMOKE_ACHIEVEMENT_ID = "__smoke_achievement__"


def cleanup() -> None:
    with db._connect() as conn:
        conn.execute("DELETE FROM achievement_display WHERE user_id = ?", (SMOKE_USER_ID,))
        conn.execute("DELETE FROM achievement_earned WHERE user_id = ?", (SMOKE_USER_ID,))
        conn.execute("DELETE FROM achievement_events WHERE user_id = ?", (SMOKE_USER_ID,))
        conn.execute("DELETE FROM macguffins WHERE macguffin_id = ?", (SMOKE_MACGUFFIN_ID,))
        conn.execute("DELETE FROM macguffin_free_claims WHERE user_id = ?", (SMOKE_USER_ID,))
        conn.execute("DELETE FROM rentals WHERE user_id = ?", (SMOKE_USER_ID,))
        conn.execute("DELETE FROM watchlist WHERE user_id = ?", (SMOKE_USER_ID,))
        conn.execute("DELETE FROM announced_movies WHERE tmdb_id = ?", (SMOKE_TMDB_ID,))
        conn.execute("DELETE FROM provider_snapshots WHERE tmdb_id = ?", (SMOKE_TMDB_ID,))
        conn.execute("DELETE FROM tracked_movies WHERE tmdb_id = ?", (SMOKE_TMDB_ID,))
        conn.execute("DELETE FROM lb_accounts WHERE user_id = ?", (SMOKE_USER_ID,))
        conn.execute("DELETE FROM user_timezones WHERE user_id = ?", (SMOKE_USER_ID,))


def main() -> None:
    if not os.getenv("DATABASE_URL"):
        raise SystemExit("DATABASE_URL is required for the Postgres smoke test")

    cleanup()
    db.init_db()

    db.set_setting("smoke_postgres", "ok")
    assert db.get_setting("smoke_postgres") == "ok"

    assert db.add_tracked_movie(SMOKE_TMDB_ID, "Smoke Movie", SMOKE_USER_ID, SMOKE_USER_ID) is True
    assert db.add_tracked_movie(SMOKE_TMDB_ID, "Smoke Movie", SMOKE_USER_ID, SMOKE_USER_ID) is False
    assert db.has_seen_provider(SMOKE_TMDB_ID, "Smoke Provider") is False
    db.record_provider(SMOKE_TMDB_ID, "Smoke Provider")
    assert db.has_seen_provider(SMOKE_TMDB_ID, "Smoke Provider") is True
    db.record_announced_movie(SMOKE_TMDB_ID, "Smoke Movie")
    assert db.has_been_announced(SMOKE_TMDB_ID) is True

    assert db.watchlist_add(SMOKE_USER_ID, "Smoke Movie", 2026, SMOKE_TMDB_ID, source="manual") is True
    assert db.watchlist_add(SMOKE_USER_ID, "Smoke Movie", 2026, SMOKE_TMDB_ID, source="manual") is False
    assert db.get_watchlist_count(SMOKE_USER_ID) == 1

    rental_id = db.create_rental(
        SMOKE_USER_ID,
        "smoke user",
        SMOKE_PLEX_KEY,
        "Smoke Rental",
        2026,
        None,
        "2026-06-07T00:00:00+00:00",
        "2026-06-08T00:00:00+00:00",
    )
    assert isinstance(rental_id, int)
    assert db.get_rental_by_id(rental_id)
    assert db.extend_rental_due_at(rental_id, "2026-06-09T00:00:00+00:00") is True
    db.mark_rental_returned(rental_id, "2026-06-08T00:00:00+00:00", 9, "solid", True, 0)

    db.claim_macguffin(SMOKE_MACGUFFIN_ID, SMOKE_USER_ID, "smoke user", "smoke")
    assert db.user_owns_macguffin(SMOKE_USER_ID, SMOKE_MACGUFFIN_ID)
    db.record_free_claim_used(SMOKE_USER_ID)
    assert db.has_used_free_claim(SMOKE_USER_ID)

    assert db.add_earned_achievement(SMOKE_USER_ID, SMOKE_ACHIEVEMENT_ID, "smoke user") is True
    assert db.add_earned_achievement(SMOKE_USER_ID, SMOKE_ACHIEVEMENT_ID, "smoke user") is False
    db.set_displayed_achievements(SMOKE_USER_ID, [SMOKE_ACHIEVEMENT_ID])
    assert db.get_displayed_achievements(SMOKE_USER_ID)

    cleanup()
    print("postgres smoke ok")


if __name__ == "__main__":
    main()
