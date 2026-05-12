# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.3.0] — 2026-05-12

### Added
- `/play` — trivia roulette game. Bot randomly picks a category (quote, emoji, tagline, or trivia) and posts a clue. First correct guess in chat wins. 30-second rounds, 1 point per win.
- `trivia_roulette.py` module with round state, JSON asset loading, and fuzzy answer matching
- `assets/` folder containing curated content per category (`quotes.json`, `emoji.json`, `taglines.json`, `trivia.json`)
- `trivia_prompt_embed` and `trivia_reveal_embed` builders in `embeds.py`

### Changed
- `/play` writes to the shared `guess_scores` table — the existing `/leaderboard` command now reflects wins from both `/guess` and `/play`
- `/giveup` extended to also end active `/play` rounds
- `/guess` and `/six` now refuse to start if a `/play` round is active in the channel (and vice versa)

### Notes
- Category content lives in `assets/*.json` and is loaded once at startup. Missing or malformed files log a warning and are skipped; the bot keeps running with whatever categories are populated. If no categories load, `/play` refuses with a friendly message.
- Each category has its own embed color and emoji badge so the "roulette" reveal feels distinct.
- No no-repeat tracking yet — with 70+ entries per category, immediate repeats are rare. Easy to add later if it becomes annoying.

---

## [1.2.4] — 2026-05-11

   ### Changed
   - Renamed `/watch` to `/suck` to better suit the bot's vibe. Functionality is unchanged - same search, same disambiguation dropdown, same availability info (theatrical, streaming, Plex library). Command description also updated to "suck up a movie and see where to watch it".

   ---
   
## [1.2.3] — 2026-05-10

### Changed
- TMDB calls now reuse a single shared `aiohttp.ClientSession` instead of opening a new one per request. Improves connection pooling, DNS caching, and keepalive — biggest impact on `/checknow` and the daily streaming scan, which previously opened hundreds of sessions per run. `picker._fetch_pool` and `imageops.download_image` also use the shared session.
- Internal: `tmdb._get` no longer takes a `session` parameter. Callers in `picker.py`, `tracker.py`, and `imageops.py` updated accordingly.
- Replaced deprecated `datetime.utcnow()` with `datetime.now(timezone.utc)` throughout. Forward-compatible with Python 3.12+ (which warns on `utcnow`). Stored ISO timestamps now include a `+00:00` suffix; reads via `datetime.fromisoformat` handle both old and new formats.

### Fixed
- `/roll` with a runtime filter would silently return a film that violated the filter when no candidates matched (e.g. `runtime:short` could hand back a 3-hour movie). Now caps the search at 30 candidates and returns "couldn't find anything matching those filters" honestly when nothing matches.

### Notes
- No database migration needed. Existing rows with naive ISO timestamps continue to read correctly.
- Bot subclass `SucklingBot` now closes the shared TMDB session on shutdown to avoid "Unclosed client session" warnings.

---

## [1.2.2] — 2026-05-10

### Added
- `/watch` now indicates whether the film is in the Return by 9 plex library
- When a film isn't in the library, the embed includes a small "request it" link that deep-links to the seerr instance for that film

### Notes
- Plex check is graceful: if `PLEX_TOKEN` isn't configured or the lookup fails, the rb9 line is omitted rather than risk a false "not in the library"
- Title matching strips articles, punctuation, and case. Prefers a title+year match before falling back to title-only

---

## [1.2.1] — 2026-05-03

### Changed
- `/rb9genre` now counts each film once by its primary (first) genre tag instead of incrementing all genre tags. Counts now sum to the total film count, giving a cleaner "primary genre" breakdown.

---

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