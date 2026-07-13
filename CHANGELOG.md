# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [2.10.3.3] - 2026-07-14

### Fixed

- Collection posters weren't loading on the portal's curation page. `plex.py`'s `_absolute_url()` appended `?X-Plex-Token=...` unconditionally, but Plex's auto-generated "composite" collection thumbs already carry their own `?width=...&height=...` query string - the result had two `?` characters, so the token got silently swallowed as part of the `height` value instead of being parsed as a real parameter, and Plex rejected the request. Now appends with `&` when the path already has a query string.

## [2.10.3.2] - 2026-07-13

### Added

- Syncs real Plex Collection metadata (title, description, poster, curated item order) into new `plex_collections_cache`/`plex_collection_items` tables, piggybacking on the existing hourly Plex sync and startup cache warm - no new schedule. Backend-only; powers the web portal's upcoming "curation" section, no new bot commands.

## [2.10.3.1] - 2026-07-11

### Fixed

- Additive column migrations (`_ensure_column`) now run on the Postgres path at startup, not just SQLite. Previously `init_db()` returned before reaching them on Postgres, so a column added to an already-deployed Postgres database would never be applied in place. No effect on current schemas (all migrated columns already exist in both dialects); this closes the gap for the next column added.

## [2.10.3] - 2026-07-10

### Fixed

- General latency pass across the bot: `/suck`, `/roll`, and the daily rec now fetch TMDB details and watch providers concurrently instead of sequentially. The `/rent` confirm flow and `/return`'s forum-thread editing (previously ~10 sequential blocking Postgres round trips per click) now run entirely off the event loop, along with achievement role sync and event recording. `/lb group` now fetches each member's Letterboxd diary in parallel instead of one at a time. Collapsed 3 separate forum-tag lookups into one query (`db.get_rental_forum_tag_ids`).
- Plex connection resilience: caches the last-working server address and retries it directly before falling back to full multi-candidate resolution, cut the per-candidate connect timeout from 15s to 6s, and quieted plexapi's internal per-URI error spam in favor of one clean warning line.

## [2.10.2] - 2026-07-10

### Fixed

- Guessing games (`/play` trivia and `/six` degrees) waited for the full achievement-award pass - up to ~100 sequential DB queries, including a full Plex library re-fetch and JSON re-parse for ~60 "rb9 library" achievements - before posting the winner reveal. Reveal now posts immediately after the score is saved; achievement awarding runs in the background. `/guess` already worked this way.
- `evaluate_user()` (the achievement scan) now caches a user's rental history, the Plex library lookup, and macguffin inventory for the duration of one evaluation instead of re-fetching per achievement, cutting DB round trips per evaluation from ~100+ to 3. Also speeds up `/achievementrescan`, which runs the same scan per user.

## [2.10.1] - 2026-07-10

### Fixed

- `/achievementboard` was crashing on every use with a Postgres `GroupingError` - the top-collectors query selected `user_tag` without grouping or aggregating it, which SQLite tolerates but Postgres rejects. Fixed the same latent bug in the late-fees leaderboard query before it could surface there too.
- `/achievements` embed decluttered: a progress bar up top, pinned badges trimmed to name-only, recent unlocks cut to 3, the sprawling "other earned" badge list replaced with a count, and "next up" hints removed - replaced by a "View Full Shelf" link to your badges tab on the member portal.

## [2.10.0] - 2026-07-09

### Added

- `/guffinhistory <card>`: see a MacGuffin's ownership trail - claims, gifts, admin moves, and removals, in order. Backed by a new `macguffin_events` log that records every future ownership change (existing cards start with an empty trail; history isn't retroactive).
- Weekly community recap: every Sunday at 11am, Suckling posts a rundown to the feed channel - top renters, new MacGuffin pulls, new achievement unlocks, and the current `/guess` and `/six` leaders. Toggle with `/toggle feature:weekly recap`, or trigger manually with `/recapnow`.

### Changed

- `/myrental` now shows full detail (forum thread link, overdue flag, extension status) for every active rental, not just when you have exactly one. Previously, members with 2-3 active rentals got a stripped-down list missing the thread link and overdue warning.
- `/achievements` now clearly separates pinned badges, other earned badges (previously invisible once you had more than 5 unpinned), and progress toward the next ones - instead of blending recent unlocks and progress hints together.
- MacGuffins moved by an admin via `/adminguffins add` are now labeled `admin` instead of `gift` in ownership records, so provenance history reads correctly.

## [2.9.1.2] - 2026-07-09

### Changed

- Scheduled jobs no longer log a spurious "missed" warning for the routine ~1 second of execution jitter every hour; the warning now only fires for a genuinely missed run.
- Added a first pytest suite covering the SQLite/Postgres dialect helpers and rental late-fee/due-date math (including a DST-transition case), wired into `scripts/prerelease_check.py`.
- Repo housekeeping: removed the fully-merged `codex/refactor-bot-cogs` branch/worktree and a stale local database backup file.

## [2.9.1.1] - 2026-07-01

### Changed

- Centralized SQLite/Postgres dialect handling in `db.py`. Case-insensitive search and ordering and insert-returning-id are now declared per query (`_like_ci`, `_order_ci`, `_returning_id`) instead of guessed from SQL text; `INSERT OR IGNORE` is translated generically for any table; and placeholder conversion no longer corrupts a literal `?` inside a string literal. Removes the hardcoded table lists and token-matching that could silently diverge between the two backends.
- Added `scripts/prerelease_check.py` and expanded the Postgres smoke test to cover the dialect-parity paths, wiring both into the pre-release sanity checks.

## [2.9.1] - 2026-07-01

### Changed

- General cleanup and optimization pass: backend hardening and reduced log noise.

## [2.9.0] - 2026-06-18

### Added

- `/track` now alerts you the moment a tracked movie shows up to rent or buy on a digital store (Apple TV, Google Play, Amazon Video, etc.), not just when it lands on a subscription service. Digital and subscription are announced separately, so you still get the dedicated subscription ping (including the Shudder alert) when the movie later starts streaming.

### Changed

- The `/track` confirmation now tells you if a movie is already available to rent or buy, in addition to whether it is already streaming.

## [2.8.0] - 2026-06-16

### Added

- `/rent`: after you spend both rerolls, you now get to pick from any of the films you were shown during the roll instead of having the last one auto-locked. A dropdown switches the previewed film and an accept button locks in your choice.

## [2.7.7] - 2026-06-16

### Changed

- Extended the off-event-loop database work to the remaining cog commands (discovery, tracking, watchlist, letterboxd, rentals, admin, achievements), so individual command database calls no longer run on the gateway thread.
- Update announcements now read member-facing copy from a dedicated `ANNOUNCEMENTS.md` file and fall back to the changelog entry when a version has no blurb, keeping announcements casual instead of reposting developer-focused changelog text.

## [2.7.6] - 2026-06-16

### Changed

- Postgres now uses a shared connection pool instead of opening a new connection for every query, removing the per-call connection handshake that was stalling the bot.

### Fixed

- Heavy commands (`/achievements`, `/achievementboard`, `/botstatus`, `/rentalstats`, `/lb group`) now run their database work off the event loop, so the bot stays responsive and scheduled jobs no longer get missed during busy database operations.

## [2.7.5] - 2026-06-16

### Fixed

- Achievement rescans now run without blocking the bot while badges are checked.

## [2.7.4] - 2026-06-10

### Fixed

- Guess rounds now reveal the answer immediately after a correct guess instead of waiting for score and achievement updates.

## [2.7.3] - 2026-06-10

### Fixed

- Prevented game rounds from crashing when a correct answer triggers score or achievement updates.

## [2.7.2] - 2026-06-09

### Fixed

- Fixed game score saves for `/guess`, `/play`, and `/six` when using the hosted database.

## [2.7.1] - 2026-06-07

### Changed

- Added hosted database support so the web dashboard can stay in sync with bot activity automatically.
- Kept the local SQLite database path as a fallback for development and rollback.

## [2.7.0] - 2026-06-06

### Added

- Added **42 new MacGuffins** to hunt down, trade, and show off.
- Added the new **MacGuffin sets** system, with rare achievement titles for members who complete a set.
- MacGuffin cards now show which set an item belongs to.
- Update announcements now include the latest changelog entry, and admins can post one manually with `/postupdate`.

### Changed

- Cleaned up the single MacGuffin card layout so fields line up more consistently.

## [2.6.1] - 2026-06-05

### Fixed

- Rental forum threads now add the **review** tag when a member returns a rental with written thoughts.
- `/setreviews` now detects the forum's **review** tag alongside the existing rental and recommendation tags.

## [2.6.0] - 2026-06-05

### Added

- Added `/plexrefresh` so admins can manually refresh the RB9 Plex library cache when Plex has new or changed titles.

### Fixed

- Plex title matching now treats `&` and `and` as equivalent, so titles like **Peter & The Wolf** can be found when Plex stores them with `and`.

## [2.5.9] - 2026-06-01

### Changed

- Embed formatting cleaned up across the board: titles and field names are consistently capitalized, body text and footers use sentence case.
- Redundant or low-value embed content trimmed: `/rb9stats` no longer duplicates oldest/newest films, `/rb9totalruntime` drops raw minutes, macguffin cards no longer repeat the card name, macguffin drop no longer shows a separate rarity field.
- Rental review recommend field now shows 👍/👎 instead of "yes"/"no".
- Rental status embed only surfaces the overdue notice when actually overdue.
- Achievement unlock posts no longer include a redundant "earned a new badge" line; avatar was also rendering twice (fixed).
- Achievement board "Top Shelves" renamed to "Top Collectors".
- Return modal titles, labels, and placeholders are properly formatted.
- Letterboxd, MacGuffin, and RB9 consistently capitalized throughout all embeds.

## [2.5.8] - 2026-05-30

### Changed

- `/achievementcatalog` now posts a compact catalog link instead of dumping the full achievement list into Discord.
- Added a JSON export path for the public website achievement catalog.

## [2.5.7] - 2026-05-30

### Added

- Added more RB9 library achievements, including actor-reference badges, title-theme badges, country/genre badges, and review-style badges.

## [2.5.6] - 2026-05-30

### Added

- Added RB9 library metadata achievements that use returned rentals and the persisted Plex library snapshot.

## [2.5.5] - 2026-05-30

### Fixed

- Fixed achievement title casing in announcement embeds, including numbered achievements.
- Shortened the `/restart` acknowledgement to `restart initiated.`

## [2.5.4] - 2026-05-30

### Changed

- Updated achievement unlock embeds to the gold announcement format.
- Added admin functionality for posting the achievement catalog and refreshing recent achievement feed embeds.

## [2.5.3] - 2026-05-28

### Added

- Added the `mutant mommy` achievement for whoever holds the iconic **the suckling** macguffin.

## [2.5.2] - 2026-05-28

### Changed

- `/achievementrescan` now posts newly backfilled achievement unlocks to the configured feed channel.

## [2.5.1] - 2026-05-28

### Changed

- Achievement badge roles now use a matching emoji and title case, like `🎬 Poster Child`.
- Existing achievement roles are renamed during badge role sync.

## [2.5.0] - 2026-05-28

### Added

- Achievements: members earn movie-club badges from rentals, reviews, macguffins, games, watchlists, tracking, and Letterboxd linking.
- Watched-movie achievements use returned rentals as the source of truth.
- Members can pin up to 3 earned achievements as visible Discord badge roles with `/achievementdisplay`, `/achievementhide`, and `/achievementclear`.
- `/achievements` shows a member's badge shelf and progress hints, while `/achievementboard` shows community unlock activity.
- `/setfeed` lets admins choose where Suckling posts achievement unlocks.
- Admins can backfill achievements with `/achievementrescan` and repair visible badge roles with `/achievementsyncroles`.

## [2.4.6] - 2026-05-28

### Added

- `/timezone` lets members set their personal rental timezone so rentals are due at 9 PM where they are.

### Changed

- Rentals are now due at 9 PM on the fifth calendar day instead of exactly 120 hours after checkout.
- Plex library data now persists in SQLite, refreshes incrementally each hour, and does a full weekly reconcile to catch removals.
- `/rb9`, rentals, and Plex availability checks can use the persisted library snapshot immediately after restart.

- `/lb group` now formats each member's recent watches as its own readable block instead of one dense paragraph.
- Letterboxd activity posts now show the Discord user linked to the account instead of falling back to the Letterboxd username.

## [2.4.5] - 2026-05-26

### Fixed

- Letterboxd activity posts now read member ratings more reliably when building star fields.

## [2.4.4] - 2026-05-24

### Added

- The desktop launcher now opens a branded Suckling dashboard with bot controls, status, live logs, update controls, and tray support.
- Added optional `Suckling.exe` build support for packaging the launcher as a Windows app.

### Changed

- The launcher is now the recommended way to run the bot and includes stronger duplicate-process protection.

## [2.4.3] - 2026-05-24

### Changed

- Letterboxd activity auto-posts now compact a single member's catch-up into one post when they have more than 3 new logs in a run.

## [2.4.2] - 2026-05-23

### Changed

- Rentals now last 5 days.

## [2.4.1] - 2026-05-21

### Changed

- MacGuffin drop embeds now put the drop type in the header and make the item name more prominent.
- Updated the banana costume MacGuffin flavor text.

## [2.4.0] - 2026-05-21

### Added

- Monthly Plex cleanup checks can find large, low-activity titles that are easy to stream elsewhere.
- `/plexcleanupnow` lets admins run the cleanup check manually in dry-run mode or post the current candidates.
- `/plexunpopular` lets admins review low-watch Plex titles with lower TMDB scores.

### Changed

- Rental title matching now ignores punctuation and spacing when targeting active rentals by title.

## [2.3.2] - 2026-05-20

### Added

- `/rent` now starts with a rental path menu: roll random, pick a movie yourself, or ask an admin for a recommendation.
- Members can now have up to 3 active rentals at once.
- `/return`, `/extend`, and `/myrental` now support multiple active rentals by showing or accepting rental ids.
- `/setrentalrequests` lets admins choose where rental recommendation requests post.

### Changed

- Randomly rolled rentals now have boosted rare/iconic macguffin drop odds when returned.
- Admin-assigned rentals are tracked as admin recommendations.
- Watchlist buttons now respond more safely to expired clicks and keep personal watchlist controls owner-only.
- Public film card buttons are now persistent across bot restarts for TMDB-backed movie cards.

## [2.3.1] - 2026-05-20

### Changed

- Housekeeping update: reorganized bot commands into feature modules so future updates are easier to maintain without changing the command list.
- Runtime paths now resolve from the project folder, which helps prevent accidental fresh data/log folders when the bot is launched from a different working directory.

## [2.3.0] - 2026-05-19

### Added

- **macguffins** - collectible, globally unique movie objects that drop when members return rentals.
- `/claimguffin` - one free starter macguffin for each member.
- `/myguffins` - private paginated collection view with per-card details.
- `/giftguffin @user <card>` - gift one of your macguffins to another member.
- `/adminguffins` - admin tool to view, add, move, remove, or randomly assign member macguffins.

### Changed

- `/return` no longer requires a rating; members can return a rental with just the recommendation checkbox and optional thoughts.

## [2.2.0] - 2026-05-19

### Added

- `/botstatus` - admin dashboard for version, uptime, latency, cache size, configured channels, auto-posting toggles, tracked films, linked Letterboxd accounts, active rentals, overdue rentals, and setup warnings.
- `/lblinked [page]` - admin list of linked Letterboxd accounts with Discord member, Letterboxd profile, and linked date.

### Changed

- Letterboxd activity posting now only posts recent RSS activity from the last 60 minutes since the previous activity run, preventing old unseen entries from flooding the channel.

## [2.1.0] - 2026-05-19

### Added

- **letterboxd activity feed** - admins can set a channel where new diary entries from linked members are posted automatically
  - `/setlbactivity <channel>` - sets the channel, seeds current feeds, and enables activity posting without dumping old watches
  - `/lbactivitynow [post]` - checks linked account activity manually; dry-run by default, live posts when `post:true`
  - `/toggle letterboxd activity <enabled>` - pauses or resumes the hourly activity feed

### Changed

- `/lb tastecheck` now compares any two Discord members or raw Letterboxd usernames instead of requiring one side to be you.

## [2.0.0] - 2026-05-18

### Added

- **letterboxd integration** - link your letterboxd account and pull in your activity
  - `/lb link <username>` - connects your letterboxd account (validates the feed before saving)
  - `/lb unlink` - removes your linked account
  - `/lb profile [user|username]` - shows recent diary entries with ratings, dates, and review snippets. accepts a discord mention (uses their linked lb account) or a raw lb username
  - `/lb watchlist [user|username]` - paginated view of a letterboxd watchlist with roll and import buttons
  - `/lb group` - aggregated recent watches across all linked server members
  - `/lb tastecheck` - compares two accounts for recent taste compatibility using shared recent watches and public watchlist overlap

- **personal watchlist** - a per-user film queue that lives in the bot
  - `/watchlist show` - browse your list (paginated, with a remove dropdown and roll button)
  - `/watchlist add <title> [year]` - add a film by title (disambiguates if needed)
  - `/watchlist remove <title>` - remove films by partial title match
  - **+ watchlist** button on all film card embeds - one-click add from `/suck`, `/roll`, `/rb9`, `/rb9randomscene`, and the daily rec

- **rent this button** on `/suck`, `/roll`, `/rb9`, `/rb9randomscene`, and the daily rec - the 📼 button that was promised in v1.5.0 but never shipped

- `letterboxd.py` - new module for async letterboxd parsing (diary + watchlist feeds)

### Changed

- `/suck` and `/roll` results now include a view with watchlist and (when available) rent buttons

### Database

- new `lb_accounts` table - discord user id to letterboxd username mapping
- new `watchlist` table - per-user internal film queue (title, year, tmdb_id, source, poster)

## [1.9.0] - 2026-05-17

### Added

- desktop launcher (`launcher.py` and `launcher/` package) - windows system tray app that wraps the bot with start, stop, restart, live logs, daily github update checks, one-click `update and restart`, and opt-in launch on startup.
- `launch.vbs` for double-click launching without a terminal window, plus `launch.bat` for troubleshooting with the venv activated.
- generated tray icon variants for running, update available, and crashed states.

### Changed

- bot shutdown now handles launcher stop signals cleanly so the tray app can stop the bot without a hard kill.

### Notes

- launcher requires `pystray` and `plyer`.
- launcher state lives in `data/launcher.json`, which is ignored by git.
- the launcher is windows-first. other platforms are not the target for this release.

## [1.8.0] - 2026-05-17

### Added

- `/extend` lets users extend an active rental by 24 hours once per rental.
- Rental reminder DMs now include an **extend 24h** button.
- Admin-only `/assignrental` command assigns an rb9 library film to a user, creates the review forum thread, and DMs the user their due date.

## [1.7.0] - 2026-05-16

### Added

- Admin-only `/restart` command that acknowledges the request, shuts down scheduled jobs/shared TMDB resources, and re-execs the bot process with the same Python invocation.

## [1.6.2] - 2026-05-15

### Changed

- Rental flow buttons now immediately show processing feedback and disable themselves to prevent double-click errors.

## [1.6.1] - 2026-05-15

### Changed

- Startup update announcement now includes a link to the public changelog page.

---

## [1.6.0] — 2026-05-15

### Added
- Startup update announcement: when the bot launches on a newly shipped version, it posts an embed to the configured update channel and records the announced version in sqlite so normal restarts do not repost it.

---

## [1.5.1] — 2026-05-15

### Changed
- TMDB client hardened: `tmdb._get` now handles retry/backoff (429 with `Retry-After` honored, 5xx with exponential backoff, transient network errors retried twice), enforces a global concurrency semaphore (8 simultaneous requests), and de-dupes in-flight requests so simultaneous calls for the same movie share one network round-trip. TTL caching centralized in `_get` — per-function cache logic removed from `get_movie_details`, `get_movie_cast`, `get_watch_providers`, `get_movie_keywords`, and `get_movie_images`.
- TMDB connection pool tuned: explicit `limit=32`, `limit_per_host=8`, DNS cache (5 min), `enable_cleanup_closed=True`. Timeout split into `sock_connect=10s` and `sock_read=20s`.
- `picker._fetch_pool` and `tracker._discover_horror_movies` now fetch pages in batches of 8 concurrently instead of one-at-a-time with sleeps. Cold-cache refresh in picker drops from ~10s+ of sleeps to ~2-3s.
- `tracker.run_check` runs 8 provider lookups concurrently per batch instead of sequentially. DB reads/writes still serialized per movie within the batch. Tracker no longer sleeps between movies — the global TMDB semaphore provides backpressure.
- Plex library cache hardened: refresh lock prevents concurrent commands from kicking off duplicate library scans. Added a precomputed normalized-title index, making `find_movie_by_title` an O(1) dict lookup instead of an O(n) scan-and-normalize. Precomputed dict version of the library so stats/random commands don't rebuild dicts every call.
- Plex library warms in the background at bot startup via a new `plex.warm_cache()` so the first `/rb9` or `/rent` after a restart doesn't pay the full library scan cost. Errors are logged but never block startup.
- New `tmdb.discover_movies()` helper. `picker.py` and `tracker.py` no longer reach into `tmdb.get_session()` or craft raw aiohttp calls — they go through the helper and inherit the new semaphore/retry/dedup automatically. Removed now-unused `config` import from `picker.py` and `tracker.py`.

### Notes
- TMDB cache key format changed (from per-function prefixes like `details:{id}` to path+params like `tmdb:/movie/{id}?...`). Cache is in-memory only, so this only matters at deploy: the cache starts cold.
- Watch providers TTL dropped from 6h to 30min. The tracker still passes `force=True` so its behavior is unchanged, but `/suck`, `/roll`, and the daily rec may refresh providers more often.
- No behavior changes to any user-facing command. No database migrations.
- One minor batching delta in picker/tracker discover pulls: when an empty page appears mid-batch, the loop now finishes the current batch before stopping instead of stopping immediately. TMDB doesn't have "holes" in discover results so this is academic.

---

## [1.5.0] — 2026-05-15

### Added

**video store rental system** — users can "rent" a film from the RB9 Plex library, have 5 days to watch it, and return it with a review. reviews post to a configurable Discord forum channel.

- `/rent` — picks a random film from the library (excluding anything the user has rented before). shows an offer screen with up to 2 re-rolls: first pick shows "re-roll", second shows "re-roll (last one)" with a warning, third pick is auto-confirmed with no choice. flow is fully ephemeral.
- `/return rating recommend [thoughts]` — returns the active rental and posts a review to the forum. rating is 1-10, recommend is a boolean toggle, thoughts are optional. edits the forum thread in-place: updates the starter message, renames the thread from "checked out" to "reviewed", and adds the recommendation tag if applicable.
- `/myrental` — ephemeral status card for your current rental: film, when you checked it out, when it's due (as a Discord timestamp), and a link to the forum thread.
- `/latefees` — leaderboard of accumulated late fees sorted descending. $1/day for every day overdue (computed at return time).
- `/rentalstats [user]` — rental history and stats for yourself or any member: total rentals, on-time vs late count, total fees paid, currently renting (if applicable), and last 5 returns.
- `/setreviews <forum_channel>` — admin-only. configures the forum channel for rental posts and auto-detects "rental" and "recommendation" forum tags. warns if either tag is missing.
- `/cancelrental @user [reason]` — admin-only. cancels a user's active rental with no late fee, edits the forum thread to show a grey cancelled state, and DMs the user with the reason.
- **rent button** on existing embeds — `/rb9`, `/rb9randomscene`, `/suck` (if in library), `/roll` (if in library), and daily rec (if in library) all show a 📼 `rent this` button when the film is available in the Plex library. button-initiated rentals get 0 re-rolls since the user already chose a specific film.
- **DM reminders** — bot DMs users when they have less than 12 hours left on a rental (once), and again when they go overdue (once).
- `rental.py` — new module handling forum thread creation/editing, late fee calculation, overdue notification, and reminder DMs. takes `bot` as a parameter; does not import `bot.py`.
- `rentals` table in `data/moviebot.db` — stores rental records with full lifecycle state (active, returned, cancelled), plex key snapshot, thread/message IDs, rating, thoughts, late fee, and notification flags.
- three new config keys in the `config` table: `reviews_channel_id`, `rental_tag_id`, `recommendation_tag_id`.
- hourly APScheduler job (`rental_check`) running `check_overdue` and `check_reminders`.
- `rating_key` field added to `plex._movie_to_dict` — needed to uniquely identify films across rerolls and for all-time exclusion tracking.
- `plex.pick_random_for_rental(exclude_keys)` — picks a random film excluding a given set of plex rating keys.

### Notes

- one active rental per user at a time. must `/return` before renting again.
- past rentals (any status) are permanently excluded from future `/rent` random picks for that user.
- forum thread lifecycle: created on rental confirmation with a "checked out" embed, edited in-place on return or admin cancel. starter message ID = thread ID in Discord forums.
- late fee is computed lazily at `/return` time: `ceil((returned_at − due_at) / 1 day) × $1`. returning on time = $0.
- no DB migration needed — `init_db` uses `CREATE TABLE IF NOT EXISTS`. new `rating_key` field in `_movie_to_dict` is transparent to existing callers.
- forum tags must be created manually in Discord's forum settings before `/setreviews` can auto-detect them. tags expected: **rental** and **recommendation** (or "recommended").
- bot needs `create public threads` + `send messages in threads` permissions in the forum channel.
- view timeouts are 300 seconds for `/rent` flow, 120 seconds for embed rent buttons. timed-out flows create no rental (the clock only starts on confirmation).

---

## [1.4.0] — 2026-05-12

### Added
- `/info` command — public about card showing bot version, uptime, server count, and the Suckling wordmark banner. Quick "what is this thing" reference for new server members.
- `assets/logo.png` — the Suckling wordmark, attached and referenced via `attachment://logo.png` in the embed. Rendered as `set_image` (full-width banner) rather than thumbnail since the wordmark is wide (~4.3:1).
- `SucklingBot.__init__` now stores `started_at`, used to compute uptime for `/info`.
- `info_embed()` and `_format_uptime()` helpers in `embeds.py`.

### Notes
- Uptime resets on every restart (no persistence — hobby scale).

---

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
