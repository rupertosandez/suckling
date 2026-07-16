# Suckling Project Stack

An overview of the repos that make up the "suckling" / Return by 9 project, written for anyone joining the project.

## Overview

| Repo | Local path | Role | Hosting |
| --- | --- | --- | --- |
| `sucklingbot` | `D:\git\Bots\sucklingbot` | The Discord bot itself | Maintainer's home PC (Windows tray launcher) |
| `sucklingweb` | `D:\git\Sites\sucklingweb` | Member portal webapp | Render (FastAPI service) |
| `sucklingsite` | `D:\git\Sites\sucklingsite` | Public docs minisite | GitHub Pages (static Jekyll) |

GitHub: https://github.com/rupertosandez/suckling. Maintainer: rupertosandez.

---

## 1. sucklingbot - the Discord bot

The core product. A Discord bot for the "Return by 9" movie community: movie discovery, a rental system for the community Plex library, watch tracking, watchlists, games, and achievements.

- **Language/runtime:** Python 3.10
- **Framework:** `discord.py` (slash commands, message content intent)
- **Integrations:** TMDB (movie data), Plex via `plexapi` (the RB9 library), Letterboxd (scraping), Tautulli (cleanup)
- **Persistence:** SQLite by default (`data/moviebot.db`); Postgres via `psycopg` with a shared connection pool when `DATABASE_URL` is set. Production runs on Postgres.
- **Scheduling:** APScheduler (`AsyncIOScheduler`) for recurring jobs (rental expiry, Letterboxd polling, cleanup)
- **Portal integration:** an outbox worker consumes request rows written by the webapp (rentals, returns, watchlist changes) and applies them through the bot's own logic. Postgres LISTEN/NOTIFY wakes the worker so portal actions apply quickly.
- **Packaging:** PyInstaller (`Suckling.spec`)
- **Supervision:** Windows tray launcher (`launcher/`, `launcher.py`) that supervises `bot.py`
- **Feature areas (`cogs/`):** discovery, rb9, rentals, tracking, watchlist, letterboxd, games, macguffins, achievements, admin, meta

Required env: `DISCORD_TOKEN`, `TMDB_API_KEY`, `GUILD_ID`. Optional: `DATABASE_URL`, `PLEX_TOKEN`, `PLEX_LIBRARY`, `BOT_TIMEZONE`, `TAUTULLI_*`, and others (see `config.py`).

Sanity checks live in `scripts/`: `prerelease_check.py` (byte-compile plus Postgres dialect smoke test) is the standard pre-release gate.

---

## 2. sucklingweb - the member portal (Render)

A logged-in webapp where members sign in with Discord and use the bot's features from the browser: browse films, rent and return, manage their watchlist, log watches, and view their stats.

- **Language/runtime:** Python
- **Framework:** FastAPI served by `uvicorn`, Jinja2 templates, `itsdangerous` sessions, `httpx` for outbound HTTP
- **Auth:** Discord OAuth (`/auth/discord/callback`), scoped to the RB9 guild. Admin routes are gated by `ADMIN_DISCORD_IDS`.
- **Database:** the same Postgres database the bot uses, accessed through a `psycopg` connection pool. SQLAlchemy plus Alembic manage the schema for the portal's own tables (all prefixed `web_`). Falls back to the bot's SQLite DB at `BOT_DB_PATH` for local development.
- **Background jobs:** APScheduler inside the app (film cache refresh from Plex/TMDB, Letterboxd sync)
- **Write path:** the portal never mutates bot-owned tables directly. Member actions are written to `web_*_requests` outbox tables, and the bot's worker picks them up and applies them. This keeps the bot the single owner of game and rental logic.
- **Main surfaces:** film catalog and detail pages (Plex library plus on-demand TMDB pages for films outside it), rentals, watchlist, watch logging with Letterboxd import/sync, macguffins and achievements, community collections, an admin panel (the Admin Dashboard)
- **Hosting:** Render web service. Build with `pip install -r requirements.txt`, start with `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Uses Render's internal Postgres and a persistent disk at `/var/data`.

Key env: `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_GUILD_ID`, `SESSION_SECRET`, `DATABASE_URL` (or `BOT_DB_PATH`), `BASE_URL`, `TMDB_API_KEY`, `PLEX_MACHINE_ID`, `ADMIN_DISCORD_IDS`.

The `relay/` directory holds the in-progress "watch together" work: a planned VPS relay (Docker Compose, Caddy/nginx) that would let members stream watch parties from the Plex server. This is at the spike stage, not deployed.

Design docs, feature specs, and schema notes live in `spec/`, `design/`, and `SCHEMA-NOTES.md` inside the repo.

---

## 3. sucklingsite - GitHub Pages docs site

A static public minisite documenting the bot. No backend.

- **Generator:** Jekyll with `kramdown` markdown, pretty permalinks (`_config.yml`)
- **Hosting:** GitHub Pages, deployed from the `main` branch root
- **Content:** `index.md` (homepage), `COMMANDS.md` (public command reference), `CHANGELOG.md`, `macguffins.md`, `achievements.md`
- **Theme:** custom `_layouts/default.html` shell, `assets/css/style.css` dark theme, Fraunces headline font loaded from Google Fonts

This site is updated as part of the bot's release workflow: its `CHANGELOG.md` and `COMMANDS.md` mirror the bot repo, written in member-facing casual lowercase copy.

---

## How they fit together

- **The bot** is the source of truth. It owns all game, rental, and tracking logic, writes member activity to the shared database, and owns the catalog JSON (macguffins, sets, achievements).
- **sucklingweb** reads that same Postgres database to render the portal, and writes member actions to outbox tables that the bot consumes. Portal and bot never both mutate the same state.
- **sucklingsite** is static documentation, refreshed on each bot release.
- Postgres (hosted on Render) is the shared production datastore for the bot and the portal. SQLite remains the local development and fallback path for both.
