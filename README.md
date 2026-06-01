# sucklingbot

a discord bot built specifically for the **return by 9** movie community. looks up films, tracks streaming availability, posts daily recommendations, runs poster/still guessing games and six degrees of separation rounds, pulls random picks and stats from the return by 9 plex library, runs a video-store-themed rental system where members can check out films until 9 pm on the fifth day and post reviews, and awards achievement badges for movie-club activity.

built on python + discord.py + tmdb + plexapi, with sqlite for persistence.

---

## what it does

- `/suck` - film lookup with full availability info (theatrical + streaming, per-region)
- `/roll` - random film pick with decade and runtime filters
- `/rb9` + 9 stat commands - pick from the return by 9 library, plus stats (longest, shortest, oldest, decade breakdown, genres, etc.)
- `/rent` - rent a random library film, due by 9 pm on the fifth day, with up to 2 rerolls. past rentals excluded forever
- `/timezone` - set your timezone so rentals are due at 9 pm where you are
- `/return` - return your rental and post a review to a configurable forum channel, with late-fee tracking
- `/extend` - one-time 24-hour rental extension, also available from reminder DMs
- `/latefees` - leaderboard of accumulated late fees
- `/rentalstats` - personal rental history
- `/achievements` - badge shelf for rental, rb9 library, review, macguffin, game, discovery, and Letterboxd milestones
- `/track` - community watchlist with first-time streaming alerts
- `/guess` - poster + still guessing game with scaled scoring (1 pt easy, 2 pts hard)
- `/play` - trivia roulette game with four categories (quote, emoji, tagline, trivia)
- `/six` - six degrees of separation game with chain validation against tmdb cast data
- 📼 rent buttons on `/rb9`, `/rb9randomscene`, `/suck`, `/roll`, and the daily rec (shown when the film is in the library)
- daily streaming announcements at 9 am, with first-time-only filtering (no re-promotion noise)
- daily recommendations at noon, with 30-day no-repeat window
- optional Letterboxd activity posts for linked members
- achievement unlocks in the configured Suckling feed channel
- toggle controls for scheduled auto-posting features
- persistent sqlite for tracked films, leaderboards, provider snapshots, and rental records
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
- (optional) a plex auth token if you want `/rb9`, the stats commands, and the rental system

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
BOT_TIMEZONE=America/Los_Angeles
SUCKLINGBOT_DATA_DIR=C:\path\to\sucklingbot\data
ACHIEVEMENT_CATALOG_URL=https://rupertosandez.github.io/sucklingsite/achievements/
```

| variable                 | required | notes                                         |
| ------------------------ | -------- | --------------------------------------------- |
| `DISCORD_TOKEN`          | yes      | from the discord developer portal             |
| `TMDB_API_KEY`           | yes      | v3 key from tmdb account settings             |
| `GUILD_ID`               | yes      | server id; right-click your server then copy id |
| `PLEX_TOKEN`             | no       | enables `/rb9`, `/rb9*` stats, and `/rent`    |
| `PLEX_LIBRARY`           | no       | plex library name (default: `Movies`)         |
| `BOT_TIMEZONE`           | no       | local timezone for rental due dates (default: `America/Los_Angeles`) |
| `SUCKLINGBOT_DATA_DIR`   | no       | custom data folder; useful for worktree testing |
| `SUCKLINGBOT_ASSETS_DIR` | no       | custom assets folder; defaults to project assets |
| `ACHIEVEMENT_CATALOG_URL` | no      | website page linked by `/achievementcatalog` |

the `.env` file is gitignored — never commit it.

custom data and assets paths can be absolute, or relative to the project root.

### first run

```bash
venv\Scripts\python.exe launcher.py
```

or double-click `launch.vbs` to start it without a terminal window. the launcher is the default way to run sucklingbot: it owns start/stop/restart, keeps one bot process running, and blocks duplicate launcher instances.

## desktop launcher

the launcher lives in the system tray and wraps the bot process.

```bash
venv\Scripts\python.exe launcher.py
```

`launch.bat` is also available when you want a visible troubleshooting terminal.

right-click the tray icon to start, stop, and restart the bot, view its log, and apply updates from github with one click. the bot can still be run directly for debugging, but the launcher path is safer for normal use because it tracks the child process and prevents accidental duplicates.

right-click the tray icon for the menu. the launcher checks for updates daily and on startup; when one is available, the menu shows the version diff and an `update and restart` option that pulls from main, installs any new requirements, and restarts the bot.

local uncommitted changes block auto-update - the launcher will tell you to commit or stash first.

to build the optional Windows app wrapper, install the dev requirements and run:

```bash
venv\Scripts\python.exe -m pip install -r requirements-dev.txt
build_suckling.bat
```

that produces `Suckling.exe` in the project root. keep it beside the repo files so it can supervise `bot.py`, use `assets/`, and write to `data/`.

in discord, configure the auto-posting channels (admin only):

```
/setannouncements <channel>
/setdaily <channel>
```

to enable the rental system, create a discord forum channel, add **rental** and **recommendation** tags to it in the forum settings, then:

```
/setreviews <forum_channel>
```

the bot will confirm it found the tags. if tags are missing it will tell you what to create.

to enable achievement unlock announcements, choose a feed channel:

```
/setfeed <channel>
```

achievement badge roles are created by the bot when members pin badges with `/achievementdisplay`. the bot needs **manage roles**, and its own Discord role must be above the achievement badge roles it creates or edits.

to refresh the website achievement catalog after changing `achievements.py`:

```
venv\Scripts\python.exe scripts\export_achievement_catalog.py
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
├── bot.py                    runtime entry, scheduler, message routing
├── cogs/                     slash command groups
│   ├── admin.py              admin dashboard, toggles, manual checks
│   ├── achievements.py       achievement shelf, feed, roles, and admin tools
│   ├── discovery.py          /suck, /roll, daily recommendations
│   ├── games.py              /guess, /play, /six, leaderboards
│   ├── letterboxd.py         /lb commands
│   ├── macguffins.py         macguffin commands
│   ├── meta.py               bot info commands
│   ├── rb9.py                rb9 library commands
│   ├── rentals.py            rental commands and admin tools
│   ├── tracking.py           streaming watchlist setup and tracking
│   └── watchlist.py          personal watchlist commands
├── config.py                 loads .env, exposes config constants
├── version.py                version constant
├── achievements.py           achievement definitions, evaluation, feed embeds, role sync
├── tmdb.py                   tmdb api wrapper + caching
├── embeds.py                 discord embed builders
├── views.py                  discord ui components (dropdowns, rental views)
├── db.py                     sqlite schema and helpers
├── rental.py                 rental lifecycle: forum threads, late fees, DMs
├── macguffin.py              macguffin card pool, drops, transfers
├── tracker.py                daily streaming-availability scan
├── picker.py                 random film candidate pool + filtering
├── imageops.py               poster cropping for /guess
├── game.py                   /guess round state
├── sixdegrees.py             /six round state, chain parsing, validation
├── trivia_roulette.py        /play round state, json asset loading, matching
├── letterboxd.py             letterboxd diary/watchlist integration
├── plex.py                   plex connection, random pick, library stats
├── cache.py                  in-memory ttl cache
├── logger.py                 file logging setup
├── launcher.py               desktop launcher entry point
├── launcher/                 tray app, subprocess manager, updates, state
├── launch.vbs                no-console launcher for windows
├── launch.bat                visible terminal launcher for troubleshooting
├── requirements.txt          python dependencies
├── COMMANDS.md               user-facing command reference
├── CHANGELOG.md              release history
├── README.md                 this file
├── .env                      secrets (gitignored)
├── assets/                   curated game content (committed)
│   ├── quotes.json
│   ├── emoji.json
│   ├── taglines.json
│   ├── trivia.json
│   └── macguffins.json
└── data/                     persistent state (gitignored)
    ├── moviebot.db
    └── bot.log
```

---

## architecture notes

- `bot.py` owns runtime setup, scheduled jobs, startup/shutdown handling, and message routing.
- `launcher.py` is the normal runtime supervisor: it starts one bot child process, tracks its pid, blocks duplicate launcher instances, and owns tray controls.
- slash commands live in `cogs/`, grouped by feature area and loaded during startup.
- `achievements.py` is the achievement registry and evaluator. watched-movie achievements use returned rentals as the source of truth.
- `rental.py` never imports `bot.py` — takes `bot: discord.Client` as a parameter, same pattern as `tracker.py`.
- `tmdb.py` uses the cache transparently — pass `force=True` to bypass when fresh data is needed.
- `tracker.py` uses `force=True` everywhere because it needs fresh data to detect changes.
- `picker.py` maintains a separate 24-hour-cached candidate pool of ~1000 films (used by `/roll` and the daily recommendation).
- `plex.py` uses `asyncio.to_thread` to wrap `plexapi`'s synchronous calls so they don't block the event loop. the library snapshot is persisted in sqlite, refreshed incrementally hourly, and fully reconciled weekly.
- `sixdegrees.py` maintains a separate 24-hour-cached pool of popular actors and uses tmdb cast data to validate chain submissions.
- all persistent state lives in `data/moviebot.db`. loseable: in-memory caches, active rounds, active rental view state (in-progress `/rent` flows restart cleanly).
- the error log captures exceptions from scheduled jobs and `on_message` handlers.

### database tables

| table                | purpose                                                                |
| -------------------- | ---------------------------------------------------------------------- |
| `config`             | key/value settings (channels, toggles, rental tag IDs)                 |
| `tracked_movies`     | user-curated watchlist                                                 |
| `provider_snapshots` | `(movie_id, provider)` pairs we've seen — used to detect new providers |
| `announced_movies`   | tmdb ids ever announced — prevents re-promotion announcements          |
| `daily_recs`         | past daily picks (powers the 30-day no-repeat window)                  |
| `guess_scores`       | leaderboard for poster/still guessing                                  |
| `six_scores`         | leaderboard for six degrees game                                       |
| `rentals`            | full rental lifecycle: status, plex key snapshot, thread IDs, rating, late fee, extension count, notification flags |
| `plex_library_cache` | persisted Plex library snapshot, including searchable metadata used by rb9 achievements |
| `achievement_earned` | unlocked achievement records                                           |
| `achievement_display` | each member's selected visible badge roles                            |
| `achievement_roles`  | Discord role IDs created for achievement badges                        |
| `achievement_events` | event counters for achievement progress that is not stored elsewhere   |

---

## scheduled jobs

three scheduled jobs run via apscheduler:

- **9:00 am** local time - streaming-availability scan + announcements
- **12:00 pm** local time - daily recommendation
- **every hour** - rental overdue DMs (once per rental when due_at passes) and 12-hour reminders (once per rental when <12h remain)

scheduled auto-posting features can be disabled at runtime with `/toggle` without removing channel configuration.

manual triggers are available via `/checknow`, `/checknowlive`, and `/dailynow` — these run regardless of toggle state.

---

## adding a new command

1. add any new helper functions in the appropriate module (`tmdb.py`, `db.py`, `plex.py`, etc.)
2. add the command to the appropriate `cogs/*.py` module using the `app_commands` pattern
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

if you're running the launcher, right-click the tray icon and choose `check for updates now` or use the `update and restart` option when it appears.

if you're running the bot manually from this repo, updates flow through git:

```bash
# on the live machine
git pull
# right-click the tray icon and choose restart bot
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

### `/rent` says the reviews forum isn't configured

run `/setreviews` as an admin and point it at a discord forum channel. make sure the bot has **create public threads** and **send messages in threads** permissions in that channel.

### `/setreviews` says tags weren't found

create **rental** and **recommendation** tags in the forum channel's settings (edit channel → tags), then run `/setreviews` again. the bot auto-detects them by name.

### rent button doesn't appear on `/suck` or `/roll`

the button only shows when the film is confirmed to be in the Plex library. if plex is unreachable or the film isn't there, the button is omitted rather than showing a broken state.

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

- `data/moviebot.db` - leaderboards, tracked films, provider snapshots, announced movies, **rental records**. if this is lost, the bot will treat its first run as a baseline and silently re-mark all currently-streaming films as already announced. rental history will be lost.
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
