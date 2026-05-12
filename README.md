# sucklingbot

a discord bot built specifically for the **return by 9** movie community. looks up films, tracks streaming availability, posts daily recommendations, runs poster/still guessing games and six degrees of separation rounds, and pulls random picks and stats from the return by 9 plex library.

built on python + discord.py + tmdb + plexapi, with sqlite for persistence.

---

## what it does

- `/suck` - film lookup with full availability info (theatrical + streaming, per-region)
- `/roll` - random film pick with decade and runtime filters
- `/rb9` + 9 stat commands - pick from the return by 9 library, plus stats (longest, shortest, oldest, decade breakdown, genres, etc.)
- `/track` - community watchlist with first-time streaming alerts
- `/guess` - poster + still guessing game with scaled scoring (1 pt easy, 2 pts hard)
- `/six` - six degrees of separation game with chain validation against tmdb cast data
- daily streaming announcements at 9 am, with first-time-only filtering (no re-promotion noise)
- daily recommendations at noon, with 30-day no-repeat window
- toggle controls for both auto-posting features
- persistent sqlite for tracked films, leaderboards, and provider snapshots
- in-memory caching for tmdb calls
- error logging to `data/bot.log`

for a full command reference, see [commands.md](COMMANDS.md).

for change history, see [changelog.md](CHANGELOG.md).

---

## setup

### prerequisites

- python 3.10 or higher
- a discord bot application + token ([discord developer portal](https://discord.com/developers/applications))
- a tmdb v3 api key ([tmdb account settings](https://www.themoviedb.org/settings/api))
- (optional) a plex auth token if you want `/rb9` and the stats commands

### quick start

```bash
git clone https://github.com/YourUsername/sucklingbot.git
cd sucklingbot
python -m venv venv
venv\Scripts\Activate.ps1   # windows; use source venv/bin/activate on macos/linux
pip install -r requirements.txt
```

if `Activate.ps1` errors with execution policy issues on windows:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### environment variables

create a `.env` file in the project root:

```
DISCORD_TOKEN=your_discord_bot_token
TMDB_API_KEY=your_tmdb_v3_api_key
GUILD_ID=your_discord_server_id
PLEX_TOKEN=your_plex_token
PLEX_LIBRARY=Movies
```

| variable        | required | notes                                         |
| --------------- | -------- | --------------------------------------------- |
| `DISCORD_TOKEN` | yes      | from the discord developer portal             |
| `TMDB_API_KEY`  | yes      | v3 key from tmdb account settings             |
| `GUILD_ID`      | yes      | server id; right-click your server then copy id |
| `PLEX_TOKEN`    | no       | enables `/rb9` and `/rb9*` stats commands     |
| `PLEX_LIBRARY`  | no       | plex library name (default: `Movies`)         |

the `.env` file is gitignored — never commit it.

### first run

```bash
python bot.py
```

you should see startup output confirming the version, database init, and slash command sync.

in discord, configure the auto-posting channels (admin only):

```
/setannouncements <channel>
/setdaily <channel>
```

then optionally toggle features off if you want them disabled:

```
/toggle feature:streaming-announcements enabled:False
/toggle feature:daily-recommendation enabled:False
```

---

## file structure

```
sucklingbot/
├── bot.py              main entry, command definitions, scheduler
├── config.py           loads .env, exposes config constants
├── version.py          version constant
├── tmdb.py             tmdb api wrapper + caching
├── embeds.py           discord embed builders
├── views.py            discord ui components (dropdowns)
├── db.py               sqlite schema and helpers
├── tracker.py          daily streaming-availability scan
├── picker.py           random film candidate pool + filtering
├── imageops.py         poster cropping for /guess
├── game.py             /guess round state
├── sixdegrees.py       /six round state, chain parsing, validation
├── plex.py             plex connection, random pick, library stats
├── cache.py            in-memory ttl cache
├── logger.py           file logging setup
├── requirements.txt    python dependencies
├── COMMANDS.md         user-facing command reference
├── CHANGELOG.md        release history
├── README.md           this file
├── .env                secrets (gitignored)
└── data/               persistent state (gitignored)
    ├── moviebot.db
    └── bot.log
```

---

## architecture notes

- `bot.py` is the only file that imports discord.py command/event decorators. everything else is plain async python.
- `tmdb.py` uses the cache transparently — pass `force=True` to bypass when fresh data is needed.
- `tracker.py` uses `force=True` everywhere because it needs fresh data to detect changes.
- `picker.py` maintains a separate 24-hour-cached candidate pool of ~1000 films (used by `/roll` and the daily recommendation).
- `plex.py` uses `asyncio.to_thread` to wrap `plexapi`'s synchronous calls so they don't block the event loop. the library list is cached for 1 hour.
- `sixdegrees.py` maintains a separate 24-hour-cached pool of popular actors and uses tmdb cast data to validate chain submissions.
- all persistent state lives in `data/moviebot.db`. loseable: in-memory caches, active rounds.
- the error log captures exceptions from scheduled jobs and `on_message` handlers.

### database tables

| table                | purpose                                                                |
| -------------------- | ---------------------------------------------------------------------- |
| `config`             | key/value settings (channels, toggles)                                 |
| `tracked_movies`     | user-curated watchlist                                                 |
| `provider_snapshots` | `(movie_id, provider)` pairs we've seen — used to detect new providers |
| `announced_movies`   | tmdb ids ever announced — prevents re-promotion announcements          |
| `daily_recs`         | past daily picks (powers the 30-day no-repeat window)                  |
| `guess_scores`       | leaderboard for poster/still guessing                                  |
| `six_scores`         | leaderboard for six degrees game                                       |

---

## scheduled jobs

two scheduled jobs run via apscheduler:

- 9:00 am local time - streaming-availability scan + announcements
- 12:00 pm local time - daily recommendation

both can be disabled at runtime with `/toggle` without removing channel configuration.

manual triggers are available via `/checknow`, `/checknowlive`, and `/dailynow` — these run regardless of toggle state.

---

## adding a new command

1. add any new helper functions in the appropriate module (`tmdb.py`, `db.py`, `plex.py`, etc.)
2. add the command to `bot.py` using the `@bot.tree.command(...)` decorator pattern
3. add an embed builder in `embeds.py` if the response needs visual structure
4. bump `version.py` and add a `changelog.md` entry
5. restart the bot — slash commands re-sync to the configured guild on every startup

adding a command takes about 20 lines of code in most cases.

---

## versioning

the project follows [semantic versioning](https://semver.org/):

- **major** - breaking changes (renamed/removed commands, new required env vars)
- **minor** - new features that don't break existing ones
- **patch** - bug fixes, copy tweaks, internal refactors

the current version is in [`version.py`](version.py) and is logged on startup.

---

## updating

if you're running the bot from this repo, updates flow through git:

```bash
# on the live machine
git pull
# stop the bot (ctrl+c) and restart
python bot.py
```

`.env` and `data/` aren't in the repo, so they persist across pulls.

---

## troubleshooting

### bot doesn't start

- `DISCORD_TOKEN not set in .env` - `.env` is missing or in the wrong folder. should be in the same folder as `bot.py`.
- `Improper token` - the token is wrong or has been reset. generate a new one in the discord developer portal.
- `Privileged Intents Required` - enable "message content intent" on the bot's settings page in the discord developer portal.

### slash commands don't appear

- stale discord cache — fully quit discord (system tray → quit) and reopen
- wrong `GUILD_ID` — verify with `Get-Content .env`. to get the right id, enable developer mode in discord (user settings → advanced) and right-click the server icon

### `/rb9` and stats don't work

- `Plex token is invalid or expired` - get a fresh token from your plex account
- `No Plex servers found on this account` - token is for an account with no owned servers
- `Library 'Movies' not found` - the error message lists available libraries. update `PLEX_LIBRARY` in `.env`

### `/six` validation feels too strict

chain matching is fuzzy on punctuation and case but doesn't handle name variants well (e.g. accent marks, last-first conventions). if a chain you know is correct keeps getting rejected, the tmdb-listed name might differ from the common name.

### streaming announcements feel wrong

- re-promotions appearing — check that the `announced_movies` table exists in `data/moviebot.db`
- nothing announced for weeks — could be genuine, or the daily check is failing. check `data/bot.log` for errors. verify `/toggle` hasn't disabled it
- films you expect aren't being tracked — they may not be in the discover candidate pool. use `/track` to add them explicitly

### logs

`data/bot.log` captures errors and warnings only. the file rotates automatically when it hits 1 mb, keeping the last 3 files.

---

## backup and recovery

the project files themselves are backed up by git. the pieces that aren't tracked (and matter):

- `data/moviebot.db` - leaderboards, tracked films, provider snapshots, announced movies. if this is lost, the bot will treat its first run as a baseline and silently re-mark all currently-streaming films as already announced.
- `.env` - secrets. easy to recreate if you have your tokens recorded elsewhere.

to back up the database manually:

```bash
cp data/moviebot.db data/moviebot.db.backup
```

worth doing periodically, especially before significant code changes.

---

## license

personal project for the return by 9 community. no license specified — if you want to use parts of this for your own bot, feel free; just don't use the exact bot configuration or branding.

---

## acknowledgments

- [tmdb](https://www.themoviedb.org/) for the comprehensive movie database
- [justwatch](https://www.justwatch.com/) (via tmdb) for streaming availability data
- [discord.py](https://discordpy.readthedocs.io/) for the discord library
- [plexapi](https://python-plexapi.readthedocs.io/) for plex integration