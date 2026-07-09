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
look up a movie. returns a card with the synopsis, director, runtime, and where to watch it (theaters, streaming). if the film is in the RB9 library, shows a 📼 **rent this** button.

- `title` (required): the movie title to search for
- `year` (optional): filter by release year if multiple matches exist

if multiple films share a title (e.g. "halloween"), a dropdown will appear so you can pick the right one. you can also pre-filter with `year` to skip the dropdown.

examples:
- `/suck The Substance`
- `/suck Halloween year:1978`

---

### `/roll [decade] [runtime]`
get a random movie recommendation. filters are optional — leave blank for a fully random pick. if the film is in the RB9 library, shows a 📼 **rent this** button.

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
picks a random movie from the library. returns title, summary, runtime, and poster. includes a 📼 **rent this** button.

### `/rb9randomscene`
a random film + a random backdrop image from it. includes a 📼 **rent this** button.

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

---

## video store

rent a film from the RB9 library. the clock starts when you confirm — it's due by 9 pm on the fifth day, and you use `/return` to post your review.

### `/rent`
start a rental from the library. you can have up to **3 active rentals** at once.

if you've set `/timezone`, rentals are due at 9 pm in your timezone. otherwise the bot uses the server default timezone.

the rental menu has three paths:

- **roll random** - get a random film, with up to **2 re-rolls**, then pick your favorite of what you saw
- **pick a movie** - choose a specific rb9 title yourself
- **ask an admin** - post a recommendation request so an admin can assign a pick

random rolls work like this:

1. bot shows a film: **[ accept rental ]** **[ re-roll ]**
2. first reroll: bot shows another film: **[ accept rental ]** **[ re-roll (last one) ]**
3. second reroll: bot shows a third film, then lets you pick any of the films you've been shown from a dropdown and accept it

films you've rented before are never offered again (all-time exclusion, any status).

randomly rolled rentals have boosted odds for rare/iconic macguffin drops when returned.

> you can also rent a specific film directly from the 📼 button on `/rb9`, `/rb9randomscene`, `/suck`, `/roll`, and the daily recommendation — no rerolls for those since you're choosing intentionally.

---

### `/timezone [timezone_name] [clear]`
set your rental timezone so due dates land at 9 pm where you are.

- `timezone_name` (optional): IANA timezone like `America/Los_Angeles`, `America/New_York`, or `Europe/London`
- `clear` (optional): clear your saved timezone and use the server default

run it with no options to check your current rental timezone.

---

### `/return`
start the private return flow.

`/return` opens a private menu. choose the rental, pick **watched it** or **didn't watch**, then fill out the popup.

watched returns post a review to the forum, can drop a macguffin, and count toward watched-rental achievements. unwatched returns close the rental without a review, rating, recommendation, macguffin drop, or watched achievement credit.

if you return it late, a late fee is calculated: **$1 for every day (or part of a day) overdue**. fees are cosmetic - tracked in the ledger but not collected.

---

### `/myrental`
shows your active rentals, due times, rental ids, and forum thread links. flags anything overdue.

ephemeral — only you see it.

---

### `/extend [rental]`
extend an active rental by 24 hours. each rental can be extended once.

- `rental` (optional): rental id or part of the title, needed if you have more than one active rental

the 12-hour reminder DM also includes an **extend 24h** button.

---

### `/setrentalrequests <channel>`
admin only. choose where **ask an admin** rental recommendation requests post.

- `channel` (required): the text channel for admin recommendation pings

---

### `/latefees`
leaderboard of accumulated late fees. shows total fees, total rentals, and late return count per person.

---

### `/rentalstats [user]`
your full rental history and stats: total rentals, on-time vs late, total fees, currently renting (if applicable), and paginated movie cards with status, rating, and recommendation.

- `user` (optional): check another member's stats

---

## macguffins

collectible movie objects. every macguffin is unique - once someone claims one, it is out of the pool.

macguffins can drop when you `/return` a rental. the drop posts publicly in the channel.

### `/claimguffin`
claim your one free starter macguffin.

the drop posts publicly in the channel, so everyone can see what you pulled.

---

### `/myguffins`
browse your macguffin collection. private to you.

shows 5 at a time, with a **view** button for each card.

---

### `/giftguffin @user <card>`
gift one of your macguffins to another member.

the card leaves your collection and moves to theirs. partial card names are fine as long as the bot can tell which one you mean.

---

### `/guffinhistory <card>`
see a macguffin's ownership history - who's held it and when it moved. works on any card, even ones you don't own.

- `card` (required): the macguffin's name or id

history only tracks moves from when this feature shipped forward, so older cards may show a shorter trail than their real age.

---

## achievements

earn movie-club badges by using suckling. rentals are the official record for watched-movie achievements, so anything about watching or returning movies comes from `/return`.

members can earn as many achievements as they want, but only **3** can be pinned as visible Discord badge roles at once.

achievement unlocks post to the Suckling feed channel once an admin configures it with `/setfeed`.

### `/achievements [user]`
view an achievement shelf.

- leave `user` blank to see your own private shelf, visible badges, recent unlocks, and progress hints
- choose another member to see their public shelf

### `/achievementdisplay <achievement> [replace]`
pin an earned achievement as one of your visible badge roles.

- `achievement` is one of your unlocked badges
- `replace` is optional, and lets you swap out an existing visible badge when your 3 slots are full

### `/achievementhide <achievement>`
remove one visible achievement badge role. the achievement stays unlocked.

### `/achievementclear`
remove all visible achievement badge roles.

### `/achievementboard`
community achievement board with newest unlocks, top shelves, and rare badges.

---

## tracking

### `/track <title> [year]`
add a movie to the watchlist. the bot will announce it in the streaming-alerts channel the moment it shows up to rent or buy on a digital store (apple tv, google play, etc.), and again when it later lands on a subscription service like shudder. if the film is *already* available, the bot will tell you immediately and link to where.

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

the bot automatically posts in five ways (admins configure the channels):

🩸 **daily recommendation** - every day at noon, the bot drops a random pick in the configured channel. if the film is in the library, it includes a 📼 rent button.

📺 **streaming announcements** - when a movie becomes available digitally for the first time (shudder, netflix, max, etc.), the bot announces it in the configured channel. shudder additions get a special highlight.

the streaming feature only announces films hitting digital for the first time — not when they move between services or get added to additional ones.

🏆 **achievement feed** - when members unlock badges, suckling posts a gold achievement embed to the configured feed channel.

📗 **letterboxd activity** - when admins enable it, suckling posts recent diary activity from linked members.

📅 **weekly recap** - every sunday at 11am, suckling posts a rundown of the week to the feed channel: top renters, new macguffin pulls, new achievement unlocks, and the current `/guess` and `/six` leaders.

---

## quick tips

- movie titles in `/suck` and `/track` support fuzzy matching, so you don't need exact punctuation or capitalization
- the dropdown menu that appears for ambiguous titles times out after 60 seconds — just run the command again if it expires
- both games and tracking are server-wide — anyone can add to the tracked list and play
- leaderboards are separate for `/guess` and `/six` — winning at one doesn't affect the other
- you can have up to 3 active rentals at a time
- the 5-day clock starts the moment you confirm — not when you start browsing
- rent buttons on embeds time out after 2 minutes. the command is still there if you miss it

---

## letterboxd

link your letterboxd account to see your activity and import your watchlist.

### `/lb link <username>`
connects your letterboxd account. the bot validates the account is public before saving.

### `/lb unlink`
removes your linked letterboxd account.

### `/lb profile [user] [username]`
shows recent diary entries - films watched, ratings, dates, and review snippets if you left one.

- `user` (optional): a server member - uses their linked letterboxd account
- `username` (optional): a raw letterboxd username (no discord account needed)
- leave both blank to see your own

### `/lb watchlist [user] [username]`
browse a letterboxd watchlist. shows 5 films per page with:
- **🎲 roll from this** - picks a random film and shows the full film card
- **📥 import all** - adds the entire watchlist to your personal bot watchlist

### `/lb group`
shows what everyone in the server has been watching lately - aggregated recent diary activity across all linked members, sorted by date.

linked accounts can also appear in the server's letterboxd activity channel if admins have enabled it.

### `/lb tastecheck [a_user] [b_user] [a_username] [b_username]`
compares two letterboxd accounts and gives a recent taste compatibility readout.

- use discord members if they've linked accounts with `/lb link`
- use raw usernames to compare anyone with public letterboxd accounts
- pick one target for side a and one target for side b
- based on recent diary activity and public watchlists, not full lifetime letterboxd history

---

## personal watchlist

a per-user film queue that lives in the bot. add films from anywhere and roll from it when you can't decide what to watch.

### `/watchlist show`
browse your watchlist. paginated (10 per page) with:
- a dropdown to remove films from the current page
- **🎲 roll from list** - picks a random film and shows the full film card with rent button if it's in the library

### `/watchlist add <title> [year]`
add a film by title. disambiguates if there are multiple matches.

### `/watchlist remove <title>`
remove films by partial title match. removes all matching entries.

you can also add films directly from the **+ watchlist** button on any film card from `/suck`, `/roll`, `/rb9`, `/rb9randomscene`, or the daily rec.


---

# admin commands

> these commands require the **manage server** permission and are hidden from regular members.

### `/botstatus`
admin dashboard for the bot: version, uptime, latency, cache size, configured channels, auto-posting toggles, tracked film count, linked letterboxd count, active rentals, overdue rentals, and setup warnings.

### `/lblinked [page]`
list linked letterboxd accounts. shows each discord member, their letterboxd profile, and when they linked it. use `page` if the list is long.

### `/setreviews <forum_channel>`
set the forum channel where rental reviews post. the bot needs **create public threads** and **send messages in threads** permissions. auto-detects **rental**, **review**, and **recommendation** forum tags if they exist (create them in the forum's settings first, then run this command).

### `/cancelrental @user [reason]`
cancel a user's active rental. no late fee applied. edits the forum thread to a grey "cancelled" state and DMs the user. optionally include a reason — it shows in the thread and the DM.

### `/assignrental @user <title> [year]`
assign an rb9 library rental to a user. creates the rental record, opens the review forum thread, and DMs the user with the due date.

### `/adminguffins <action> @user [card]`
view or edit a member's macguffins.

- `view`: shows the member's current collection
- `add`: adds a macguffin by name or id; if someone else has it, moves it
- `remove`: removes a macguffin from that member's collection
- `random`: assigns the member a random unclaimed macguffin

### `/setannouncements <channel>`
set the channel where streaming announcements post. the bot needs send-message and embed-link permissions in the chosen channel.

### `/setdaily <channel>`
set the channel where the daily recommendation posts (at noon).

### `/setlbactivity <channel>`
set the channel where new diary entries from linked letterboxd accounts post. when you run this, the bot seeds the current feeds first so old watches do not flood the channel.

### `/setfeed <channel>`
set the channel where suckling feed posts go, including achievement unlocks and the weekly recap.

the bot needs send-message and embed-link permissions in the chosen channel.

### `/achievementcatalog <channel>`
post an achievement catalog embed and website link in the chosen channel.

useful for pinning a public achievement reference without dumping the full badge list into Discord.

### `/postfaq <channel> [thread_name]`
admin only. create a Suckling FAQ thread in the chosen channel. the bot posts a primary index embed with jump links inside the thread, followed by compact section embeds for lookup, rentals, watchlists, letterboxd, games, achievements, and macguffins.

### `/toggle <feature> <enabled>`
enable or disable an auto-posting feature without removing the channel setting.

- `feature`: `streaming announcements`, `daily recommendation`, `letterboxd activity`, or `weekly recap`
- `enabled`: `True` or `False`

### `/checknow`
manually trigger the streaming check in dry-run mode. doesn't post to discord — just returns a summary of what *would* be announced. useful for verifying detection.

### `/checknowlive`
manually trigger the streaming check **and post** announcements live. use sparingly.

### `/dailynow`
manually trigger today's daily recommendation post.

### `/recapnow`
manually trigger the weekly recap post to the feed channel.

### `/postupdate`
post the current bot update announcement, including the changelog entry for the current version.

### `/lbactivitynow [post]`
manually check linked letterboxd activity. by default this is a dry run and reports how many unseen entries were found, plus how many are recent enough to post. set `post:True` to post entries from the last 60 minutes only.

### `/plexrefresh`
refresh the rb9 plex library cache. use this when plex has a new or changed title and suckling does not see it yet.

### `/cachestats [clear]`
show the size of the in-memory cache. pass `clear:True` to wipe both the tmdb cache and the random-pick pool (useful if data feels stale).

### `/restart`
restart the bot process. useful after pulling updates or clearing a stuck runtime state.

### `/achievementrescan [user]`
backfill achievements from existing bot history. leave `user` blank to rescan everyone.

### `/achievementsyncroles <user>`
reapply a member's selected visible achievement badge roles. useful if roles were manually changed in Discord.

the bot needs manage roles, and its own Discord role must be above the achievement badge roles.

### `/achievementrefreshfeed [limit]`
refresh recent achievement feed posts into the current achievement announcement format.

### `/version`
show the bot's current version.

### `/ping`
quick health check — replies with the bot's latency. available to everyone, but mostly used for debugging.
