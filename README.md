# sucklingbot

A Discord bot built specifically for the **Return by 9** movie community. Looks up films, tracks streaming availability, posts daily recommendations, runs poster/still guessing games and Six Degrees of Separation rounds, and pulls random picks and stats from the Return by 9 Plex library.

Built on Python + discord.py + TMDB + plexapi, with SQLite for persistence.

---

## Features

- **`/watch`** — film lookup with full availability info (theatrical + streaming, per-region)
- **`/roll`** — random film pick with decade and runtime filters
- **`/rb9`** + 9 stat commands — pick from the Return by 9 library, plus stats (longest, shortest, oldest, decade breakdown, genres, etc.)
- **`/track`** — community watchlist with first-time streaming alerts
- **`/guess`** — poster + still guessing game with scaled scoring (1 pt easy, 2 pts hard)
- **`/six`** — Six Degrees of Separation game with chain validation against TMDB cast data
- **Daily streaming announcements** at 9 AM, with first-time-only filtering (no re-promotion noise)
- **Daily recommendations** at noon, with 30-day no-repeat window
- **Toggle controls** for both auto-posting features
- **Persistent SQLite** for tracked films, leaderboards, and provider snapshots
- **In-memory caching** for TMDB calls
- **Error logging** to `data/bot.log`

For a full command reference, see [COMMANDS.md](COMMANDS.md).

For change history, see [CHANGELOG.md](CHANGELOG.md).

---

## Setup

### Prerequisites

- Python 3.10 or higher
- A Discord bot application + token ([Discord developer portal](https://discord.com/developers/applications))
- A TMDB v3 API key ([TMDB account settings](https://www.themoviedb.org/settings/api))
- (Optional) A Plex auth token if you want `/rb9` and the stats commands

### Quick start

```bash
git clone https://github.com/YourUsername/sucklingbot.git
cd sucklingbot
python -m venv venv
venv\Scripts\Activate.ps1   # Windows; use source venv/bin/activate on macOS/Linux
pip install -r requirements.txt
```

If `Activate.ps1` errors with execution policy issues on Windows:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Environment variables

Create a `.env` file in the project root:

```
DISCORD_TOKEN=your_discord_bot_token
TMDB_API_KEY=your_tmdb_v3_api_key
GUILD_ID=your_discord_server_id
PLEX_TOKEN=your_plex_token
PLEX_LIBRARY=Movies
```

| Variable        | Required | Notes                                         |
| --------------- | -------- | --------------------------------------------- |
| `DISCORD_TOKEN` | Yes      | From the Discord developer portal             |
| `TMDB_API_KEY`  | Yes      | v3 key from TMDB account settings             |
| `GUILD_ID`      | Yes      | Server ID; right-click your server then Copy ID |
| `PLEX_TOKEN`    | No       | Enables `/rb9` and `/rb9*` stats commands     |
| `PLEX_LIBRARY`  | No       | Plex library name (default: `Movies`)         |

The `.env` file is gitignored — never commit it.

### First run

```bash
python bot.py
```

You should see startup output confirming the version, database init, and slash command sync.

In Discord, configure the auto-posting channels (admin only):

```
/setannouncements <channel>
/setdaily <channel>
```

Then optionally toggle features off if you want them disabled:

```
/toggle feature:streaming-announcements enabled:False
/toggle feature:daily-recommendation enabled:False
```

---

## File Structure

```
sucklingbot/
├── bot.py              Main entry, command definitions, scheduler
├── config.py           Loads .env, exposes config constants
├── version.py          Version constant
├── tmdb.py             TMDB API wrapper + caching
├── embeds.py           Discord embed builders
├── views.py            Discord UI components (dropdowns)
├── db.py               SQLite schema and helpers
├── tracker.py          Daily streaming-availability scan
├── picker.py           Random film candidate pool + filtering
├── imageops.py         Poster cropping for /guess
├── game.py             /guess round state
├── sixdegrees.py       /six round state, chain parsing, validation
├── plex.py             Plex connection, random pick, library stats
├── cache.py            In-memory TTL cache
├── logger.py           File logging setup
├── requirements.txt    Python dependencies
├── COMMANDS.md         User-facing command reference
├── CHANGELOG.md        Release history
├── README.md           This file
├── .env                Secrets (gitignored)
└── data/               Persistent state (gitignored)
    ├── moviebot.db
    └── bot.log
```

---

## Architecture Notes

- **`bot.py`** is the only file that imports discord.py command/event decorators. Everything else is plain async Python.
- **`tmdb.py`** uses the cache transparently — pass `force=True` to bypass when fresh data is needed.
- **`tracker.py`** uses `force=True` everywhere because it needs fresh data to detect changes.
- **`picker.py`** maintains a separate 24-hour-cached candidate pool of ~1000 films (used by `/roll` and the daily recommendation).
- **`plex.py`** uses `asyncio.to_thread` to wrap `plexapi`'s synchronous calls so they don't block the event loop. The library list is cached for 1 hour.
- **`sixdegrees.py`** maintains a separate 24-hour-cached pool of popular actors and uses TMDB cast data to validate chain submissions.
- **All persistent state** lives in `data/moviebot.db`. Loseable: in-memory caches, active rounds.
- **The error log** captures exceptions from scheduled jobs and `on_message` handlers.

### Database tables

| Table                | Purpose                                                                |
| -------------------- | ---------------------------------------------------------------------- |
| `config`             | Key/value settings (channels, toggles)                                 |
| `tracked_movies`     | User-curated watchlist                                                 |
| `provider_snapshots` | `(movie_id, provider)` pairs we've seen — used to detect new providers |
| `announced_movies`   | TMDB IDs ever announced — prevents re-promotion announcements          |
| `daily_recs`         | Past daily picks (powers the 30-day no-repeat window)                  |
| `guess_scores`       | Leaderboard for poster/still guessing                                  |
| `six_scores`         | Leaderboard for Six Degrees game                                       |

---

## Scheduled jobs

Two scheduled jobs run via APScheduler:

- **9:00 AM local time** — streaming-availability scan + announcements
- **12:00 PM local time** — daily recommendation

Both can be disabled at runtime with `/toggle` without removing channel configuration.

Manual triggers are available via `/checknow`, `/checknowlive`, and `/dailynow` — these run regardless of toggle state.

---

## Adding a New Command

1. Add any new helper functions in the appropriate module (`tmdb.py`, `db.py`, `plex.py`, etc.).
2. Add the command to `bot.py` using the `@bot.tree.command(...)` decorator pattern.
3. Add an embed builder in `embeds.py` if the response needs visual structure.
4. Bump `version.py` and add a `CHANGELOG.md` entry.
5. Restart the bot — slash commands re-sync to the configured guild on every startup.

Adding a command takes about 20 lines of code in most cases.

---

## Versioning

The project follows [Semantic Versioning](https://semver.org/):

- **MAJOR** — breaking changes (renamed/removed commands, new required env vars)
- **MINOR** — new features that don't break existing ones
- **PATCH** — bug fixes, copy tweaks, internal refactors

The current version is in [`version.py`](version.py) and is logged on startup.

---

## Updating

If you're running the bot from this repo, updates flow through Git:

```bash
# On the live machine
git pull
# Stop the bot (Ctrl+C) and restart
python bot.py
```

`.env` and `data/` aren't in the repo, so they persist across pulls.

---

## Troubleshooting

### Bot doesn't start

- `DISCORD_TOKEN not set in .env` — `.env` is missing or in the wrong folder. Should be in the same folder as `bot.py`.
- `Improper token` — the token is wrong or has been reset. Generate a new one in the Discord developer portal.
- `Privileged Intents Required` — enable "Message Content Intent" on the bot's settings page in the Discord developer portal.

### Slash commands don't appear

- Stale Discord cache — fully quit Discord (system tray → Quit) and reopen.
- Wrong `GUILD_ID` — verify with `Get-Content .env`. To get the right ID, enable Developer Mode in Discord (User Settings → Advanced) and right-click the server icon.

### `/rb9` and stats don't work

- `Plex token is invalid or expired` — get a fresh token from your Plex account.
- `No Plex servers found on this account` — token is for an account with no owned servers.
- `Library 'Movies' not found` — the error message lists available libraries. Update `PLEX_LIBRARY` in `.env`.

### `/six` validation feels too strict

Chain matching is fuzzy on punctuation and case but doesn't handle name variants well (e.g. accent marks, last-first conventions). If a chain you know is correct keeps getting rejected, the TMDB-listed name might differ from the common name.

### Streaming announcements feel wrong

- Re-promotions appearing — check that the `announced_movies` table exists in `data/moviebot.db`.
- Nothing announced for weeks — could be genuine, or the daily check is failing. Check `data/bot.log` for errors. Verify `/toggle` hasn't disabled it.
- Films you expect aren't being tracked — they may not be in the Discover candidate pool. Use `/track` to add them explicitly.

### Logs

`data/bot.log` captures errors and warnings only. The file rotates automatically when it hits 1 MB, keeping the last 3 files.

---

## Backup and Recovery

The project files themselves are backed up by Git. The pieces that aren't tracked (and matter):

- **`data/moviebot.db`** — leaderboards, tracked films, provider snapshots, announced movies. If this is lost, the bot will treat its first run as a baseline and silently re-mark all currently-streaming films as already announced.
- **`.env`** — secrets. Easy to recreate if you have your tokens recorded elsewhere.

To back up the database manually:

```bash
cp data/moviebot.db data/moviebot.db.backup
```

Worth doing periodically, especially before significant code changes.

---

## License

Personal project for the Return by 9 community. No license specified — if you want to use parts of this for your own bot, feel free; just don't use the exact bot configuration or branding.

---

## Acknowledgments

- [TMDB](https://www.themoviedb.org/) for the comprehensive movie database
- [JustWatch](https://www.justwatch.com/) (via TMDB) for streaming availability data
- [discord.py](https://discordpy.readthedocs.io/) for the Discord library
- [plexapi](https://python-plexapi.readthedocs.io/) for Plex integration
