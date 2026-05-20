# Sucklingbot Project Overview

**Last updated:** May 19, 2026
**Current version:** 2.3.0
**Maintainer:** rupertosandez (GitHub)

---

## Project Summary

A Discord bot built for the "Return by 9" movie community. Integrates TMDB for movie lookups, Plex for library management, and SQLite for persistent state. Hosted on the maintainer's home PC.

**Stack:** Python 3.10+ • discord.py • TMDB API v3 • plexapi • SQLite • APScheduler

---

## Core Features

### Film Lookup & Discovery
- **`/suck <title> [year]`** - Look up films with full streaming availability (theatrical + digital, per-region)
- **`/roll [decade] [runtime]`** - Random film recommendations with optional filters
- **`/track <title>`** - Community watchlist with first-time-streaming alerts
- **`/tracked`** - View all tracked films and who added them

### Letterboxd & Personal Watchlists
- **`/lb link <username>`** - Link a public Letterboxd account
- **`/lb profile [user] [username]`** - Recent diary activity
- **`/lb watchlist [user] [username]`** - Browse, roll from, or import a Letterboxd watchlist
- **`/lb group`** - Recent linked-member Letterboxd activity
- **`/lb tastecheck`** - Recent taste compatibility check between any two accounts
- **Letterboxd activity feed** - Optional auto-post channel for linked members' new diary entries
- **`/watchlist show/add/remove`** - Per-user private film queue with roll/remove controls

### Return by 9 Library (Plex-backed)
- **`/rb9`** - Random pick from RB9 library with poster
- **`/rb9randomscene`** - Random film + backdrop image
- **Stats commands:** `/rb9stats`, `/rb9biggest`, `/rb9shortest`, `/rb9oldest`, `/rb9newest`, `/rb9decade`, `/rb9genre`, `/rb9totalruntime`

### Video Store Rental System
- **`/rent`** - Rent a random film (48-hour window, 2 re-rolls, permanent exclusion of past rentals)
- **`/return <rating> <recommend> [thoughts]`** - Return rental + post review to forum + roll a MacGuffin drop
- **`/extend`** - Extend active rental by 24 hours (once per rental)
- **`/myrental`** - Current rental status with live countdown
- **`/latefees`** - Late fee leaderboard (cosmetic $1/day)
- **`/rentalstats [user]`** - Rental history & stats
- **`/setreviews <forum_channel>`** - Admin: configure review forum
- **`/cancelrental @user [reason]`** - Admin: cancel rental with no fee
- **`/assignrental @user <title> [year]`** - Admin: assign specific rental to user

### MacGuffins
- **Drop on `/return`** - Globally unique collectible movie objects awarded after successful rental returns
- **`/claimguffin`** - One free starter MacGuffin per member
- **`/myguffins`** - Private paginated collection view with card details
- **`/giftguffin @user <card>`** - Gift a MacGuffin from your collection to another member
- **`/adminguffins <action> @user [card]`** - Admin: view, add, move, remove, or randomly assign member MacGuffins

### Games
- **`/guess [difficulty]`** - Poster/still guessing (1 pt easy, 2 pts hard, 60s timer)
- **`/play`** - Trivia roulette (quote, emoji, tagline, trivia categories, 30s timer)
- **`/six`** - Six degrees of separation (actor chains, scaled scoring, 4m timer)
- **`/leaderboard`** - Top `/guess` scorers
- **`/sixleaderboard`** - Top `/six` scorers
- **`/giveup`** - End current game round

### Auto-Posting (admin-configurable)
- **Daily recommendations** - Noon, random pick from TMDB
- **Streaming announcements** - 9am, new digital availability (first-time only, re-promotion prevention)
- **Letterboxd activity** - Hourly check for new diary entries from linked accounts
- **Feature toggles:** `/toggle feature:streaming-announcements enabled:False` etc.
- **Manual triggers:** `/checknow` (dry-run), `/checknowlive` (post live), `/dailynow`, `/lbactivitynow`

### Admin / Utilities
- **`/botstatus`** - Admin dashboard: version, uptime, latency, cache, channels, toggles, rental/LB counts, setup warnings
- **`/lblinked [page]`** - Paginated list of linked Letterboxd accounts (admin)
- **`/info`** - Bot info card
- **`/ping`** - Health check (latency)
- **`/version`** - Current bot version
- **`/cachestats [clear]`** - In-memory cache status
- **`/restart`** - Restart bot process

---

## Architecture

### Core Modules

| File | Purpose |
|------|---------|
| `bot.py` | Runtime entry point: bot setup, scheduler, signal handling, startup, and message routing |
| `cogs/` | Slash command cogs grouped by feature area |
| `cogs/admin.py` | Admin dashboard, toggles, manual checks, cache tools |
| `cogs/discovery.py` | `/suck`, `/roll`, daily recommendation posting |
| `cogs/games.py` | `/guess`, `/play`, `/six`, `/giveup`, game leaderboards |
| `cogs/letterboxd.py` | `/lb` account, profile, watchlist, group, and tastecheck commands |
| `cogs/macguffins.py` | MacGuffin claim, gift, collection, and admin commands |
| `cogs/meta.py` | Bot info/metadata commands |
| `cogs/rb9.py` | Plex-backed RB9 library pick and stats commands |
| `cogs/rentals.py` | Rental commands and rental admin tools |
| `cogs/tracking.py` | Streaming watchlist and auto-posting setup commands |
| `cogs/watchlist.py` | Personal watchlist commands |
| `config.py` | Environment variable loading (.env → config constants) |
| `tmdb.py` | TMDB API wrapper with request deduping, retry/backoff, semaphore, TTL caching |
| `plex.py` | Plex library connection, random pick, stats (async wrapper around plexapi) |
| `db.py` | SQLite schema, CRUD helpers (18KB) |
| `rental.py` | Rental lifecycle: forum threads, late fees, DMs, overdue checks |
| `macguffin.py` | MacGuffin card loading, validation, drops, and transfers |
| `tracker.py` | 9am streaming check: detects new providers, announces to Discord |
| `picker.py` | Random film candidate pool (~1000 films) + filtering (decade, runtime) |
| `embeds.py` | Discord embed builders (26KB, heavily formatted responses) |
| `views.py` | Discord UI: dropdowns, buttons, rental confirm/reroll flows (18KB) |
| `letterboxd.py` | Letterboxd diary/watchlist fetching and parsing |
| `game.py` | `/guess` round state |
| `sixdegrees.py` | `/six` round state, chain validation against TMDB cast data |
| `trivia_roulette.py` | `/play` round state, JSON asset matching |
| `imageops.py` | Poster cropping for hard `/guess` difficulty |
| `cache.py` | In-memory TTL cache (used by tmdb.py, picker.py, sixdegrees.py) |
| `logger.py` | File logging (data/bot.log, 1MB rotating) |
| `launcher.py` | Windows system tray launcher wrapper |
| `launcher/` | Tray UI, subprocess mgmt, auto-updates, state persistence |
| `version.py` | Version constant (currently 2.3.0) |

### Design Patterns

**Feature cogs:** Slash commands live under `cogs/` and are loaded by `bot.py` during startup. Runtime services such as scheduled checks still live in `bot.py` or feature modules.

**No circular imports:** `rental.py`, `tracker.py`, and cogs take `bot: discord.Client` as a parameter or callback instead of importing `bot.py`.

**Async throughout:** Everything uses `asyncio`. Plex calls wrapped with `asyncio.to_thread` to avoid blocking the event loop.

**Caching layers:**
- TMDB: in-memory TTL cache, global semaphore (8 concurrent requests), request deduping
- Plex: 1-hour library cache with refresh lock (prevents duplicate scans)
- Picker: 24-hour candidate pool of ~1000 films
- Six Degrees: 24-hour actor pool

**Persistent state:** SQLite only. Loseable: in-memory caches, active game rounds, active rental view state (restart restarts cleanly).

### Database Schema

| Table | Purpose |
|-------|---------|
| `config` | Key/value settings (channels, toggles, rental tag IDs) |
| `tracked_movies` | User watchlist for streaming alerts |
| `provider_snapshots` | (movie_id, provider) pairs seen; detects new providers |
| `announced_movies` | TMDB IDs announced; prevents re-promotions |
| `daily_recs` | Past daily picks; 30-day no-repeat window |
| `guess_scores` | Poster/still guessing leaderboard |
| `six_scores` | Six degrees leaderboard |
| `lb_accounts` | Discord user to Letterboxd username links |
| `lb_activity_seen` | Letterboxd diary entries already seeded or posted |
| `watchlist` | Per-user personal film queues |
| `rentals` | Full rental lifecycle (status, plex key, thread IDs, rating, late fee, notifications) |
| `macguffins` | Globally unique MacGuffin ownership records |
| `macguffin_free_claims` | One-time free claim tracking per user |

### Scheduled Jobs (APScheduler)

- **9:00 AM** - `tracker.run_check()` - Streaming availability scan + announcements
- **12:00 PM** - Daily recommendation pick + post
- **Every hour** - `rental_check()` - Overdue DMs (once) + 12-hour reminders (once)
- **Every hour** - `lb_activity_check()` - New linked-member Letterboxd diary posts

Auto-posting jobs can be toggled with `/toggle` without losing configuration.

---

## Key Recent Changes (v2.3.0 - May 19)

**MacGuffins**
- Successful `/return` commands now drop a globally unique collectible MacGuffin and announce it in-channel
- `/claimguffin`, `/myguffins`, and `/giftguffin` add starter claims, private collections, and member-to-member gifting
- `/adminguffins` lets admins view, add, move, remove, or randomly assign member MacGuffins
- MacGuffin cards are loaded from `assets/macguffins.json` and owned through SQLite records

## Key Recent Changes (v2.2.0 - May 19)

**Admin dashboard and Letterboxd account management**
- `/botstatus` — admin dashboard showing version, uptime, latency, cache size, configured channels, auto-posting toggles, tracked film count, linked Letterboxd accounts, active/overdue rentals, and setup warnings
- `/lblinked [page]` — paginated list of linked Letterboxd accounts with Discord member, LB profile, and linked date
- Letterboxd activity posting now only pulls entries from the last 60 minutes, preventing old unseen diary entries from flooding the channel on catchup runs

## Key Recent Changes (v2.1.0 - May 19)

**Letterboxd activity and tastecheck**
- Added an optional channel for new diary activity from linked Letterboxd accounts
- `/setlbactivity` seeds current feeds before enabling so old watches do not spam the channel
- `/lbactivitynow` lets admins dry-run or post a manual activity check
- `/lb tastecheck` now compares any two Discord members or raw Letterboxd usernames

## Key Recent Changes (v2.0.0 - May 18)

**Letterboxd and personal watchlists**
- Linked Letterboxd accounts with profile, watchlist, group activity, and tastecheck commands
- Per-user bot watchlists with add/remove/show/roll controls
- Film cards now include `+ watchlist` and, when rb9 has the film, `rent this`
- Daily recommendations, `/suck`, `/roll`, `/rb9`, and `/rb9randomscene` can start rentals from film cards

**Desktop Launcher** - Windows system tray app added in v1.9.0
- Start/stop/restart controls, live logs, update checks, and one-click update/restart

---

## File Structure Tree

```
sucklingbot/
├── bot.py                    # Runtime entry, scheduler, message routing
├── cogs/                     # Slash command groups
│   ├── admin.py              # Admin dashboard, toggles, manual checks
│   ├── discovery.py          # /suck, /roll, daily recommendations
│   ├── games.py              # /guess, /play, /six, leaderboards
│   ├── letterboxd.py         # /lb commands
│   ├── macguffins.py         # MacGuffin commands
│   ├── meta.py               # Bot info commands
│   ├── rb9.py                # RB9 library commands
│   ├── rentals.py            # Rental commands and admin tools
│   ├── tracking.py           # Streaming watchlist setup and tracking
│   └── watchlist.py          # Personal watchlist commands
├── config.py                 # .env → config constants
├── version.py                # VERSION = "2.3.0"
├── tmdb.py                   # TMDB API wrapper (cached, deduped, backoff)
├── plex.py                   # Plex library async wrapper
├── db.py                      # SQLite CRUD
├── rental.py                 # Rental system, forum threads, late fees, DMs
├── macguffin.py              # MacGuffin card pool, drops, transfers
├── tracker.py                # 9am streaming check
├── picker.py                 # Random candidate pool + filters
├── embeds.py                 # Discord embed builders
├── views.py                  # Discord UI components (dropdowns, buttons)
├── letterboxd.py             # Letterboxd diary/watchlist integration
├── game.py                   # /guess round state
├── sixdegrees.py             # /six round state
├── trivia_roulette.py        # /play round state
├── imageops.py               # Poster cropping
├── cache.py                  # In-memory TTL cache
├── logger.py                 # File logging setup
├── launcher.py               # Tray launcher entry
├── launcher/                 # Tray UI, subprocess, updates
│   ├── __init__.py
│   ├── process.py           # Subprocess manager
│   ├── state.py             # Launcher state (json)
│   ├── ui.py                # Tray menu
│   └── updater.py           # GitHub update checks
├── launch.vbs                # Double-click launcher (no terminal)
├── launch.bat                # Troubleshooting launcher (with terminal)
├── requirements.txt          # Dependencies
├── COMMANDS.md               # User-facing command reference
├── CHANGELOG.md              # Release history
├── README.md                 # Setup & overview
├── AGENTS.md                 # Codex notes (release workflow, site sync)
├── PROJECT_OVERVIEW.md       # This file
├── .env                      # Secrets (gitignored)
├── assets/                   # Curated game content (committed)
│   ├── quotes.json          # /play quotes
│   ├── emoji.json           # /play emoji descriptions
│   ├── taglines.json        # /play taglines
│   ├── trivia.json          # /play trivia facts
│   └── macguffins.json      # MacGuffin card definitions
└── data/                     # Persistent state (gitignored)
    ├── moviebot.db          # SQLite database
    └── bot.log              # Error log (1MB rotating)
```

---

## Environment Variables

Create `.env` in project root:

```
DISCORD_TOKEN=your_bot_token
TMDB_API_KEY=your_tmdb_v3_api_key
GUILD_ID=your_discord_server_id
PLEX_TOKEN=your_plex_token              # Optional; enables /rb9, /rent
PLEX_LIBRARY=Movies                      # Optional; default "Movies"
```

---

## Dependencies

```
discord.py>=2.3.0              # Discord API
python-dotenv>=1.0.0          # .env loading
aiohttp>=3.9.0                # Async HTTP (TMDB)
apscheduler>=3.10.0           # Scheduled jobs
pillow>=10.0.0                # Image ops (poster cropping)
plexapi>=4.15.0               # Plex library
pystray>=0.19.5               # System tray
plyer>=2.1.0                  # Desktop notifications
```

---

## Quick Reference: Adding a Command

1. Add helper functions in the appropriate module (tmdb.py, plex.py, db.py, etc.)
2. Define the command in the appropriate `cogs/*.py` file with an `app_commands` decorator
3. Add embed builder in embeds.py if needed
4. Bump version in version.py
5. Add changelog entry in CHANGELOG.md
6. Restart bot — slash commands re-sync to guild on every startup

Typical command: ~20 lines of code.

---

## Release Workflow

When shipping bot updates:

1. Update bot code, version.py, CHANGELOG.md
2. Commit and push bot repo
3. **Sync site repo** (`D:\git\Sites\sucklingsite`):
   - Update `CHANGELOG.md` with member-facing release notes
   - Update `COMMANDS.md` if commands changed
   - Commit and push site repo separately

Site is for community members (casual tone, user-facing copy) not maintainers (no implementation details, technical jargon, or database notes).

---

## Troubleshooting Checklist

### Bot doesn't start
- [ ] `.env` exists in project root
- [ ] `DISCORD_TOKEN`, `TMDB_API_KEY`, `GUILD_ID` set
- [ ] Message content intent enabled in Discord Developer Portal

### Slash commands don't appear
- [ ] `GUILD_ID` is correct (right-click server → Copy Server ID)
- [ ] Fully quit Discord and reopen (system tray → Quit)

### `/rb9` commands fail
- [ ] `PLEX_TOKEN` set and not expired
- [ ] Plex server exists on that account
- [ ] `PLEX_LIBRARY` name matches your library

### `/rent` forum errors
- [ ] Create **rental** and **recommendation** tags in forum settings
- [ ] Run `/setreviews <forum_channel>` as admin
- [ ] Bot has "create public threads" + "send messages in threads" permissions

### Rent button missing on `/suck`, `/roll`
- [ ] Film must be confirmed in Plex library (missing = no button, not broken state)

---

## Performance Notes

**TMDB caching:** Global semaphore of 8 concurrent requests prevents rate-limiting. Request deduping means simultaneous calls for the same movie share one network round-trip.

**Plex library:** Cached 1h, refresh lock prevents duplicate scans. Precomputed title index (O(1) lookup instead of O(n) scan). Cold cache warms at startup via `plex.warm_cache()`.

**Tracker:** Fetches provider lookups in batches of 8 concurrently, DB ops still serialized per movie. No sleeps between movies — global TMDB semaphore provides backpressure.

**Picker:** Cold-cache refresh drops from ~10s+ to ~2-3s with concurrent page fetching.

---

## Known Limitations

- **Six Degrees chain validation:** Fuzzy on punctuation/case, but doesn't handle name variants well (accents, last-first conventions). If a correct chain is rejected, the TMDB-listed name might differ from the common name.
- **Streaming detection:** Announces first-time digital availability only; doesn't track moves between services or additions to extra services.
- **Rent button timeout:** 2 minutes before disappearing (user can still call `/rent` command).
- **Game round timeout:** Dropdown selections and game rounds time out after 60 seconds.

---

## Links

- **GitHub:** https://github.com/rupertosandez/suckling
- **Community Site:** D:\git\Sites\sucklingsite
- **Bot local folder:** D:\git\Bots\sucklingbot

---

## Maintenance Notes

- **Database backup:** `cp data/moviebot.db data/moviebot.db.backup` periodically, especially before code changes
- **Logs:** `data/bot.log` rotates at 1MB (keeps last 3 files)
- **Update launcher:** Right-click tray icon → "check for updates now" or "update and restart"
- **Manual update:** `git pull` → restart bot process
- **.env & data/** persist across updates (gitignored)
