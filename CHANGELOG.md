# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).


## [1.2.0] — 2026-05-03

### Added
- `/rb9stats` — overall library stats (count, runtime, year range, ratings)
- `/rb9biggest` and `/rb9shortest` — longest/shortest films by runtime
- `/rb9oldest` and `/rb9newest` — oldest film by year, newest by date added
- `/rb9totalruntime` — fun "how long would it take to watch everything" stats
- `/rb9decade` — bar chart of films per decade
- `/rb9genre` — top 10 genres by count
- `/rb9randomscene` — random film + backdrop image

### Changed
- Renamed `/plex` to `/rb9` for consistency with the new stats commands

---

## [1.1.1] — 2026-05-03

### Changed
- `/giveup` now works for both `/guess` and `/six` rounds (previously only `/guess`)
- Bumped Six Degrees round duration from 3 to 4 minutes

---

## [1.1.0] — 2026-05-03

### Added
- `/six` command — Six Degrees of Separation game with two random popular actors
- `/sixleaderboard` command for the new game's separate leaderboard
- TMDB helpers: `search_person`, `get_movie_cast`, `get_popular_people`
- `six_scores` database table
- `sixdegrees.py` module with chain parsing, validation, and round state

### Notes
- Chains are validated against TMDB cast data — players submit `Actor -> Film -> Actor -> ...` chains in chat
- First valid chain wins; scoring is 5/4/3/2/1 by chain length (shorter = more points)
- Max 6 films per chain to keep validation cost reasonable

---

## [1.0.0] — 2026-05-03

First shipped version. The bot is fully featured and announced to the community.

### Added
- `/guess` difficulty rework: easy (full still, 1 point) and hard (cropped poster, 2 points)
- `/version` command and startup version log line
- `CHANGELOG.md` and `version.py` for tracking releases

### Changed
- Streaming announcements now only fire for first-time digital releases. Films that move between services or get added to additional ones no longer trigger announcements.

---

## [0.9.0] — Pre-release: announcement filtering & Plex

### Added
- `announced_movies` table to track films that have ever been announced as streaming
- First-announce-aware run logic: existing streaming films get silently baselined on first run after the update
- `/plex` command — random pick from the configured Plex library, using plex.tv relay for remote access
- `PLEX_TOKEN` and `PLEX_LIBRARY` environment variables (both optional)

### Changed
- `_check_movie_providers` now returns whether a movie is currently streaming, used for baselining

---

## [0.8.0] — Pre-release: toggles and quality-of-life

### Added
- `/toggle` command for enabling/disabling streaming announcements and daily recommendations
- Lightweight error logging to `data/bot.log` with auto-rotation (1 MB cap, 3 backups)
- Try/except wrappers around scheduled jobs and `on_message` for crash visibility

---

## [0.7.0] — Pre-release: more guessing modes

### Added
- Movie still guessing — `/guess type:still` pulls a random backdrop instead of cropping a poster
- Combined `/guess` command with optional `type` and `difficulty` overrides; defaults to random for both
- TMDB `get_movie_images` and `pick_backdrop_url` helpers
- `make_still_puzzle` in `imageops.py` with mode-appropriate difficulty mapping

### Changed
- Guess rounds dynamically pick poster vs still, with a fallback to poster if no clean backdrop is available

---

## [0.6.0] — Pre-release: poster guessing game

### Added
- `/guess`, `/giveup`, `/leaderboard` commands
- Poster cropping/blurring via Pillow with three difficulty levels
- `game.py` for in-memory round state with channel-scoped active rounds
- `guess_scores` table for persistent leaderboard
- `imageops.py` for image manipulation

### Changed
- `on_message` listener wired up to detect correct guesses in active rounds

---

## [0.5.0] — Pre-release: random picks and daily recommendations

### Added
- `/roll` command for random horror picks with optional decade and runtime filters
- Daily horror recommendation scheduled at noon, posting to a configurable channel
- `/setdaily` admin command for configuring the daily-rec channel
- `/dailynow` admin command for manual triggering
- `picker.py` with cached candidate pool of ~1000 popular horror films
- `daily_recs` table with 30-day no-repeat exclusion logic

### Changed
- Apscheduler now runs two jobs: 9 AM streaming check + 12 PM daily recommendation

---

## [0.4.0] — Pre-release: tracking and announcements

### Added
- `/track`, `/untrack`, `/tracked` commands for the community watchlist
- `/setannouncements` admin command for configuring the alerts channel
- `/checknow` (dry-run) and `/checknowlive` (live) admin commands
- Daily 9 AM streaming-availability scan via apscheduler
- `tracker.py` with first-run baselining and structured `CheckResult` summaries
- Auto-availability check on `/track`: if the film is already streaming, the bot says so immediately and links to where; otherwise it confirms tracking
- Provider snapshot recording on `/track` to prevent the daily job from re-announcing already-streaming films
- TMDB watch-providers caching and selective bypass with `force=True`
- In-memory cache layer (`cache.py`) with 6-hour TTL
- `/cachestats` admin command

---

## [0.3.0] — Pre-release: persistence

### Added
- SQLite database at `data/moviebot.db`
- `db.py` with schema, helpers, and idempotent `init_db`
- `tracked_movies`, `provider_snapshots`, and `config` tables

---

## [0.2.0] — Pre-release: full availability info

### Added
- "Where to watch" section in `/watch` embeds, showing both theatrical and digital availability
- TMDB release-dates endpoint integration for accurate per-region theatrical and digital dates
- Disambiguation dropdown for ambiguous titles via `discord.ui.View`
- Optional `year` parameter on `/watch` for forcing a specific match
- Popularity-weighted search results (overrides TMDB's default sort)

### Changed
- Embed formatting cleaned up; em-dash status indicators replaced with text descriptors

---

## [0.1.0] — Pre-release: initial bot

### Added
- `/ping` health check
- `/watch` lookup command with TMDB integration
- Discord bot scaffolding with guild-scoped slash command syncing
- TMDB API wrapper (`tmdb.py`)
- Discord embed builders (`embeds.py`)
- `.env`-based secret management
- Project structure with virtual environment and requirements
