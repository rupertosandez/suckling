# Sucklingbot Expected Behavior Reference

**Last updated:** June 16, 2026
**Current version:** 2.7.5
**Purpose:** reference document for a developer health audit

This document describes how the bot should work when healthy. It is written as an audit baseline rather than a marketing overview. If code behavior disagrees with this document, the audit should decide whether the code is broken, the documentation is stale, or the product expectation needs to change.

## Product Role

Sucklingbot is a Discord bot for the Return by 9 movie community. It should help members:

- look up films and availability
- get random movie recommendations
- browse and rent films from the RB9 Plex library
- return rentals with reviews and late-fee tracking
- maintain private watchlists
- track films for first-time digital streaming alerts
- connect Letterboxd accounts and browse group activity
- play movie guessing, trivia, and Six Degrees games
- earn achievements and collect MacGuffins

The bot is intentionally community-facing and playful, but the runtime should be boring: predictable startup, one active process, no duplicate scheduled posts, durable state, and clear admin recovery paths.

## Runtime Model

The normal production runtime is the Windows tray launcher:

- `launcher.py` starts and supervises `bot.py`.
- `launch.vbs` starts the launcher without a terminal.
- `launch.bat` starts the launcher with a visible terminal for troubleshooting.
- The launcher should prevent duplicate launcher instances.
- `bot.py` should prevent duplicate bot processes with `data/bot.instance.lock`.
- `bot.py` writes `data/bot.pid` while running and clears it during clean shutdown.
- `/restart` should restart through the launcher when launcher-managed, or exec the current Python process when run manually.

On startup, `bot.py` should:

- configure logging
- initialize the database schema
- load MacGuffin and trivia assets
- register persistent public film-card buttons
- load all cogs listed in `COG_EXTENSIONS`
- sync slash commands to the configured guild
- warm the Plex cache in the background when Plex is configured
- post the current version update announcement once, if configured and not already posted
- start APScheduler jobs only once

On shutdown, the bot should:

- stop scheduled jobs
- close shared HTTP sessions for TMDB, Letterboxd, and cleanup checks
- clear its pid file
- release process resources without leaving a second process logged into Discord

## Required Configuration

Configuration is loaded from `.env` in the project root by `config.py`.

Required:

- `DISCORD_TOKEN`
- `TMDB_API_KEY`
- `GUILD_ID`

Optional:

- `DATABASE_URL`: if set, use Postgres; otherwise use local SQLite at `data/moviebot.db`
- `PLEX_TOKEN`: enables RB9 library commands and rentals
- `PLEX_LIBRARY`: Plex library name, default `Movies`
- `BOT_TIMEZONE`: server fallback timezone for rentals, default `America/Los_Angeles`
- `SUCKLINGBOT_DATA_DIR`: override data folder
- `SUCKLINGBOT_ASSETS_DIR`: override assets folder
- `ACHIEVEMENT_CATALOG_URL`: URL used by `/achievementcatalog`
- `TAUTULLI_URL` and `TAUTULLI_API_KEY`: enable Plex cleanup and unpopularity audit helpers
- `PLEX_CLEANUP_ENABLED`: enables scheduled cleanup posts when truthy

The bot should fail fast if required env vars are missing. Plex, Tautulli, and catalog settings are optional; missing optional services should degrade only their related features.

## External Systems

### Discord

The bot uses `discord.py` slash commands and message content intent.

Expected permissions vary by feature:

- General commands: send messages, embed links, attach files where game images are posted
- Forum rentals: create public threads and send messages in threads
- Achievement display roles: manage roles, with the bot's role above badge roles
- FAQ posting: send messages, embed links, create public threads, send messages in threads
- Admin setup commands: the invoking admin needs Manage Server

Member-facing commands should generally avoid leaking tracebacks. Admin commands may return concise operational errors.

### TMDB

TMDB powers movie lookup, recommendations, watch providers, game images, Six Degrees validation, and cleanup provider checks.

Expected behavior:

- API calls are async and cached where freshness is not required.
- Tracking checks should bypass cache for provider freshness.
- Provider lookups use the US region unless explicitly changed in code.
- If TMDB is unavailable, affected commands should return clear user-facing errors without crashing the bot.

### Plex

Plex powers RB9 library commands, rent buttons, random rentals, and library metadata achievements.

Expected behavior:

- If `PLEX_TOKEN` is not set, non-Plex features still work.
- Plex API calls should not block the event loop; synchronous Plex work is wrapped off-thread.
- The library cache persists in the database and supports fast reads.
- The bot performs hourly incremental cache refreshes and a weekly full refresh.
- `/plexrefresh` performs a full refresh on demand.
- Rent buttons appear only when a film is confirmed available in the RB9 library.

### Letterboxd

Letterboxd powers linked accounts, profiles, watchlists, group activity, taste checks, and optional activity auto-posts.

Expected behavior:

- `/lb link` validates that the username exists and is public before saving.
- Private or missing Letterboxd accounts should produce friendly errors.
- `/setlbactivity` and enabling Letterboxd activity should seed current feed entries first so old diary entries do not flood the channel.
- Scheduled activity posts should only post recent unseen entries, with compact posts for large single-member batches.

### Tautulli

Tautulli powers Plex cleanup and unpopularity audit commands.

Expected behavior:

- Missing Tautulli config should affect only cleanup-related commands/jobs.
- Monthly cleanup should run only when `PLEX_CLEANUP_ENABLED` is truthy.
- Cleanup posts should use the configured announcement channel.

## Persistence

The bot supports SQLite and Postgres through `db.py`.

Expected behavior:

- If `DATABASE_URL` is set, use Postgres.
- If `DATABASE_URL` is absent, use SQLite at `config.DB_PATH`.
- Schema creation happens automatically on startup.
- SQLite migrations should preserve existing data by adding missing columns when needed.
- Postgres schema should be kept functionally equivalent to SQLite schema.
- In-memory caches, active game rounds, and in-progress UI view state may be lost on restart.
- User data, rentals, achievements, MacGuffins, tracked films, watchlists, and channel settings must persist.

Important tables:

- `config`: channel IDs, feature toggles, forum tag IDs, last announced version
- `tracked_movies`: server-wide streaming tracking list
- `provider_snapshots`: provider names already seen per tracked movie (digital rent/buy names carry a `digital:` prefix)
- `announced_movies`: TMDB IDs already announced for subscription streaming
- `announced_digital`: TMDB IDs already announced for digital rent/buy availability
- `daily_recs`: daily recommendation history and 30-day no-repeat window
- `guess_scores`: shared `/guess` and `/play` leaderboard
- `six_scores`: Six Degrees leaderboard
- `lb_accounts`: Discord user to Letterboxd username links
- `lb_activity_seen`: diary entries already seeded or posted
- `watchlist`: private per-user watchlists
- `macguffins`: globally unique MacGuffin ownership
- `macguffin_free_claims`: one free claim per user
- `user_timezones`: rental due-date timezone overrides
- `rentals`: rental lifecycle, ratings, recommendations, fees, notifications, thread IDs
- `plex_library_cache`: persisted RB9 library snapshot and metadata
- `achievement_earned`: unlocked achievements
- `achievement_display`: up to 3 displayed badges per member
- `achievement_roles`: Discord role IDs for achievement badges
- `achievement_events`: lightweight progress events not stored elsewhere

## Scheduled Jobs

`bot.py` schedules these jobs after the bot is ready:

- 9:00 AM local time: streaming availability check
- 12:00 PM local time: daily recommendation
- hourly: rental overdue notices and 12-hour reminders
- hourly: Letterboxd activity check
- monthly on day 1 at 10:00 AM: Plex cleanup check
- hourly: Plex incremental cache refresh
- Sunday at 4:00 AM: Plex full cache refresh

Expected scheduler behavior:

- Jobs should not be registered more than once per process.
- Streaming, daily recommendation, and Letterboxd activity should respect runtime toggles.
- Manual admin triggers should run even when scheduled toggles are off, where the command design says so.
- Scheduled exceptions should be logged and should not kill the bot.

## Feature Expectations

### Lookup and Discovery

`/suck <title> [year]`

- Searches TMDB.
- If no result exists, reports no result.
- If multiple plausible matches exist and no year is supplied, shows a dropdown.
- If a year is supplied or no disambiguation is needed, posts a movie embed with details and US watch providers.
- Adds a film-card view with `+ watchlist`.
- Adds `rent this` only when Plex availability is confirmed.

`/roll [decade] [runtime]`

- Picks from the random candidate pool.
- Supports decade text like `1980s`.
- Supports runtime choices: short, medium, long.
- Posts a movie recommendation with providers.
- Uses the same film-card actions as `/suck`.
- Should return a friendly message if filters are too narrow.

Daily recommendation:

- Posts to the configured daily channel at noon if enabled.
- Avoids movies used in the last 30 days.
- Records successful posts in `daily_recs`.
- Includes `rent this` when the selected film is in Plex.

### RB9 Library

RB9 commands should read from Plex/cache and fail gracefully if Plex is unavailable.

Commands:

- `/rb9`: random library film with poster and rent button
- `/rb9randomscene`: random film plus backdrop and rent button
- `/rb9stats`: overall count/runtime/year/rating summary
- `/rb9biggest`: longest film
- `/rb9shortest`: shortest film, excluding very short entries under the configured logic
- `/rb9oldest`: oldest by release year
- `/rb9newest`: most recently added
- `/rb9totalruntime`: total runtime estimates
- `/rb9decade`: decade breakdown
- `/rb9genre`: top genres

Expected behavior:

- Library reads should be fast after cache warmup.
- Missing runtime/year/art data should produce sensible empty states, not crashes.
- `/plexrefresh` should refresh the cache and report movie count.

### Rentals

Rentals are the official watched-film record for achievements.

`/rent`

- Member can have at most 3 active rentals.
- Opens an ephemeral path picker:
  - roll random
  - pick a movie
  - ask an admin
- Random rentals allow up to 2 rerolls.
- On the second reroll, the third film is locked automatically.
- Films the user has ever rented are excluded from future random offers, regardless of status.
- Confirmed rentals create a database row and should create/update the review forum thread when configured.
- Due date is 9:00 PM on the fifth day in the user's saved timezone, or the server default timezone.

`/timezone [timezone_name] [clear]`

- With no options, reports the saved timezone or fallback timezone.
- With `timezone_name`, validates IANA timezone names and saves them.
- With `clear`, removes the saved timezone.

`/return`

- Opens an ephemeral private flow.
- If one active rental exists, goes directly to watched/unwatched choice.
- If multiple active rentals exist, asks which rental to return.
- Watched return requires `recommended` yes/no and accepts optional rating 1-10 and thoughts.
- Watched return marks status returned, calculates late fee, edits forum thread, posts review content, may drop a MacGuffin, and awards achievements.
- Unwatched return marks status returned_unwatched, calculates late fee, edits forum thread, and does not post a review, drop a MacGuffin, or award watched-rental achievements.

`/extend [rental]`

- Extends an active rental by 24 hours.
- Each rental can be extended once.
- If the user has multiple active rentals, rental ID or title fragment should disambiguate.
- Reminder and overdue flags should reset appropriately after extension.

`/myrental`

- Shows active rental status privately.
- If multiple rentals are active, shows a list.

`/latefees`

- Shows users with accumulated cosmetic late fees.
- Late fee calculation is $1 per day or partial day overdue.

`/rentalstats [user]`

- Shows rental history, active status, totals, late counts/fees, ratings, and paginated history.

Admin rental commands:

- `/setreviews <forum_channel>` stores the review forum and detects `rental`, `recommendation`, and `review` tags.
- `/setrentalrequests <channel>` stores where recommendation requests post.
- `/cancelrental @user [rental] [reason]` cancels one active rental, updates thread, and DMs the user.
- `/assignrental @user <title> [year]` assigns a specific RB9 film, creates the rental, creates the forum thread, and DMs the user.

Audit risk areas:

- duplicate active rentals
- reroll exclusion logic
- timezone edge cases around DST
- forum permission failures
- late fee boundary conditions
- thread/message IDs after failed forum creation
- watched vs unwatched achievement/MacGuffin side effects

### Streaming Tracking

`/track <title> [year]`

- Searches TMDB and disambiguates when needed.
- Adds the movie to the server tracked list.
- If already available, replies immediately with availability (subscription streaming, or rent/buy on a digital store).
- Otherwise, the movie is monitored for both subscription streaming and digital rent/buy availability.
- Awards tracking-related achievements.

`/untrack <title>`

- Removes a single matching tracked film.
- If multiple tracked films match, asks for a more specific title.

`/tracked`

- Shows up to 25 tracked films and who added them.

Scheduled streaming check:

- Runs at 9:00 AM when enabled.
- Uses fresh TMDB provider data.
- Compares providers against `provider_snapshots` (subscription names stored bare; digital rent/buy names stored with a `digital:` prefix so the two tiers can't collide).
- Announces availability to the configured announcements channel as two separate tiers:
  - Subscription streaming (TMDB `flatrate`) for all candidates (tracked films + the Discover horror pool). Gated by `announced_movies`.
  - Digital rent/buy (TMDB `rent` + `buy`) for tracked films only, to keep volume sane. Gated by `announced_digital`.
- Because the tiers are gated independently, a movie that first appears on a digital store still gets its own subscription announcement later (preserving the Shudder alert).
- On first exposure of either tier, currently-available films are baselined silently instead of dumping the back catalog.
- Should not announce normal movement between streaming services as a new release.

Admin setup and triggers:

- `/setannouncements <channel>`
- `/toggle streaming announcements`
- `/checknow` dry run
- `/checknowlive` live post

### Personal Watchlist

`/watchlist show`

- Shows the member's private watchlist ephemerally.
- Paginates 10 per page.
- Includes controls to remove items from the current page.
- Includes roll-from-list behavior.

`/watchlist add <title> [year]`

- Searches TMDB.
- Disambiguates when multiple plausible matches exist.
- Saves title/year/TMDB ID/poster.
- Avoids obvious duplicates for the same user.
- Awards watchlist-related achievements.

`/watchlist remove <title>`

- Removes all entries matching the title fragment for that user.
- Records removal events for achievement progress.

Film-card `+ watchlist` actions should add to this same private watchlist.

### Letterboxd

Commands:

- `/lb link <username>`
- `/lb unlink`
- `/lb profile [user] [username]`
- `/lb watchlist [user] [username]`
- `/lb group`
- `/lb tastecheck [a_user] [b_user] [a_username] [b_username]`

Expected behavior:

- Linked account commands should be private when they mutate account state.
- Profile and watchlist commands should work for linked Discord users or raw public Letterboxd usernames.
- Watchlist browsing should show 5 films per page.
- Watchlist controls should support rolling from the Letterboxd list and importing to the private bot watchlist.
- Group activity should aggregate linked accounts and tolerate failures for individual accounts.
- Tastecheck should compare recent diary overlap and shared public watchlist items where available.

Letterboxd activity posting:

- `/setlbactivity <channel>` sets the channel, seeds current entries, and enables posting.
- `/toggle letterboxd activity` should seed before enabling.
- `/lbactivitynow [post]` should dry-run by default.
- Scheduled posts should ignore stale unseen entries outside the posting window and mark skipped/stale entries as seen.

### Games

Only one active round of a given game type should exist per channel, and games should avoid conflicting active rounds where implemented.

`/guess [difficulty]`

- Difficulty is easy, hard, or random when omitted.
- Easy uses a full movie still and awards 1 point.
- Hard uses a cropped poster puzzle and awards 2 points.
- Round lasts 60 seconds.
- First message in the channel matching the film title wins.
- Points go to `guess_scores`.
- Fast wins within 10 seconds record speedrun events.
- `/leaderboard` shows top guess/play scorers.

`/play`

- Picks one trivia roulette entry from quote, emoji, tagline, or trivia assets.
- Round lasts 30 seconds.
- First matching chat answer wins 1 point.
- Shares `guess_scores` leaderboard with `/guess`.
- Records trivia achievement events.

`/six`

- Picks two popular actors.
- Round lasts 4 minutes.
- Players submit chains like `Actor -> Film -> Actor -> Film -> Actor`.
- Chain validation uses TMDB cast data.
- First valid chain wins.
- Points are based on number of films in the chain:
  - 1 film: 5 points
  - 2 films: 4 points
  - 3 films: 3 points
  - 4 films: 2 points
  - 5 or more films: 1 point
- Points go to `six_scores`.
- `/sixleaderboard` shows top Six Degrees scorers.

`/giveup`

- Ends the active `/guess`, `/play`, or `/six` round in the channel and reveals/ends normally.

Audit risk areas:

- message listener conflicts
- stale active rounds after exceptions
- fuzzy title matching false positives/false negatives
- score write failures being hidden
- image download/processing failures

### MacGuffins

MacGuffins are globally unique collectible movie objects loaded from `assets/macguffins.json`.

Commands:

- `/claimguffin`: one free starter claim per member
- `/myguffins`: private paginated collection
- `/giftguffin @user <card>`: transfer one owned card to another member
- `/adminguffins <action> @user [card]`: admin view/add/remove/random

Expected behavior:

- A MacGuffin can have only one owner at a time.
- A member can use `/claimguffin` once.
- Claims should be locked per user to avoid double-claim races.
- Gifts should reject bots and self-gifts.
- Partial card names are allowed when unambiguous.
- Public drop/gift messages should post in-channel.
- Watched rental returns can drop a MacGuffin.
- Randomly rolled rentals use boosted rare/iconic drop odds.
- Pool exhaustion should be handled gracefully.

### Achievements

Achievements are defined and evaluated in `achievements.py`.

Commands:

- `/achievements [user]`
- `/achievementdisplay <achievement> [replace]`
- `/achievementhide <achievement>`
- `/achievementclear`
- `/achievementboard`
- `/setfeed <channel>`
- `/achievementcatalog <channel>`
- `/achievementrescan [user]`
- `/achievementsyncroles <user>`
- `/achievementrefreshfeed [limit]`

Expected behavior:

- Members can unlock many achievements.
- Members can display up to 3 earned achievements as Discord roles.
- Displayed achievement roles should be created/reused and synced to the member.
- The bot should never try to assign roles above its own highest role.
- `/achievements` with no user should be private and include progress hints.
- `/achievements @user` should show another member's public shelf.
- Unlock announcements post to the configured Suckling feed.
- Rescans should award missing achievements from existing bot history and post new unlocks when possible.
- Role sync should repair visible badge roles after manual Discord edits or permission fixes.
- Catalog posting should link to the configured public achievement catalog URL.

Primary achievement data sources:

- returned watched rentals
- Plex library metadata cache
- game score tables
- MacGuffin ownership and gift events
- tracked films and watchlist events
- Letterboxd links
- lightweight `achievement_events`

### Admin and Operations

Admin commands should require Manage Server unless they are intentionally public.

Commands:

- `/botstatus`: version, uptime, latency, cache size, channels, toggles, counts, warnings
- `/lblinked [page]`: linked Letterboxd accounts
- `/version`: current version
- `/postupdate`: post the current version announcement
- `/restart`: restart process
- `/toggle <feature> <enabled>`: streaming announcements, daily recommendation, Letterboxd activity
- `/checknow`: dry-run streaming check
- `/checknowlive`: live streaming check
- `/dailynow`: post today's daily recommendation
- `/postfaq <channel> [thread_name]`: create FAQ thread
- `/lbactivitynow [post]`: dry-run or live Letterboxd activity check
- `/plexcleanupnow [post]`: dry-run or live cleanup check
- `/plexrefresh`: refresh RB9 Plex cache
- `/plexunpopular`: low-watch, low-rated Plex audit
- `/cachestats [clear]`: inspect or clear in-memory cache and refresh roll pool

Expected behavior:

- Admin setup commands validate bot permissions before saving channel config.
- Manual posting commands return private summaries.
- Commands that can post publicly default to dry-run where designed.
- `/botstatus` should surface missing channel setup and active/overdue rental counts.

## UI and Interaction Expectations

Discord UI components live mainly in `views.py`.

Expected behavior:

- User-specific ephemeral views should reject interactions from other users.
- Public film-card buttons should persist across restarts where registered as persistent views.
- Timed views should fail closed with a friendly "run the command again" experience.
- Film cards from `/suck`, `/roll`, `/rb9`, `/rb9randomscene`, daily recommendations, and Letterboxd/watchlist rolls should share consistent actions.
- Buttons should not create duplicate rentals or duplicate watchlist entries when clicked repeatedly.
- Direct rent buttons should skip rerolls because the user intentionally picked that film.

## Error Handling and Logging

`logger.py` writes warnings/errors to `data/bot.log` with rotation.

Expected behavior:

- Scheduled job failures are logged and printed, then the bot continues.
- User-facing errors should be concise and not expose stack traces.
- Missing optional services should not break unrelated commands.
- Network failures should be handled at feature boundaries.
- Cache clearing should not require restart.

## Data That Must Not Be Lost

For a production audit, treat these as critical:

- `.env`
- `data/moviebot.db` when using SQLite
- Postgres database when using `DATABASE_URL`
- `assets/*.json` curated content
- `data/bot.log` and rotated logs when diagnosing incidents

The code repo itself is recoverable from Git. The database is the source of truth for community history.

## Healthy Behavior Checklist

A healthy deployment should satisfy the following:

- Bot starts once and syncs slash commands to the configured guild.
- `/info` or another lightweight command responds quickly.
- `/botstatus` shows expected channels and no surprise warnings.
- TMDB lookup works through `/suck`.
- `/roll` can produce a recommendation and respects filters.
- Plex-backed `/rb9` works when `PLEX_TOKEN` is configured.
- Rent buttons appear only for Plex-available films.
- `/rent` enforces the 3-active-rental limit.
- `/return` correctly separates watched and unwatched flows.
- Rental forum threads are created/edited when configured.
- Hourly rental reminders and overdue notices are sent only once per rental state.
- `/track` adds films and the scheduled checker does not duplicate announcements.
- `/watchlist` actions are private and per-user.
- Letterboxd link/profile/watchlist work for public accounts.
- Letterboxd activity enabling seeds before posting.
- `/guess`, `/play`, and `/six` start, score, reveal, and clean up active rounds.
- MacGuffin claims/gifts enforce uniqueness.
- Achievement unlocks persist and displayed roles sync.
- Scheduled posts respect toggles.
- Manual admin dry-runs report useful summaries.
- Logs contain actionable errors without runaway noise.

## High-Value Audit Targets

Prioritize these areas during a health audit:

- SQLite/Postgres parity, especially conflict handling and auto-increment IDs.
- Race conditions around rental creation, MacGuffin claims, and button double-clicks.
- Timezone and late-fee math around daylight saving transitions.
- Scheduler duplication after reconnects or restarts.
- Permission failure handling for forums, threads, roles, and channel posts.
- Plex cache refresh correctness after deleted, renamed, or newly added titles.
- Provider snapshot behavior for streaming announcements.
- Letterboxd activity seeding and stale-entry skipping.
- Achievement rescan idempotency and role sync idempotency.
- Graceful degradation when TMDB, Plex, Letterboxd, Discord, or Tautulli fails.
- User-visible command copy staying member-friendly while errors remain actionable.

## Release Workflow Expectations

When shipping bot updates:

- Update bot code and any internal docs/changelog as needed.
- Update `version.py` according to semver.
- Update this repo's `CHANGELOG.md`.
- If member-facing commands or behavior changed, update this repo's `COMMANDS.md`.
- Keep the public GitHub Pages site in sync:
  - `D:\git\Sites\sucklingsite\CHANGELOG.md`
  - `D:\git\Sites\sucklingsite\COMMANDS.md` when commands changed
- Commit and push the bot repo and site repo separately.

Site copy should stay member-facing: casual lowercase voice, no implementation details, and no internal architecture notes.

## File Map

Core runtime:

- `bot.py`: bot startup, cog loading, scheduler, message listener, shutdown/restart
- `config.py`: `.env` loading and path/env constants
- `db.py`: SQLite/Postgres schema and persistence helpers
- `logger.py`: rotating file logging
- `version.py`: current version

Feature cogs:

- `cogs/discovery.py`: `/suck`, `/roll`, daily recommendations
- `cogs/rb9.py`: RB9 Plex library commands
- `cogs/rentals.py`: rental member/admin commands
- `cogs/tracking.py`: tracking setup, `/track`, `/untrack`, `/tracked`
- `cogs/watchlist.py`: private watchlist commands
- `cogs/letterboxd.py`: `/lb` command group
- `cogs/games.py`: `/guess`, `/play`, `/six`, leaderboards, `/giveup`
- `cogs/macguffins.py`: MacGuffin commands
- `cogs/achievements.py`: achievement shelf, feed, roles, admin repair
- `cogs/admin.py`: admin dashboard, toggles, manual jobs, FAQ, cache tools
- `cogs/meta.py`: public bot info

Service modules:

- `tmdb.py`: TMDB API wrapper, caching, provider/image helpers
- `plex.py`: Plex API/cache, library selection, stats, availability
- `rental.py`: rental lifecycle, due dates, forum threads, reminders, extensions
- `tracker.py`: streaming availability scanner
- `picker.py`: random movie pool and filters
- `letterboxd.py`: Letterboxd scraping/fetching
- `achievements.py`: achievement registry/evaluation/feed/role sync
- `macguffin.py`: card loading, drops, transfers
- `cleanup.py`: Tautulli cleanup and unpopularity audits
- `game.py`: `/guess` round state
- `trivia_roulette.py`: `/play` round state and asset loading
- `sixdegrees.py`: `/six` round state and validation
- `imageops.py`: image download/cropping
- `cache.py`: in-memory TTL cache
- `embeds.py`: Discord embed builders
- `views.py`: Discord buttons, dropdowns, modals, paginated views
- `update_announcements.py`: update announcement posting

Launcher:

- `launcher.py`: launcher entry point
- `launcher/`: tray UI, child process management, update checks, state

Assets and docs:

- `assets/*.json`: curated trivia/MacGuffin/game data
- `assets/*.png` and `assets/*.ico`: bot/launcher imagery
- `README.md`: setup and operator overview
- `COMMANDS.md`: member-facing command reference
- `CHANGELOG.md`: bot release history
- `AGENTS.md`: Codex/release workflow notes
- `PROJECT_OVERVIEW.md`: this audit reference

## Known Limitations

- Streaming announcements are first-time digital availability alerts, not full provider-move tracking.
- Six Degrees matching depends on TMDB names and cast data; name variants may fail.
- Discord role assignment is limited by the bot's role hierarchy.
- Runtime UI views and active game rounds do not survive restart unless explicitly registered as persistent public views.
- Letterboxd scraping can break if Letterboxd markup changes.
- Plex cleanup/unpopularity commands require Tautulli and are best treated as operational aids, not deletion automation.
