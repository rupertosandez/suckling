# bot commands

a reference for everything the bot can do. all commands are slash commands — type `/` in any channel where the bot has access to see them in the autocomplete.

this bot is for the **return by 9** movie community.

---

## about the bot

### `/info`
quick about card for the bot.

---

## lookup & discovery

### `/suck <title> [year]`
look up a movie. returns a card with the synopsis, director, runtime, and where to watch it (theaters, streaming).

- `title` (required): the movie title to search for
- `year` (optional): filter by release year if multiple matches exist

if multiple films share a title (e.g. "halloween"), a dropdown will appear so you can pick the right one. you can also pre-filter with `year` to skip the dropdown.

examples:
- `/suck The Substance`
- `/suck Halloween year:1978`

---

### `/roll [decade] [runtime]`
get a random movie recommendation. filters are optional — leave blank for a fully random pick.

- `decade` (optional): e.g. `1980s`, `2010s`
- `runtime` (optional): `short` (under 90 min), `medium` (90-120 min), or `long` (over 120 min)

examples:
- `/roll`
- `/roll decade:1980s`
- `/roll decade:2020s runtime:short`

---

## return by 9 library

commands that pull from the return by 9 plex library.

### `/rb9`
picks a random movie from the library. returns title, summary, runtime, and poster.

### `/rb9stats`
overall library summary: total movie count, total runtime, year range, oldest film, newest film, most recently added, average rating.

### `/rb9biggest`
the longest film in the library by runtime.

### `/rb9shortest`
the shortest film in the library by runtime (excludes very short entries under 30 minutes).

### `/rb9oldest`
the oldest film in the library by release year.

### `/rb9newest`
the most recently added film in the library.

### `/rb9totalruntime`
fun stats on how long it'd take to watch the entire library back-to-back, including a "8 hours per day" estimate.

### `/rb9decade`
bar chart breakdown of films per decade in the library.

### `/rb9genre`
top 10 genres in the library by count.

### `/rb9randomscene`
a random film + a random backdrop image from it.

---

## tracking

### `/track <title> [year]`
add a movie to the watchlist. the bot will announce it in the streaming-alerts channel when it becomes available digitally for the first time. if the film is *already* streaming, the bot will tell you immediately and link to where.

- `title` (required): the movie title to track
- `year` (optional): filter by year if there are multiple matches

example: `/track The Conjuring Last Rites`

---

### `/untrack <title>`
remove a movie from the watchlist.

- `title` (required): the title or part of the title to remove

example: `/untrack conjuring`

---

### `/tracked`
show every movie currently being tracked, plus who added each one.

---

## games

### `/play`
start a trivia roulette round. the bot randomly picks one of four categories and posts a clue. first person to guess correctly in chat wins.

categories:
- 🎬 **quote** - a memorable line of dialogue
- 🎭 **emoji** - the film described in emoji
- 📜 **tagline** - the marketing tagline from posters or trailers
- 🎞️ **trivia** - a piece of behind-the-scenes or production trivia

30-second time limit per round. 1 point per win. shares the leaderboard with `/guess` (use `/leaderboard` to see standings).

only one round can be active in a channel at a time. `/giveup` ends the round.

example: `/play`

---

### `/guess [difficulty]`
start a guessing round. the bot posts an image and the first person to guess correctly in chat wins. 60-second time limit per round.

- `difficulty` (optional): `easy` (full movie still, 1 point) or `hard` (cropped poster, 2 points). default: random.

only one round can be active in a channel at a time.

examples:
- `/guess`
- `/guess difficulty:easy`
- `/guess difficulty:hard`

---

### `/six`
start a six degrees of separation round. the bot picks two random popular actors and challenges players to connect them through shared films.

submit chains in chat using this format:
```
Actor -> Film -> Actor -> Film -> Actor
```

first valid chain wins. shorter chains earn more points:

| films in chain | points |
| -------------- | ------ |
| 1              | 5      |
| 2              | 4      |
| 3              | 3      |
| 4              | 2      |
| 5+             | 1      |

maximum chain length is 6 films. round duration is 4 minutes.

---

### `/giveup`
end the current round (works for both `/guess` and `/six`). anyone in the channel can call it.

---

### `/leaderboard`
show the top scorers for `/guess`.

### `/sixleaderboard`
show the top scorers for `/six`.

---

## auto-posting features

the bot automatically posts in two ways (admins configure the channels):

🩸 **daily recommendation** - every day at noon, the bot drops a random pick in the configured channel.

📺 **streaming announcements** - when a movie becomes available digitally for the first time (shudder, netflix, max, etc.), the bot announces it in the configured channel. shudder additions get a special highlight.

the streaming feature only announces films hitting digital for the first time — not when they move between services or get added to additional ones.

---

## quick tips

- movie titles in `/suck` and `/track` support fuzzy matching, so you don't need exact punctuation or capitalization
- the dropdown menu that appears for ambiguous titles times out after 60 seconds — just run the command again if it expires
- both games and tracking are server-wide — anyone can add to the tracked list and play
- leaderboards are separate for `/guess` and `/six` — winning at one doesn't affect the other

---

# admin commands

> these commands require the **manage server** permission and are hidden from regular members.

### `/setannouncements <channel>`
set the channel where streaming announcements post. the bot needs send-message and embed-link permissions in the chosen channel.

### `/setdaily <channel>`
set the channel where the daily recommendation posts (at noon).

### `/toggle <feature> <enabled>`
enable or disable an auto-posting feature without removing the channel setting.

- `feature`: `streaming announcements` or `daily recommendation`
- `enabled`: `True` or `False`

### `/checknow`
manually trigger the streaming check in dry-run mode. doesn't post to discord — just returns a summary of what *would* be announced. useful for verifying detection.

### `/checknowlive`
manually trigger the streaming check **and post** announcements live. use sparingly.

### `/dailynow`
manually trigger today's daily recommendation post.

### `/cachestats [clear]`
show the size of the in-memory cache. pass `clear:True` to wipe both the tmdb cache and the random-pick pool (useful if data feels stale).

### `/version`
show the bot's current version.

### `/ping`
quick health check — replies with the bot's latency. available to everyone, but mostly used for debugging.