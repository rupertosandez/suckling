"""Unit tests for the SQLite/Postgres dialect helpers in db.py.

These are pure-function tests (no live database, SQLite or Postgres,
required) that pin down the dialect-parity guarantees db.py centralizes:
case-insensitive search/order, insert-returning-id, and placeholder
conversion that must not corrupt a literal `?` inside a string literal.
They toggle `config.DATABASE_URL` to flip `db._using_postgres()` since that
flag is what every dialect helper keys off.

Complements (does not replace) scripts/smoke_postgres_db.py, which exercises
the same paths against a real Postgres database.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import db


def _as_postgres(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://example/db")


def _as_sqlite(monkeypatch):
    monkeypatch.setattr(config, "DATABASE_URL", None)


# ---------- _like_ci ----------

def test_like_ci_sqlite_uses_like(monkeypatch):
    _as_sqlite(monkeypatch)
    assert db._like_ci() == "LIKE"


def test_like_ci_postgres_uses_ilike(monkeypatch):
    _as_postgres(monkeypatch)
    assert db._like_ci() == "ILIKE"


# ---------- _order_ci ----------

def test_order_ci_sqlite_uses_collate_nocase(monkeypatch):
    _as_sqlite(monkeypatch)
    assert db._order_ci("title") == "title COLLATE NOCASE"


def test_order_ci_postgres_uses_lower(monkeypatch):
    _as_postgres(monkeypatch)
    assert db._order_ci("title") == "LOWER(title)"


# ---------- _returning_id ----------

def test_returning_id_sqlite_is_noop(monkeypatch):
    _as_sqlite(monkeypatch)
    assert db._returning_id("INSERT INTO rentals (a) VALUES (?)") == "INSERT INTO rentals (a) VALUES (?)"


def test_returning_id_postgres_appends_returning(monkeypatch):
    _as_postgres(monkeypatch)
    assert (
        db._returning_id("INSERT INTO rentals (a) VALUES (?)")
        == "INSERT INTO rentals (a) VALUES (?) RETURNING id"
    )


# ---------- _convert_placeholders ----------

def test_convert_placeholders_rewrites_bind_markers():
    assert db._convert_placeholders("SELECT * FROM t WHERE a = ? AND b = ?") == (
        "SELECT * FROM t WHERE a = %s AND b = %s"
    )


def test_convert_placeholders_skips_literal_question_mark_in_string():
    sql = "SELECT * FROM t WHERE msg = 'are you sure?' AND id = ?"
    assert db._convert_placeholders(sql) == (
        "SELECT * FROM t WHERE msg = 'are you sure?' AND id = %s"
    )


def test_convert_placeholders_handles_escaped_quote_in_string():
    sql = "SELECT * FROM t WHERE msg = 'it''s a ? test' AND id = ?"
    assert db._convert_placeholders(sql) == (
        "SELECT * FROM t WHERE msg = 'it''s a ? test' AND id = %s"
    )


# ---------- _postgres_query ----------

def test_postgres_query_translates_insert_or_ignore():
    sql = "INSERT OR IGNORE INTO watchlist (user_id, tmdb_id) VALUES (?, ?)"
    result = db._postgres_query(sql)
    assert result == "INSERT INTO watchlist (user_id, tmdb_id) VALUES (%s, %s) ON CONFLICT DO NOTHING"


def test_postgres_query_leaves_explicit_on_conflict_alone():
    sql = "INSERT OR IGNORE INTO t (a) VALUES (?) ON CONFLICT (a) DO UPDATE SET a = ?"
    result = db._postgres_query(sql)
    assert result.startswith("INSERT INTO t (a) VALUES (%s) ON CONFLICT (a) DO UPDATE SET a = %s")
    assert "ON CONFLICT DO NOTHING" not in result


def test_postgres_query_plain_select_only_converts_placeholders():
    sql = "SELECT * FROM t WHERE a = ?"
    assert db._postgres_query(sql) == "SELECT * FROM t WHERE a = %s"
