# UX audit notes

This repo powers Suckling, a Discord bot for the Return by 9 movie community. The fastest way to understand the user experience is to read these files in order:

1. `README.md` - setup, feature overview, permissions, runtime notes, and troubleshooting
2. `COMMANDS.md` - member-facing command reference, including admin-only commands
3. `PROJECT_OVERVIEW.md` - architecture map, data model, scheduled jobs, recent feature context
4. `CHANGELOG.md` - release history and recent user-visible changes

## Primary user journeys

- **Find something to watch:** `/suck`, `/roll`, `/rb9`, `/rb9randomscene`, daily recommendations, and watchlist buttons all lead to movie cards. If the film is in the RB9 Plex library, cards can show a rent button.
- **Rent and return:** `/rent` starts a rental, `/return` posts the review, `/extend` adds one 24-hour extension, and `/myrental` shows active rentals. Members can have up to 3 active rentals.
- **Achievements:** `/achievements` shows a badge shelf. `/achievementdisplay`, `/achievementhide`, and `/achievementclear` control up to 3 visible Discord badge roles. Unlock announcements post in the configured feed channel.
- **Community play:** `/guess`, `/play`, and `/six` run chat-based movie games with separate leaderboards.
- **Collections:** MacGuffins drop from returned rentals and can be viewed, gifted, or admin-managed.
- **Discovery queues:** `/track` manages the community streaming watchlist, while `/watchlist` manages each member's private film queue.
- **Letterboxd:** `/lb` commands link accounts, show activity, browse/import watchlists, and compare taste.

## UX areas worth reviewing

- Achievement shelf clarity: earned vs pinned vs next-up progress.
- Feed announcement readability, especially achievement embeds on desktop and mobile Discord.
- Rental flow clarity when a member has multiple active rentals.
- Forum review thread lifecycle from checked-out to reviewed or cancelled.
- Permissions/setup messages for feed, reviews forum, badge roles, and auto-post channels.
- Admin command language: it should be clear when a command posts publicly versus runs as a dry run.

## Implementation landmarks

- `cogs/achievements.py` - achievement commands, catalog posts, feed refresh, and admin rescan
- `achievements.py` - achievement definitions, progress rules, feed embeds, role sync
- `cogs/rentals.py` and `rental.py` - rental command UX and forum thread lifecycle
- `embeds.py` - shared Discord embed formatting
- `views.py` - button/dropdown flows
- `db.py` - persistent state helpers and table definitions

For watched-movie achievements, `/return` is the source of truth. Watching movies outside the rental system does not count unless an admin backfills eligible history with `/achievementrescan`.
