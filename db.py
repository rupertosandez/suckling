import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import config


ANNOUNCEMENT_CHANNEL_KEY = "announcement_channel_id"
DAILY_REC_CHANNEL_KEY = "daily_rec_channel_id"
ANNOUNCEMENTS_ENABLED_KEY = "announcements_enabled"
DAILY_REC_ENABLED_KEY = "daily_rec_enabled"


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
        """)


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
            # fromisoformat handles both naive (legacy) and aware (new) strings.
            # .timestamp() works on both; naive values are interpreted as local
            # time, which is close enough for the 30-day exclusion window.
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