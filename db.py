import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import config


ANNOUNCEMENT_CHANNEL_KEY = "announcement_channel_id"
DAILY_REC_CHANNEL_KEY = "daily_rec_channel_id"
ANNOUNCEMENTS_ENABLED_KEY = "announcements_enabled"
DAILY_REC_ENABLED_KEY = "daily_rec_enabled"
REVIEWS_CHANNEL_KEY = "reviews_channel_id"
RENTAL_TAG_KEY = "rental_tag_id"
RECOMMENDATION_TAG_KEY = "recommendation_tag_id"
LAST_UPDATE_ANNOUNCED_VERSION_KEY = "last_update_announced_version"


def _utc_now_iso() -> str:
    """Current UTC time as an ISO-format string. Timezone-aware (yields +00:00 suffix)."""
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)

    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tracked_movies (
                tmdb_id    INTEGER PRIMARY KEY,
                title      TEXT NOT NULL,
                added_by   TEXT NOT NULL,
                added_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS provider_snapshots (
                tmdb_id        INTEGER NOT NULL,
                provider_name  TEXT NOT NULL,
                first_seen_at  TEXT NOT NULL,
                PRIMARY KEY (tmdb_id, provider_name)
            );

            CREATE TABLE IF NOT EXISTS daily_recs (
                tmdb_id     INTEGER PRIMARY KEY,
                title       TEXT NOT NULL,
                posted_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS guess_scores (
                user_id    TEXT PRIMARY KEY,
                user_tag   TEXT NOT NULL,
                points     INTEGER NOT NULL DEFAULT 0,
                wins       INTEGER NOT NULL DEFAULT 0,
                last_win   TEXT
            );

            CREATE TABLE IF NOT EXISTS announced_movies (
                tmdb_id        INTEGER PRIMARY KEY,
                title          TEXT NOT NULL,
                first_seen_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS six_scores (
                user_id    TEXT PRIMARY KEY,
                user_tag   TEXT NOT NULL,
                points     INTEGER NOT NULL DEFAULT 0,
                wins       INTEGER NOT NULL DEFAULT 0,
                last_win   TEXT
            );

            CREATE TABLE IF NOT EXISTS rentals (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id          TEXT NOT NULL,
                user_name        TEXT NOT NULL,
                plex_key         TEXT NOT NULL,
                title            TEXT NOT NULL,
                year             INTEGER,
                poster_url       TEXT,
                rented_at        TEXT NOT NULL,
                due_at           TEXT NOT NULL,
                returned_at      TEXT,
                thread_id        TEXT,
                message_id       TEXT,
                rerolls_used     INTEGER NOT NULL DEFAULT 0,
                initiated_by     TEXT NOT NULL DEFAULT 'command',
                status           TEXT NOT NULL DEFAULT 'active',
                rating           INTEGER,
                thoughts         TEXT,
                recommended      INTEGER,
                late_fee_dollars REAL NOT NULL DEFAULT 0,
                reminder_sent    INTEGER NOT NULL DEFAULT 0,
                overdue_notified INTEGER NOT NULL DEFAULT 0,
                extensions_used  INTEGER NOT NULL DEFAULT 0
            );
        """)
        _ensure_column(
            conn,
            "rentals",
            "extensions_used",
            "INTEGER NOT NULL DEFAULT 0",
        )


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    existing = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


# ---------- config ----------

def get_setting(key: str) -> str | None:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO config (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_announcement_channel_id() -> int | None:
    raw = get_setting(ANNOUNCEMENT_CHANNEL_KEY)
    return int(raw) if raw else None


def set_announcement_channel_id(channel_id: int) -> None:
    set_setting(ANNOUNCEMENT_CHANNEL_KEY, str(channel_id))


def get_daily_rec_channel_id() -> int | None:
    raw = get_setting(DAILY_REC_CHANNEL_KEY)
    return int(raw) if raw else None


def set_daily_rec_channel_id(channel_id: int) -> None:
    set_setting(DAILY_REC_CHANNEL_KEY, str(channel_id))


def is_announcements_enabled() -> bool:
    raw = get_setting(ANNOUNCEMENTS_ENABLED_KEY)
    if raw is None:
        return True
    return raw == "1"


def set_announcements_enabled(enabled: bool) -> None:
    set_setting(ANNOUNCEMENTS_ENABLED_KEY, "1" if enabled else "0")


def is_daily_rec_enabled() -> bool:
    raw = get_setting(DAILY_REC_ENABLED_KEY)
    if raw is None:
        return True
    return raw == "1"


def set_daily_rec_enabled(enabled: bool) -> None:
    set_setting(DAILY_REC_ENABLED_KEY, "1" if enabled else "0")


def get_reviews_channel_id() -> int | None:
    raw = get_setting(REVIEWS_CHANNEL_KEY)
    return int(raw) if raw else None


def set_reviews_channel_id(channel_id: int) -> None:
    set_setting(REVIEWS_CHANNEL_KEY, str(channel_id))


def get_rental_tag_id() -> int | None:
    raw = get_setting(RENTAL_TAG_KEY)
    return int(raw) if raw else None


def set_rental_tag_id(tag_id: int) -> None:
    set_setting(RENTAL_TAG_KEY, str(tag_id))


def get_recommendation_tag_id() -> int | None:
    raw = get_setting(RECOMMENDATION_TAG_KEY)
    return int(raw) if raw else None


def set_recommendation_tag_id(tag_id: int) -> None:
    set_setting(RECOMMENDATION_TAG_KEY, str(tag_id))


def get_last_update_announced_version() -> str | None:
    return get_setting(LAST_UPDATE_ANNOUNCED_VERSION_KEY)


def set_last_update_announced_version(bot_version: str) -> None:
    set_setting(LAST_UPDATE_ANNOUNCED_VERSION_KEY, bot_version)


# ---------- tracked_movies ----------

def add_tracked_movie(tmdb_id: int, title: str, added_by: str) -> bool:
    with _connect() as conn:
        try:
            conn.execute(
                "INSERT INTO tracked_movies (tmdb_id, title, added_by, added_at) "
                "VALUES (?, ?, ?, ?)",
                (tmdb_id, title, added_by, _utc_now_iso()),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def remove_tracked_movie(tmdb_id: int) -> bool:
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM tracked_movies WHERE tmdb_id = ?", (tmdb_id,))
        return cursor.rowcount > 0


def list_tracked_movies() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT tmdb_id, title, added_by, added_at FROM tracked_movies ORDER BY added_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]


# ---------- provider_snapshots ----------

def has_seen_provider(tmdb_id: int, provider_name: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM provider_snapshots WHERE tmdb_id = ? AND provider_name = ?",
            (tmdb_id, provider_name),
        ).fetchone()
        return row is not None


def record_provider(tmdb_id: int, provider_name: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO provider_snapshots (tmdb_id, provider_name, first_seen_at) "
            "VALUES (?, ?, ?)",
            (tmdb_id, provider_name, _utc_now_iso()),
        )


# ---------- daily_recs ----------

def record_daily_rec(tmdb_id: int, title: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO daily_recs (tmdb_id, title, posted_at) VALUES (?, ?, ?) "
            "ON CONFLICT(tmdb_id) DO UPDATE SET posted_at = excluded.posted_at, title = excluded.title",
            (tmdb_id, title, _utc_now_iso()),
        )


def recent_rec_ids(within_days: int = 30) -> set[int]:
    cutoff = datetime.now(timezone.utc).timestamp() - (within_days * 86400)
    with _connect() as conn:
        rows = conn.execute("SELECT tmdb_id, posted_at FROM daily_recs").fetchall()
    recent = set()
    for row in rows:
        try:
            posted_ts = datetime.fromisoformat(row["posted_at"]).timestamp()
            if posted_ts >= cutoff:
                recent.add(row["tmdb_id"])
        except ValueError:
            continue
    return recent


# ---------- guess_scores ----------

def increment_guess_score(user_id: str, user_tag: str, points: int = 1) -> int:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO guess_scores (user_id, user_tag, points, wins, last_win) "
            "VALUES (?, ?, ?, 1, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "  points = points + ?, "
            "  wins = wins + 1, "
            "  user_tag = excluded.user_tag, "
            "  last_win = excluded.last_win",
            (user_id, user_tag, points, _utc_now_iso(), points),
        )
        row = conn.execute(
            "SELECT points FROM guess_scores WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["points"] if row else points


def get_leaderboard(limit: int = 10) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT user_id, user_tag, points, wins, last_win "
            "FROM guess_scores ORDER BY points DESC, last_win ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


# ---------- announced_movies ----------

def has_been_announced(tmdb_id: int) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM announced_movies WHERE tmdb_id = ?", (tmdb_id,)
        ).fetchone()
        return row is not None


def record_announced_movie(tmdb_id: int, title: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO announced_movies (tmdb_id, title, first_seen_at) "
            "VALUES (?, ?, ?)",
            (tmdb_id, title, _utc_now_iso()),
        )


def announced_count() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM announced_movies").fetchone()["c"]


# ---------- six_scores ----------

def increment_six_score(user_id: str, user_tag: str, points: int = 1) -> int:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO six_scores (user_id, user_tag, points, wins, last_win) "
            "VALUES (?, ?, ?, 1, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "  points = points + ?, "
            "  wins = wins + 1, "
            "  user_tag = excluded.user_tag, "
            "  last_win = excluded.last_win",
            (user_id, user_tag, points, _utc_now_iso(), points),
        )
        row = conn.execute(
            "SELECT points FROM six_scores WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["points"] if row else points


def get_six_leaderboard(limit: int = 10) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT user_id, user_tag, points, wins, last_win "
            "FROM six_scores ORDER BY points DESC, last_win ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


# ---------- rentals ----------

def create_rental(
    user_id: str,
    user_name: str,
    plex_key: str,
    title: str,
    year: int | None,
    poster_url: str | None,
    rented_at: str,
    due_at: str,
    rerolls_used: int = 0,
    initiated_by: str = "command",
) -> int:
    """Insert a new rental record. Returns the new rental id."""
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO rentals "
            "(user_id, user_name, plex_key, title, year, poster_url, "
            " rented_at, due_at, rerolls_used, initiated_by, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')",
            (user_id, user_name, plex_key, title, year, poster_url,
             rented_at, due_at, rerolls_used, initiated_by),
        )
        return cursor.lastrowid


def get_active_rental(user_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM rentals WHERE user_id = ? AND status = 'active'",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


def get_rental_by_id(rental_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM rentals WHERE id = ?", (rental_id,)
        ).fetchone()
        return dict(row) if row else None


def set_rental_thread(rental_id: int, thread_id: int, message_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE rentals SET thread_id = ?, message_id = ? WHERE id = ?",
            (str(thread_id), str(message_id), rental_id),
        )


def mark_rental_returned(
    rental_id: int,
    returned_at: str,
    rating: int,
    thoughts: str | None,
    recommended: bool,
    late_fee_dollars: float,
) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE rentals SET status = 'returned', returned_at = ?, rating = ?, "
            "thoughts = ?, recommended = ?, late_fee_dollars = ? WHERE id = ?",
            (returned_at, rating, thoughts, 1 if recommended else 0,
             late_fee_dollars, rental_id),
        )


def extend_rental_due_at(
    rental_id: int,
    due_at: str,
    max_extensions: int = 1,
) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE rentals SET due_at = ?, extensions_used = extensions_used + 1, "
            "reminder_sent = 0, overdue_notified = 0 "
            "WHERE id = ? AND status = 'active' AND extensions_used < ?",
            (due_at, rental_id, max_extensions),
        )
        return cursor.rowcount > 0


def cancel_rental_by_id(rental_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE rentals SET status = 'cancelled' WHERE id = ?",
            (rental_id,),
        )


def get_user_rented_plex_keys(user_id: str) -> set[str]:
    """All plex keys ever rented by this user (active, returned, or cancelled)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT plex_key FROM rentals WHERE user_id = ?", (user_id,)
        ).fetchall()
        return {row["plex_key"] for row in rows}


def get_overdue_active_rentals() -> list[dict]:
    """Active rentals that are past due and haven't been DM-notified yet."""
    now = _utc_now_iso()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM rentals "
            "WHERE status = 'active' AND due_at < ? AND overdue_notified = 0",
            (now,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_reminder_due_rentals() -> list[dict]:
    """Active rentals due within 12 hours that haven't been reminded yet."""
    now = datetime.now(timezone.utc)
    window_end = now.replace(microsecond=0).isoformat().replace("+00:00", "") 
    # Build the 12h-from-now boundary as an ISO string
    from datetime import timedelta
    cutoff = (now + timedelta(hours=12)).isoformat()
    now_iso = now.isoformat()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM rentals "
            "WHERE status = 'active' AND due_at > ? AND due_at <= ? AND reminder_sent = 0",
            (now_iso, cutoff),
        ).fetchall()
        return [dict(row) for row in rows]


def mark_overdue_notified(rental_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE rentals SET overdue_notified = 1 WHERE id = ?", (rental_id,)
        )


def mark_reminder_sent(rental_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE rentals SET reminder_sent = 1 WHERE id = ?", (rental_id,)
        )


def get_late_fees_leaderboard(limit: int = 10) -> list[dict]:
    """Total late fees accumulated per user, descending."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT user_id, user_name, "
            "  SUM(late_fee_dollars) AS total_fees, "
            "  COUNT(*) AS total_rentals, "
            "  SUM(CASE WHEN late_fee_dollars > 0 THEN 1 ELSE 0 END) AS late_count "
            "FROM rentals "
            "WHERE status IN ('returned', 'active') "
            "GROUP BY user_id "
            "HAVING total_fees > 0 "
            "ORDER BY total_fees DESC "
            "LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_user_rental_history(user_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM rentals WHERE user_id = ? ORDER BY rented_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_all_active_rentals() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM rentals WHERE status = 'active' ORDER BY rented_at ASC"
        ).fetchall()
        return [dict(row) for row in rows]
