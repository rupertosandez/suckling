# Bot Commands

A reference for everything the bot can do. All commands are slash commands — type `/` in any channel where the bot has access to see them in the autocomplete.

This bot is for the **Return by 9** movie community.

---

## Lookup & Discovery

### `/watch <title> [year]`
Look up a movie. Returns a card with the synopsis, director, runtime, and where to watch it (theaters, streaming).

- **`title`** (required): the movie title to search for
- **`year`** (optional): filter by release year if multiple matches exist

If multiple films share a title (e.g. "Halloween"), a dropdown will appear so you can pick the right one. You can also pre-filter with `year` to skip the dropdown.

**Examples:**
- `/watch The Substance`
- `/watch Halloween year:1978`

---

### `/roll [decade] [runtime]`
Get a random movie recommendation. Filters are optional — leave blank for a fully random pick.

- **`decade`** (optional): e.g. `1980s`, `2010s`
- **`runtime`** (optional): `short` (under 90 min), `medium` (90–120 min), or `long` (over 120 min)

**Examples:**
- `/roll`
- `/roll decade:1980s`
- `/roll decade:2020s runtime:short`

---

## Return by 9 Library

Commands that pull from the Return by 9 Plex library.

### `/rb9`
Picks a random movie from the library. Returns title, summary, runtime, and poster.

### `/rb9stats`
Overall library summary: total movie count, total runtime, year range, oldest film, newest film, most recently added, average rating.

### `/rb9biggest`
The longest film in the library by runtime.

### `/rb9shortest`
The shortest film in the library by runtime (excludes very short entries under 30 minutes).

### `/rb9oldest`
The oldest film in the library by release year.

### `/rb9newest`
The most recently added film in the library.

### `/rb9totalruntime`
Fun stats on how long it'd take to watch the entire library back-to-back, including a "8 hours per day" estimate.

### `/rb9decade`
Bar chart breakdown of films per decade in the library.

### `/rb9genre`
Top 10 genres in the library by count.

### `/rb9randomscene`
A random film + a random backdrop image from it.

---

## Tracking

### `/track <title> [year]`
Add a movie to the watchlist. The bot will announce it in the streaming-alerts channel when it becomes available digitally for the first time. If the film is *already* streaming, the bot will tell you immediately and link to where.

- **`title`** (required): the movie title to track
- **`year`** (optional): filter by year if there are multiple matches

**Example:** `/track The Conjuring Last Rites`

---

### `/untrack <title>`
Remove a movie from the watchlist.

- **`title`** (required): the title or part of the title to remove

**Example:** `/untrack conjuring`

---

### `/tracked`
Show every movie currently being tracked, plus who added each one.

---

## Games

### `/guess [difficulty]`
Start a guessing round. The bot posts an image and the first person to guess correctly in chat wins. 60-second time limit per round.

- **`difficulty`** (optional): `easy` (full movie still, 1 point) or `hard` (cropped poster, 2 points). Default: random.

Only one round can be active in a channel at a time.

**Examples:**
- `/guess`
- `/guess difficulty:easy`
- `/guess difficulty:hard`

---

### `/six`
Start a Six Degrees of Separation round. The bot picks two random popular actors and challenges players to connect them through shared films.

Submit chains in chat using this format:
```
Actor -> Film -> Actor -> Film -> Actor
```

First valid chain wins. Shorter chains earn more points:

| Films in chain | Points |
| -------------- | ------ |
| 1              | 5      |
| 2              | 4      |
| 3              | 3      |
| 4              | 2      |
| 5+             | 1      |

Maximum chain length is 6 films. Round duration is 4 minutes.

---

### `/giveup`
End the current round (works for both `/guess` and `/six`). Anyone in the channel can call it.

---

### `/leaderboard`
Show the top scorers for `/guess`.

### `/sixleaderboard`
Show the top scorers for `/six`.

---

## Auto-Posting Features

The bot automatically posts in two ways (admins configure the channels):

**🩸 Daily recommendation** — every day at noon, the bot drops a random pick in the configured channel.

**📺 Streaming announcements** — when a movie becomes available digitally for the first time (Shudder, Netflix, Max, etc.), the bot announces it in the configured channel. Shudder additions get a special highlight.

The streaming feature only announces films hitting digital for the first time — not when they move between services or get added to additional ones.

---

## Quick Tips

- **Movie titles in `/watch` and `/track`** support fuzzy matching, so you don't need exact punctuation or capitalization.
- **The dropdown menu** that appears for ambiguous titles times out after 60 seconds — just run the command again if it expires.
- **Both games and tracking are server-wide** — anyone can add to the tracked list and play.
- **Leaderboards are separate** for `/guess` and `/six` — winning at one doesn't affect the other.

---

# Admin Commands

> These commands require the **Manage Server** permission and are hidden from regular members.

### `/setannouncements <channel>`
Set the channel where streaming announcements post. The bot needs send-message and embed-link permissions in the chosen channel.

### `/setdaily <channel>`
Set the channel where the daily recommendation posts (at noon).

### `/toggle <feature> <enabled>`
Enable or disable an auto-posting feature without removing the channel setting.

- **`feature`**: `streaming announcements` or `daily recommendation`
- **`enabled`**: `True` or `False`

### `/checknow`
Manually trigger the streaming check in dry-run mode. Doesn't post to Discord — just returns a summary of what *would* be announced. Useful for verifying detection.

### `/checknowlive`
Manually trigger the streaming check **and post** announcements live. Use sparingly.

### `/dailynow`
Manually trigger today's daily recommendation post.

### `/cachestats [clear]`
Show the size of the in-memory cache. Pass `clear:True` to wipe both the TMDB cache and the random-pick pool (useful if data feels stale).

### `/version`
Show the bot's current version.

### `/ping`
Quick health check — replies with the bot's latency. Available to everyone, but mostly used for debugging.
