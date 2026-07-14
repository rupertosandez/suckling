# Suckling Project Stack

A rundown of the three repos that make up the "suckling" / Return by 9 project.

## Overview

| Repo | Local path | Role | Hosting |
| --- | --- | --- | --- |
| `sucklingbot` | `D:\git\Bots\sucklingbot` | The Discord bot itself | Maintainer's home PC (Windows tray launcher) |
| `sucklingsite` | `D:\git\Sites\sucklingsite` | Public docs minisite | GitHub Pages (static Jekyll) |
| `sucklingweb` | `D:\git\Sites\sucklingweb` | Member dashboard webapp | Render (FastAPI service) |

GitHub org: https://github.com/rupertosandez/suckling · Maintainer: rupertosandez

---

## 1. sucklingbot — the Discord bot

The core product. Discord bot for the "Return by 9" movie community.

- **Language/runtime:** Python 3.10
- **Framework:** `discord.py` (slash commands + message content intent)
- **Integrations:** TMDB (movie data), Plex via `plexapi` (RB9 library), Letterboxd (scraping), Tautulli (cleanup)
- **Persistence:** SQLite by default (`data/moviebot.db`); Postgres via `psycopg` when `DATABASE_URL` is set
- **Scheduling:** APScheduler (`AsyncIOScheduler`)
- **Packaging:** PyInstaller (`Suckling.spec`)
- **Supervision:** Windows tray launcher (`launcher/`, `launcher.py`) that supervises `bot.py`
- **Feature areas (`cogs/`):** discovery, rb9, rentals, tracking, watchlist, letterboxd, games, macguffins, achievements, admin, meta

Required env: `DISCORD_TOKEN`, `TMDB_API_KEY`, `GUILD_ID`. Optional: `DATABASE_URL`, `PLEX_TOKEN`, `PLEX_LIBRARY`, `BOT_TIMEZONE`, `TAUTULLI_*`, etc.

---

## 2. sucklingsite — GitHub Pages docs site

Static public minisite documenting the bot.

- **Type:** Static site, no backend
- **Generator:** Jekyll with `kramdown` markdown, pretty permalinks (`_config.yml`)
- **Hosting:** GitHub Pages, deployed from `main` branch / root folder
- **Content:** `index.md` (homepage), `COMMANDS.md` (public command reference), `CHANGELOG.md`, plus `macguffins.md` and `achievements.md`
- **Theme:** custom `_layouts/default.html` shell, `assets/css/style.css` dark theme, Fraunces headline font from Google Fonts (no font files committed)

This is the site kept in sync during the bot's release workflow — `CHANGELOG.md` and `COMMANDS.md` here mirror the bot repo, in member-facing casual lowercase copy.

---

## 3. sucklingweb — Render member dashboard

Logged-in webapp experiment: members sign in with Discord and see their RB9 activity.

- **Language/runtime:** Python
- **Framework:** FastAPI, served by `uvicorn` (Jinja2 templates, `itsdangerous` sessions)
- **Auth:** Discord OAuth (`/auth/discord/callback`), scoped to the RB9 guild
- **Data source:** reads live member data from Postgres when `DATABASE_URL` is set; falls back to the bot's SQLite DB at `BOT_DB_PATH`. Public catalog JSON (macguffins, sets, achievements) is bundled in `app/data` so it can deploy without the bot repo
- **Dashboard surfaces:** macguffins & set progress, achievements, active rentals & recent returns, watchlist, community collections
- **Hosting:** Render web service — build `pip install -r requirements.txt`, start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, persistent disk at `/var/data`, internal Render Postgres

Key env: `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_GUILD_ID`, `SESSION_SECRET`, `DATABASE_URL` (or `BOT_DB_PATH`), `BASE_URL`, `PLEX_MACHINE_ID`.

---

## How they fit together

- **The bot** is the source of truth: it writes all member activity to SQLite/Postgres and owns the catalog JSON (macguffins, sets, achievements).
- **sucklingweb** reads that same database (Postgres in production, SQLite locally) to render a live per-member dashboard.
- **sucklingsite** is static documentation that mirrors the bot's `CHANGELOG.md` / `COMMANDS.md` on each release.
- Postgres is the shared production datastore between the bot and the webapp; SQLite remains the local/fallback path for both.
