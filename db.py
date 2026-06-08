import re
import sqlite3
import json
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import config


ANNOUNCEMENT_CHANNEL_KEY = "announcement_channel_id"
DAILY_REC_CHANNEL_KEY = "daily_rec_channel_id"
LB_ACTIVITY_CHANNEL_KEY = "lb_activity_channel_id"
LB_ACTIVITY_LAST_RUN_KEY = "lb_activity_last_run_at"
ANNOUNCEMENTS_ENABLED_KEY = "announcements_enabled"
DAILY_REC_ENABLED_KEY = "daily_rec_enabled"
LB_ACTIVITY_ENABLED_KEY = "lb_activity_enabled"
REVIEWS_CHANNEL_KEY = "reviews_channel_id"
RENTAL_REQUEST_CHANNEL_KEY = "rental_request_channel_id"
RENTAL_TAG_KEY = "rental_tag_id"
RECOMMENDATION_TAG_KEY = "recommendation_tag_id"
REVIEW_TAG_KEY = "review_tag_id"
LAST_UPDATE_ANNOUNCED_VERSION_KEY = "last_update_announced_version"
FEED_CHANNEL_KEY = "feed_channel_id"
POSTGRES_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tracked_movies (
    tmdb_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    added_by TEXT NOT NULL,
    added_by_id TEXT,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_snapshots (
    tmdb_id INTEGER NOT NULL,
    provider_name TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (tmdb_id, provider_name)
);

CREATE TABLE IF NOT EXISTS daily_recs (
    tmdb_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    posted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS guess_scores (
    user_id TEXT PRIMARY KEY,
    user_tag TEXT NOT NULL,
    points INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    last_win TEXT
);

CREATE TABLE IF NOT EXISTS announced_movies (
    tmdb_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    first_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS six_scores (
    user_id TEXT PRIMARY KEY,
    user_tag TEXT NOT NULL,
    points INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    last_win TEXT
);

CREATE TABLE IF NOT EXISTS lb_accounts (
    user_id TEXT PRIMARY KEY,
    lb_username TEXT NOT NULL,
    linked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lb_activity_seen (
    entry_key TEXT PRIMARY KEY,
    lb_username TEXT NOT NULL,
    film_title TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    posted_at TEXT
);

CREATE TABLE IF NOT EXISTS watchlist (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    tmdb_id INTEGER,
    title TEXT NOT NULL,
    year INTEGER,
    poster_url TEXT,
    added_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    UNIQUE(user_id, title, year)
);

CREATE TABLE IF NOT EXISTS macguffins (
    macguffin_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    owner_tag TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    acquired_via TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS macguffin_free_claims (
    user_id TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS user_timezones (
    user_id TEXT PRIMARY KEY,
    timezone TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rentals (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    user_name TEXT NOT NULL,
    plex_key TEXT NOT NULL,
    title TEXT NOT NULL,
    year INTEGER,
    poster_url TEXT,
    rented_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    returned_at TEXT,
    thread_id TEXT,
    message_id TEXT,
    rerolls_used INTEGER NOT NULL DEFAULT 0,
    initiated_by TEXT NOT NULL DEFAULT 'command',
    status TEXT NOT NULL DEFAULT 'active',
    rating INTEGER,
    thoughts TEXT,
    recommended INTEGER,
    late_fee_dollars DOUBLE PRECISION NOT NULL DEFAULT 0,
    reminder_sent INTEGER NOT NULL DEFAULT 0,
    overdue_notified INTEGER NOT NULL DEFAULT 0,
    extensions_used INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS plex_library_cache (
    rating_key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    year INTEGER,
    summary TEXT NOT NULL DEFAULT '',
    thumb_path TEXT,
    art_path TEXT,
    duration_minutes INTEGER,
    rating TEXT,
    audience_rating DOUBLE PRECISION,
    added_at TEXT,
    updated_at TEXT,
    genres_json TEXT NOT NULL DEFAULT '[]',
    directors_json TEXT NOT NULL DEFAULT '[]',
    writers_json TEXT NOT NULL DEFAULT '[]',
    actors_json TEXT NOT NULL DEFAULT '[]',
    countries_json TEXT NOT NULL DEFAULT '[]',
    collections_json TEXT NOT NULL DEFAULT '[]',
    cached_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS achievement_earned (
    user_id TEXT NOT NULL,
    achievement_id TEXT NOT NULL,
    user_tag TEXT NOT NULL,
    earned_at TEXT NOT NULL,
    source_type TEXT,
    source_id TEXT,
    PRIMARY KEY (user_id, achievement_id)
);

CREATE TABLE IF NOT EXISTS achievement_display (
    user_id TEXT NOT NULL,
    achievement_id TEXT NOT NULL,
    slot INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, achievement_id),
    UNIQUE (user_id, slot)
);

CREATE TABLE IF NOT EXISTS achievement_roles (
    achievement_id TEXT PRIMARY KEY,
    role_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS achievement_events (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    user_tag TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rentals_user_status
    ON rentals (user_id, status);
CREATE INDEX IF NOT EXISTS idx_rentals_status_due
    ON rentals (status, due_at);
CREATE INDEX IF NOT EXISTS idx_watchlist_user_added
    ON watchlist (user_id, added_at DESC);
CREATE INDEX IF NOT EXISTS idx_lb_activity_username
    ON lb_activity_seen (lb_username, first_seen_at);
CREATE INDEX IF NOT EXISTS idx_plex_library_added
    ON plex_library_cache (added_at);
CREATE INDEX IF NOT EXISTS idx_plex_library_updated
    ON plex_library_cache (updated_at);
CREATE INDEX IF NOT EXISTS idx_achievement_events_user_type
    ON achievement_events (user_id, event_type);
CREATE INDEX IF NOT EXISTS idx_achievement_earned_earned_at
    ON achievement_earned (earned_at DESC);
"""


def _utc_now_iso() -> str:
    """Current UTC time as an ISO-format string. Timezone-aware (yields +00:00 suffix)."""
    return datetime.now(timezone.utc).isoformat()


def _normalize_watchlist_title(title: str) -> str:
    """Normalize enough to avoid obvious duplicate imports without external lookups."""
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def _normalize_rental_search_title(title: str | None) -> str:
    """Compact title text so punctuation/spacing variants still match."""
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())


def _using_postgres() -> bool:
    return bool(config.DATABASE_URL)


def _postgres_query(sql: str) -> str:
    sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO")
    sql = sql.replace("COLLATE NOCASE", "")
    sql = sql.replace(" title LIKE ", " title ILIKE ")
    sql = sql.replace("?", "%s")
    if "INSERT INTO provider_snapshots" in sql and "ON CONFLICT" not in sql:
        sql = f"{sql} ON CONFLICT DO NOTHING"
    if "INSERT INTO announced_movies" in sql and "ON CONFLICT" not in sql:
        sql = f"{sql} ON CONFLICT DO NOTHING"
    if "INSERT INTO macguffin_free_claims" in sql and "ON CONFLICT" not in sql:
        sql = f"{sql} ON CONFLICT DO NOTHING"
    if sql.lstrip().upper().startswith("INSERT INTO RENTALS") and "RETURNING" not in sql.upper():
        sql = f"{sql} RETURNING id"
    return sql


class _PostgresCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self.rowcount = cursor.rowcount
        self.lastrowid = None
        if cursor.description:
            rows = cursor.fetchall()
            self._rows = [dict(row) for row in rows]
            if (
                cursor.description
                and len(cursor.description) == 1
                and cursor.description[0].name == "id"
                and self._rows
            ):
                self.lastrowid = self._rows[0]["id"]
        else:
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _PostgresConnection:
    def __init__(self):
        from psycopg import connect
        from psycopg.rows import dict_row

        self._conn = connect(config.DATABASE_URL, row_factory=dict_row)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        self._conn.close()

    def execute(self, sql: str, params: Iterable[Any] = ()):
        try:
            cursor = self._conn.execute(_postgres_query(sql), tuple(params))
            return _PostgresCursor(cursor)
        except Exception as exc:
            from psycopg import IntegrityError

            if isinstance(exc, IntegrityError):
                self._conn.rollback()
                raise sqlite3.IntegrityError(str(exc)) from exc
            raise

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]):
        rows = list(rows)
        if not rows:
            return _PostgresCursor(self._conn.cursor())
        try:
            cursor = self._conn.cursor()
            cursor.executemany(_postgres_query(sql), rows)
            return _PostgresCursor(cursor)
        except Exception as exc:
            from psycopg import IntegrityError

            if isinstance(exc, IntegrityError):
                self._conn.rollback()
                raise sqlite3.IntegrityError(str(exc)) from exc
            raise

    def executescript(self, sql: str):
        for statement in sql.split(";"):
            statement = statement.strip()
            if statement:
                self.execute(statement)


def _connect() -> sqlite3.Connection | _PostgresConnection:
    if _using_postgres():
        return _PostgresConnection()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _chunks(items: list, size: int = 900):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def init_db() -> None:
    if _using_postgres():
        with _connect() as conn:
            conn.executescript(POSTGRES_SCHEMA_SQL)
        return

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
                added_by_id TEXT,
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

            CREATE TABLE IF NOT EXISTS lb_accounts (
                user_id     TEXT PRIMARY KEY,
                lb_username TEXT NOT NULL,
                linked_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS lb_activity_seen (
                entry_key   TEXT PRIMARY KEY,
                lb_username TEXT NOT NULL,
                film_title  TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                posted_at   TEXT
            );

            CREATE TABLE IF NOT EXISTS watchlist (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT NOT NULL,
                tmdb_id    INTEGER,
                title      TEXT NOT NULL,
                year       INTEGER,
                poster_url TEXT,
                added_at   TEXT NOT NULL,
                source     TEXT NOT NULL DEFAULT 'manual',
                UNIQUE(user_id, title, year)
            );

            CREATE TABLE IF NOT EXISTS macguffins (
                macguffin_id   TEXT PRIMARY KEY,
                owner_id       TEXT NOT NULL,
                owner_tag      TEXT NOT NULL,
                acquired_at    TEXT NOT NULL,
                acquired_via   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS macguffin_free_claims (
                user_id  TEXT PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS user_timezones (
                user_id      TEXT PRIMARY KEY,
                timezone     TEXT NOT NULL,
                updated_at   TEXT NOT NULL
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

            CREATE TABLE IF NOT EXISTS plex_library_cache (
                rating_key       TEXT PRIMARY KEY,
                title            TEXT NOT NULL,
                year             INTEGER,
                summary          TEXT NOT NULL DEFAULT '',
                thumb_path       TEXT,
                art_path         TEXT,
                duration_minutes INTEGER,
                rating           TEXT,
                audience_rating  REAL,
                added_at         TEXT,
                updated_at       TEXT,
                genres_json      TEXT NOT NULL DEFAULT '[]',
                directors_json   TEXT NOT NULL DEFAULT '[]',
                writers_json     TEXT NOT NULL DEFAULT '[]',
                actors_json      TEXT NOT NULL DEFAULT '[]',
                countries_json   TEXT NOT NULL DEFAULT '[]',
                collections_json TEXT NOT NULL DEFAULT '[]',
                cached_at        TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS achievement_earned (
                user_id          TEXT NOT NULL,
                achievement_id   TEXT NOT NULL,
                user_tag         TEXT NOT NULL,
                earned_at        TEXT NOT NULL,
                source_type      TEXT,
                source_id        TEXT,
                PRIMARY KEY (user_id, achievement_id)
            );

            CREATE TABLE IF NOT EXISTS achievement_display (
                user_id          TEXT NOT NULL,
                achievement_id   TEXT NOT NULL,
                slot             INTEGER NOT NULL,
                updated_at       TEXT NOT NULL,
                PRIMARY KEY (user_id, achievement_id),
                UNIQUE (user_id, slot)
            );

            CREATE TABLE IF NOT EXISTS achievement_roles (
                achievement_id   TEXT PRIMARY KEY,
                role_id          TEXT NOT NULL,
                created_at       TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS achievement_events (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id          TEXT NOT NULL,
                user_tag         TEXT NOT NULL,
                event_type       TEXT NOT NULL,
                source_id        TEXT,
                created_at       TEXT NOT NULL
            );
        """)
        _ensure_column(
            conn,
            "tracked_movies",
            "added_by_id",
            "TEXT",
        )
        _ensure_column(
            conn,
            "rentals",
            "extensions_used",
            "INTEGER NOT NULL DEFAULT 0",
        )
        for column in (
            "directors_json",
            "writers_json",
            "actors_json",
            "countries_json",
            "collections_json",
        ):
            _ensure_column(
                conn,
                "plex_library_cache",
                column,
                "TEXT NOT NULL DEFAULT '[]'",
            )
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_rentals_user_status
                ON rentals (user_id, status);
            CREATE INDEX IF NOT EXISTS idx_rentals_status_due
                ON rentals (status, due_at);
            CREATE INDEX IF NOT EXISTS idx_watchlist_user_added
                ON watchlist (user_id, added_at DESC);
            CREATE INDEX IF NOT EXISTS idx_lb_activity_username
                ON lb_activity_seen (lb_username, first_seen_at);
            CREATE INDEX IF NOT EXISTS idx_plex_library_added
                ON plex_library_cache (added_at);
            CREATE INDEX IF NOT EXISTS idx_plex_library_updated
                ON plex_library_cache (updated_at);
            CREATE INDEX IF NOT EXISTS idx_achievement_events_user_type
                ON achievement_events (user_id, event_type);
            CREATE INDEX IF NOT EXISTS idx_achievement_earned_earned_at
                ON achievement_earned (earned_at DESC);
        """)


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    if _using_postgres():
        existing = {
            row["column_name"]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = ?",
                (table,),
            ).fetchall()
        }
        if column not in existing:
            pg_definition = definition.replace("REAL", "DOUBLE PRECISION")
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {pg_definition}")
        return

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


# ---------- user preferences ----------

def get_user_timezone(user_id: str) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT timezone FROM user_timezones WHERE user_id = ?",
            (str(user_id),),
        ).fetchone()
        return row["timezone"] if row else None


def set_user_timezone(user_id: str, timezone_name: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO user_timezones (user_id, timezone, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "timezone = excluded.timezone, updated_at = excluded.updated_at",
            (str(user_id), timezone_name, _utc_now_iso()),
        )


def clear_user_timezone(user_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "DELETE FROM user_timezones WHERE user_id = ?",
            (str(user_id),),
        )


# ---------- Plex library cache ----------

def get_plex_library_cache() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT rating_key, title, year, summary, thumb_path, art_path,
                   duration_minutes, rating, audience_rating, added_at,
                   updated_at, genres_json, directors_json, writers_json,
                   actors_json, countries_json, collections_json
            FROM plex_library_cache
            ORDER BY title COLLATE NOCASE
            """
        ).fetchall()

    movies = []
    for row in rows:
        movie = dict(row)
        for key in ("genres", "directors", "writers", "actors", "countries", "collections"):
            try:
                movie[key] = json.loads(movie.pop(f"{key}_json") or "[]")
            except json.JSONDecodeError:
                movie[key] = []
        movies.append(movie)
    return movies


def upsert_plex_library_cache(movies: Iterable[dict]) -> int:
    return _upsert_plex_library_cache_rows(_plex_library_cache_rows(movies))


def replace_plex_library_cache(movies: Iterable[dict]) -> int:
    rows = _plex_library_cache_rows(movies)
    with _connect() as conn:
        conn.execute("DELETE FROM plex_library_cache")
        _upsert_plex_library_cache_rows(rows, conn=conn)
    return len(rows)


def _plex_library_cache_rows(movies: Iterable[dict]) -> list[tuple]:
    cached_at = _utc_now_iso()
    return [
        (
            str(movie["rating_key"]),
            movie.get("title") or "Unknown",
            movie.get("year"),
            movie.get("summary") or "",
            movie.get("thumb_path"),
            movie.get("art_path"),
            movie.get("duration_minutes"),
            movie.get("rating"),
            movie.get("audience_rating"),
            movie.get("added_at"),
            movie.get("updated_at"),
            json.dumps(movie.get("genres") or []),
            json.dumps(movie.get("directors") or []),
            json.dumps(movie.get("writers") or []),
            json.dumps(movie.get("actors") or []),
            json.dumps(movie.get("countries") or []),
            json.dumps(movie.get("collections") or []),
            cached_at,
        )
        for movie in movies
        if movie.get("rating_key")
    ]


def _upsert_plex_library_cache_rows(
    rows: list[tuple],
    *,
    conn: sqlite3.Connection | None = None,
) -> int:
    if not rows:
        return 0

    def _write(target_conn: sqlite3.Connection) -> None:
        target_conn.executemany(
            """
            INSERT INTO plex_library_cache (
                rating_key, title, year, summary, thumb_path, art_path,
                duration_minutes, rating, audience_rating, added_at, updated_at,
                genres_json, directors_json, writers_json, actors_json,
                countries_json, collections_json, cached_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(rating_key) DO UPDATE SET
                title = excluded.title,
                year = excluded.year,
                summary = excluded.summary,
                thumb_path = excluded.thumb_path,
                art_path = excluded.art_path,
                duration_minutes = excluded.duration_minutes,
                rating = excluded.rating,
                audience_rating = excluded.audience_rating,
                added_at = excluded.added_at,
                updated_at = excluded.updated_at,
                genres_json = excluded.genres_json,
                directors_json = excluded.directors_json,
                writers_json = excluded.writers_json,
                actors_json = excluded.actors_json,
                countries_json = excluded.countries_json,
                collections_json = excluded.collections_json,
                cached_at = excluded.cached_at
            """,
            rows,
        )

    if conn is not None:
        _write(conn)
    else:
        with _connect() as new_conn:
            _write(new_conn)
    return len(rows)


def get_plex_library_cache_watermarks() -> tuple[str | None, str | None]:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT MAX(added_at) AS max_added_at,
                   MAX(updated_at) AS max_updated_at
            FROM plex_library_cache
            """
        ).fetchone()
    if not row:
        return None, None
    return row["max_added_at"], row["max_updated_at"]


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


def get_lb_activity_channel_id() -> int | None:
    raw = get_setting(LB_ACTIVITY_CHANNEL_KEY)
    return int(raw) if raw else None


def set_lb_activity_channel_id(channel_id: int) -> None:
    set_setting(LB_ACTIVITY_CHANNEL_KEY, str(channel_id))


def get_lb_activity_last_run_at() -> str | None:
    return get_setting(LB_ACTIVITY_LAST_RUN_KEY)


def set_lb_activity_last_run_at(run_at: str | None = None) -> None:
    set_setting(LB_ACTIVITY_LAST_RUN_KEY, run_at or _utc_now_iso())


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


def is_lb_activity_enabled() -> bool:
    raw = get_setting(LB_ACTIVITY_ENABLED_KEY)
    if raw is None:
        return False
    return raw == "1"


def set_lb_activity_enabled(enabled: bool) -> None:
    set_setting(LB_ACTIVITY_ENABLED_KEY, "1" if enabled else "0")


def get_reviews_channel_id() -> int | None:
    raw = get_setting(REVIEWS_CHANNEL_KEY)
    return int(raw) if raw else None


def set_reviews_channel_id(channel_id: int) -> None:
    set_setting(REVIEWS_CHANNEL_KEY, str(channel_id))


def get_rental_request_channel_id() -> int | None:
    raw = get_setting(RENTAL_REQUEST_CHANNEL_KEY)
    return int(raw) if raw else None


def set_rental_request_channel_id(channel_id: int) -> None:
    set_setting(RENTAL_REQUEST_CHANNEL_KEY, str(channel_id))


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


def get_review_tag_id() -> int | None:
    raw = get_setting(REVIEW_TAG_KEY)
    return int(raw) if raw else None


def set_review_tag_id(tag_id: int) -> None:
    set_setting(REVIEW_TAG_KEY, str(tag_id))


def get_last_update_announced_version() -> str | None:
    return get_setting(LAST_UPDATE_ANNOUNCED_VERSION_KEY)


def set_last_update_announced_version(bot_version: str) -> None:
    set_setting(LAST_UPDATE_ANNOUNCED_VERSION_KEY, bot_version)


def get_feed_channel_id() -> int | None:
    raw = get_setting(FEED_CHANNEL_KEY)
    return int(raw) if raw else None


def set_feed_channel_id(channel_id: int) -> None:
    set_setting(FEED_CHANNEL_KEY, str(channel_id))


# ---------- tracked_movies ----------

def add_tracked_movie(
    tmdb_id: int,
    title: str,
    added_by: str,
    added_by_id: str | None = None,
) -> bool:
    with _connect() as conn:
        try:
            conn.execute(
                "INSERT INTO tracked_movies (tmdb_id, title, added_by, added_by_id, added_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (tmdb_id, title, added_by, added_by_id, _utc_now_iso()),
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
            "SELECT tmdb_id, title, added_by, added_by_id, added_at "
            "FROM tracked_movies ORDER BY added_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def tracked_movie_count() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM tracked_movies").fetchone()["c"]


def tracked_movie_count_for_user(user_id: str) -> int:
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM tracked_movies WHERE added_by_id = ?",
            (user_id,),
        ).fetchone()["c"]


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


def get_provider_snapshot_map(tmdb_ids: Iterable[int]) -> dict[int, set[str]]:
    """Return seen provider names keyed by TMDB id for a batch of movies."""
    ids = list(dict.fromkeys(tmdb_ids))
    if not ids:
        return {}

    result: dict[int, set[str]] = {}
    with _connect() as conn:
        for chunk in _chunks(ids):
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                "SELECT tmdb_id, provider_name FROM provider_snapshots "
                f"WHERE tmdb_id IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                result.setdefault(row["tmdb_id"], set()).add(row["provider_name"])
    return result


def record_providers_many(records: Iterable[tuple[int, str]]) -> None:
    now = _utc_now_iso()
    rows = [(tmdb_id, provider_name, now) for tmdb_id, provider_name in records]
    if not rows:
        return
    with _connect() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO provider_snapshots "
            "(tmdb_id, provider_name, first_seen_at) VALUES (?, ?, ?)",
            rows,
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


def get_guess_score(user_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT user_id, user_tag, points, wins, last_win "
            "FROM guess_scores WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


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


def get_announced_movie_ids(tmdb_ids: Iterable[int]) -> set[int]:
    ids = list(dict.fromkeys(tmdb_ids))
    if not ids:
        return set()

    announced: set[int] = set()
    with _connect() as conn:
        for chunk in _chunks(ids):
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT tmdb_id FROM announced_movies WHERE tmdb_id IN ({placeholders})",
                chunk,
            ).fetchall()
            announced.update(row["tmdb_id"] for row in rows)
    return announced


def record_announced_movies_many(records: Iterable[tuple[int, str]]) -> None:
    now = _utc_now_iso()
    rows = [(tmdb_id, title, now) for tmdb_id, title in records]
    if not rows:
        return
    with _connect() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO announced_movies (tmdb_id, title, first_seen_at) "
            "VALUES (?, ?, ?)",
            rows,
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


def get_six_score(user_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT user_id, user_tag, points, wins, last_win "
            "FROM six_scores WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


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


def get_active_rentals(user_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM rentals WHERE user_id = ? AND status = 'active' "
            "ORDER BY rented_at ASC",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_active_rental_count_for_user(user_id: str) -> int:
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM rentals WHERE user_id = ? AND status = 'active'",
            (user_id,),
        ).fetchone()["c"]


def get_active_rental_by_id(user_id: str, rental_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM rentals "
            "WHERE id = ? AND user_id = ? AND status = 'active'",
            (rental_id, user_id),
        ).fetchone()
        return dict(row) if row else None


def find_active_rental(user_id: str, query: str) -> list[dict]:
    query = (query or "").strip()
    if not query:
        return []

    if query.isdigit():
        rental = get_active_rental_by_id(user_id, int(query))
        return [rental] if rental else []

    pattern = f"%{query}%"
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM rentals "
            "WHERE user_id = ? AND status = 'active' AND title LIKE ? "
            "ORDER BY rented_at ASC",
            (user_id, pattern),
        ).fetchall()
        matches = [dict(row) for row in rows]
        if matches:
            return matches

        normalized_query = _normalize_rental_search_title(query)
        if not normalized_query:
            return []

        rows = conn.execute(
            "SELECT * FROM rentals "
            "WHERE user_id = ? AND status = 'active' "
            "ORDER BY rented_at ASC",
            (user_id,),
        ).fetchall()
        return [
            dict(row)
            for row in rows
            if normalized_query in _normalize_rental_search_title(row["title"])
        ]


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
    rating: int | None,
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


def mark_rental_returned_unwatched(
    rental_id: int,
    returned_at: str,
    reason: str | None,
    late_fee_dollars: float,
) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE rentals SET status = 'returned_unwatched', returned_at = ?, "
            "rating = NULL, thoughts = ?, recommended = NULL, "
            "late_fee_dollars = ? WHERE id = ?",
            (returned_at, reason, late_fee_dollars, rental_id),
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


def active_rental_count() -> int:
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM rentals WHERE status = 'active'"
        ).fetchone()["c"]


def overdue_active_rental_count() -> int:
    now = _utc_now_iso()
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM rentals WHERE status = 'active' AND due_at < ?",
            (now,),
        ).fetchone()["c"]


def get_reminder_due_rentals() -> list[dict]:
    """Active rentals due within 12 hours that haven't been reminded yet."""
    now = datetime.now(timezone.utc)
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
            "WHERE status IN ('returned', 'returned_unwatched') "
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


def get_all_returned_rental_user_ids() -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT user_id FROM rentals WHERE status = 'returned'"
        ).fetchall()
        return [row["user_id"] for row in rows]


def get_all_achievement_candidate_user_ids() -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT user_id FROM rentals
            UNION SELECT owner_id AS user_id FROM macguffins
            UNION SELECT user_id FROM guess_scores
            UNION SELECT user_id FROM six_scores
            UNION SELECT user_id FROM watchlist
            UNION SELECT added_by_id AS user_id FROM tracked_movies WHERE added_by_id IS NOT NULL
            UNION SELECT user_id FROM lb_accounts
            UNION SELECT user_id FROM achievement_events
            """
        ).fetchall()
        return [row["user_id"] for row in rows if row["user_id"]]


def get_all_active_rentals() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM rentals WHERE status = 'active' ORDER BY rented_at ASC"
        ).fetchall()
        return [dict(row) for row in rows]


# ---------- lb_accounts ----------

def link_lb_account(user_id: str, lb_username: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO lb_accounts (user_id, lb_username, linked_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET lb_username = excluded.lb_username, "
            "linked_at = excluded.linked_at",
            (user_id, lb_username, _utc_now_iso()),
        )


def unlink_lb_account(user_id: str) -> bool:
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM lb_accounts WHERE user_id = ?", (user_id,))
        return cursor.rowcount > 0


def get_lb_username(user_id: str) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT lb_username FROM lb_accounts WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["lb_username"] if row else None


def get_all_lb_accounts() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT user_id, lb_username, linked_at FROM lb_accounts"
        ).fetchall()
        return [dict(row) for row in rows]


def lb_account_count() -> int:
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM lb_accounts").fetchone()["c"]


def has_seen_lb_activity(entry_key: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM lb_activity_seen WHERE entry_key = ?",
            (entry_key,),
        ).fetchone()
        return row is not None


def get_seen_lb_activity_keys(entry_keys: Iterable[str]) -> set[str]:
    keys = list(dict.fromkeys(entry_keys))
    if not keys:
        return set()

    seen: set[str] = set()
    with _connect() as conn:
        for chunk in _chunks(keys):
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT entry_key FROM lb_activity_seen WHERE entry_key IN ({placeholders})",
                chunk,
            ).fetchall()
            seen.update(row["entry_key"] for row in rows)
    return seen


def record_lb_activity_seen(
    entry_key: str,
    lb_username: str,
    film_title: str,
    posted: bool = False,
) -> None:
    posted_at = _utc_now_iso() if posted else None
    with _connect() as conn:
        conn.execute(
            "INSERT INTO lb_activity_seen "
            "(entry_key, lb_username, film_title, first_seen_at, posted_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(entry_key) DO UPDATE SET "
            "posted_at = COALESCE(lb_activity_seen.posted_at, excluded.posted_at)",
            (entry_key, lb_username, film_title, _utc_now_iso(), posted_at),
        )


def record_lb_activity_seen_many(
    records: Iterable[tuple[str, str, str, bool]],
) -> None:
    now = _utc_now_iso()
    rows = [
        (entry_key, lb_username, film_title, now, now if posted else None)
        for entry_key, lb_username, film_title, posted in records
    ]
    if not rows:
        return

    with _connect() as conn:
        conn.executemany(
            "INSERT INTO lb_activity_seen "
            "(entry_key, lb_username, film_title, first_seen_at, posted_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(entry_key) DO UPDATE SET "
            "posted_at = COALESCE(lb_activity_seen.posted_at, excluded.posted_at)",
            rows,
        )


# ---------- watchlist ----------

def watchlist_add(
    user_id: str,
    title: str,
    year: int | None,
    tmdb_id: int | None = None,
    poster_url: str | None = None,
    source: str = "manual",
) -> bool:
    """Add a film to the user's watchlist. Returns True if added, False if already present."""
    with _connect() as conn:
        title_key = _normalize_watchlist_title(title)
        rows = conn.execute(
            "SELECT title, year FROM watchlist WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        for row in rows:
            if row["year"] == year and _normalize_watchlist_title(row["title"]) == title_key:
                return False

        try:
            conn.execute(
                "INSERT INTO watchlist (user_id, tmdb_id, title, year, poster_url, added_at, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, tmdb_id, title, year, poster_url, _utc_now_iso(), source),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def watchlist_remove_by_id(entry_id: int, user_id: str) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM watchlist WHERE id = ? AND user_id = ?", (entry_id, user_id)
        )
        return cursor.rowcount > 0


def watchlist_remove_by_title(user_id: str, title_fragment: str) -> int:
    """Remove entries matching a partial title (case-insensitive). Returns rows deleted."""
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM watchlist WHERE user_id = ? AND title LIKE ?",
            (user_id, f"%{title_fragment}%"),
        )
        return cursor.rowcount


def get_watchlist(user_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM watchlist WHERE user_id = ? ORDER BY added_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_watchlist_count(user_id: str) -> int:
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM watchlist WHERE user_id = ?", (user_id,)
        ).fetchone()["c"]


def watchlist_clear(user_id: str) -> int:
    """Remove all entries from a user's watchlist. Returns rows deleted."""
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM watchlist WHERE user_id = ?", (user_id,))
        return cursor.rowcount


# ---------- macguffins ----------

def get_claimed_macguffin_ids() -> set[str]:
    """Return the set of all macguffin IDs that have been claimed by anyone."""
    with _connect() as conn:
        rows = conn.execute("SELECT macguffin_id FROM macguffins").fetchall()
        return {row["macguffin_id"] for row in rows}


def macguffin_is_claimed(macguffin_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM macguffins WHERE macguffin_id = ?", (macguffin_id,)
        ).fetchone()
        return row is not None


def claim_macguffin(
    macguffin_id: str,
    owner_id: str,
    owner_tag: str,
    acquired_via: str,
) -> None:
    """Insert a new macguffin ownership record."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO macguffins (macguffin_id, owner_id, owner_tag, acquired_at, acquired_via) "
            "VALUES (?, ?, ?, ?, ?)",
            (macguffin_id, owner_id, owner_tag, _utc_now_iso(), acquired_via),
        )


def get_macguffin_inventory(user_id: str) -> list[dict]:
    """Return all macguffins owned by a user, most recently acquired first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT macguffin_id, owner_id, owner_tag, acquired_at, acquired_via "
            "FROM macguffins WHERE owner_id = ? ORDER BY acquired_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_macguffin_record(macguffin_id: str) -> dict | None:
    """Return the ownership record for a macguffin, if claimed."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT macguffin_id, owner_id, owner_tag, acquired_at, acquired_via "
            "FROM macguffins WHERE macguffin_id = ?",
            (macguffin_id,),
        ).fetchone()
        return dict(row) if row else None


def user_owns_macguffin(user_id: str, macguffin_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM macguffins WHERE owner_id = ? AND macguffin_id = ?",
            (user_id, macguffin_id),
        ).fetchone()
        return row is not None


def transfer_macguffin(
    macguffin_id: str,
    new_owner_id: str,
    new_owner_tag: str,
) -> bool:
    """Transfer a macguffin to a new owner. Returns True if the row was updated."""
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE macguffins SET owner_id = ?, owner_tag = ?, acquired_via = 'gift' "
            "WHERE macguffin_id = ?",
            (new_owner_id, new_owner_tag, macguffin_id),
        )
        return cursor.rowcount > 0


def remove_macguffin(macguffin_id: str, owner_id: str | None = None) -> bool:
    """Delete a macguffin ownership record. Optionally require the current owner."""
    with _connect() as conn:
        if owner_id is None:
            cursor = conn.execute(
                "DELETE FROM macguffins WHERE macguffin_id = ?",
                (macguffin_id,),
            )
        else:
            cursor = conn.execute(
                "DELETE FROM macguffins WHERE macguffin_id = ? AND owner_id = ?",
                (macguffin_id, owner_id),
            )
        return cursor.rowcount > 0


def has_used_free_claim(user_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM macguffin_free_claims WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row is not None


def record_free_claim_used(user_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO macguffin_free_claims (user_id) VALUES (?)",
            (user_id,),
        )


# ---------- achievements ----------

def record_achievement_event(
    user_id: str,
    user_tag: str,
    event_type: str,
    source_id: str | None = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO achievement_events "
            "(user_id, user_tag, event_type, source_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, user_tag, event_type, source_id, _utc_now_iso()),
        )


def achievement_event_count(user_id: str, event_type: str) -> int:
    with _connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) AS c FROM achievement_events "
            "WHERE user_id = ? AND event_type = ?",
            (user_id, event_type),
        ).fetchone()["c"]


def add_earned_achievement(
    user_id: str,
    achievement_id: str,
    user_tag: str,
    source_type: str | None = None,
    source_id: str | None = None,
) -> bool:
    with _connect() as conn:
        try:
            conn.execute(
                "INSERT INTO achievement_earned "
                "(user_id, achievement_id, user_tag, earned_at, source_type, source_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, achievement_id, user_tag, _utc_now_iso(), source_type, source_id),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def get_earned_achievement_ids(user_id: str) -> set[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT achievement_id FROM achievement_earned WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return {row["achievement_id"] for row in rows}


def get_earned_achievements(user_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT user_id, achievement_id, user_tag, earned_at, source_type, source_id "
            "FROM achievement_earned WHERE user_id = ? ORDER BY earned_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_recent_achievement_unlocks(limit: int = 10) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT user_id, achievement_id, user_tag, earned_at, source_type, source_id "
            "FROM achievement_earned ORDER BY earned_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_achievement_counts_by_user(limit: int = 10) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT user_id, user_tag, COUNT(*) AS total, MAX(earned_at) AS last_earned "
            "FROM achievement_earned GROUP BY user_id "
            "ORDER BY total DESC, last_earned ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_achievement_rarity_counts() -> dict[str, int]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT achievement_id, COUNT(*) AS total "
            "FROM achievement_earned GROUP BY achievement_id"
        ).fetchall()
        return {row["achievement_id"]: row["total"] for row in rows}


def get_displayed_achievements(user_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT achievement_id, slot, updated_at FROM achievement_display "
            "WHERE user_id = ? ORDER BY slot ASC",
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def set_displayed_achievements(user_id: str, achievement_ids: list[str]) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM achievement_display WHERE user_id = ?", (user_id,))
        rows = [
            (user_id, achievement_id, index + 1, _utc_now_iso())
            for index, achievement_id in enumerate(achievement_ids[:3])
        ]
        if rows:
            conn.executemany(
                "INSERT INTO achievement_display "
                "(user_id, achievement_id, slot, updated_at) VALUES (?, ?, ?, ?)",
                rows,
            )


def get_achievement_role_id(achievement_id: str) -> int | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT role_id FROM achievement_roles WHERE achievement_id = ?",
            (achievement_id,),
        ).fetchone()
        return int(row["role_id"]) if row else None


def set_achievement_role_id(achievement_id: str, role_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO achievement_roles (achievement_id, role_id, created_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(achievement_id) DO UPDATE SET role_id = excluded.role_id",
            (achievement_id, str(role_id), _utc_now_iso()),
        )


def get_all_achievement_role_ids() -> set[int]:
    with _connect() as conn:
        rows = conn.execute("SELECT role_id FROM achievement_roles").fetchall()
        return {int(row["role_id"]) for row in rows}
