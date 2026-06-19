# Suckling Embed Imagery Reference

This is a planning reference for adding small contextual Suckling mascot PNGs to Discord embeds.

General guidance:

- Use mascot art where Suckling is doing something: handing over a rental, announcing news, awarding a badge, hosting a game, checking a ledger, or guiding a member.
- Be careful with film lookup embeds that already use movie posters or backdrops. Those usually benefit more from the actual movie art staying dominant.
- A good baseline is one small transparent PNG per embed family, not necessarily one unique PNG per command.

## Discovery And Film Cards

### `movie_embed`

Used by `/suck`, watchlist roll buttons, and generic movie lookups.

Current visual: movie poster thumbnail from TMDB.

Imagery suggestion: low priority. Keep the movie poster as the main visual. If mascot art is added, use a tiny "film clerk" Suckling as a footer/icon motif rather than replacing the poster.

### `roll_embed`

Used by `/roll` random horror recommendation.

Current visual: movie poster thumbnail from TMDB.

Imagery suggestion: Suckling rolling dice next to a stack of tapes, or pulling a random VHS from a bargain bin. Good candidate if the mascot can appear alongside, not instead of, the poster.

### `daily_rec_embed`

Used by the scheduled daily horror recommendation.

Current visual: movie poster thumbnail from TMDB.

Imagery suggestion: Suckling presenting "today's pick" on a little marquee sign, holding a candle, or setting a VHS on a spooky movie-night table.

### `streaming_announcement_embed`

Used by the automatic streaming availability feed.

Current visual: movie poster thumbnail from TMDB.

Imagery suggestion: Suckling pointing a remote at a TV that says "now streaming", or popping up from behind a television with a little antenna.

### `digital_announcement_embed`

Used by the automatic availability feed when a tracked movie shows up to rent or buy on a digital store (Apple TV, Google Play, etc.). Teal accent to distinguish it from the subscription-streaming announcement.

Current visual: movie poster thumbnail from TMDB.

Imagery suggestion: Suckling at a video-store rental counter or tapping a "rent / buy" button on a phone, clutching a credit card or a coin.

### `rb9_pick_embed`

Used by `/rb9` to pick a random film from the Return by 9 library.

Current visual: Plex library thumbnail.

Imagery suggestion: Suckling behind a video store counter handing over a tape from the RB9 shelf. Keep the film thumbnail visible if possible.

### `rb9_random_scene_embed`

Used by `/rb9randomscene`.

Current visual: full movie backdrop image.

Imagery suggestion: low priority. The backdrop is the point of the embed. If used, make Suckling a tiny theater usher with a flashlight, but do not compete with the scene image.

## Return By 9 Stats

### `rb9_stats_embed`

Used by `/rb9stats`.

Current visual: none.

Imagery suggestion: librarian Suckling standing in front of shelves, holding a clipboard of library totals.

### `rb9_single_movie_embed`

Used by `/rb9biggest`, `/rb9shortest`, `/rb9oldest`, and `/rb9newest`.

Current visual: Plex library thumbnail.

Imagery suggestion: make variants only if desired. Biggest: Suckling struggling to carry a giant tape. Shortest: Suckling holding a tiny tape. Oldest: Suckling dusting off an ancient VHS. Newest: Suckling placing a fresh arrival sticker on a tape.

### `rb9_total_runtime_embed`

Used by `/rb9totalruntime`.

Current visual: none.

Imagery suggestion: Suckling buried under an enormous mountain of tapes, or sitting with a calendar and an impossible watch schedule.

### `rb9_decade_embed`

Used by `/rb9decade`.

Current visual: text bar chart.

Imagery suggestion: Suckling sorting tapes into decade-labeled bins.

### `rb9_genre_embed`

Used by `/rb9genre`.

Current visual: text bar chart.

Imagery suggestion: Suckling sorting tapes by genre stickers, with a few visible labels like horror, action, comedy, and drama.

## Rentals

### `rental_offer_embed`

Used during the `/rent` reroll flow before a member commits.

Current visual: Plex library thumbnail.

Imagery suggestion: Suckling holding up a VHS for inspection, with a small "rent this?" posture. This is one of the best fits for mascot art.

### `rental_confirmed_embed`

Used as the forum thread opener when a rental is confirmed.

Current visual: Plex library thumbnail.

Imagery suggestion: Suckling handing the member a VHS with a due-date slip tucked into the case.

### `rental_review_embed`

Used when `/return` posts a watched return and review.

Current visual: poster or Plex thumbnail.

Imagery suggestion: Suckling stamping a tape "returned" while reading a little review card.

### `rental_unwatched_return_embed`

Used when `/return` marks a rental as returned unwatched.

Current visual: poster or Plex thumbnail.

Imagery suggestion: Suckling gently taking back an unopened tape, maybe with a sheepish expression and a little dust puff.

### `rental_cancelled_embed`

Used when an admin cancels a rental thread.

Current visual: poster or Plex thumbnail.

Imagery suggestion: Suckling putting a VHS back on the shelf with a neutral "closed" tag. Keep it calm, not punitive.

### `rental_status_embed`

Used by `/myrental` when a member has one active rental.

Current visual: poster thumbnail.

Imagery suggestion: Suckling checking a due-date card. For overdue rentals, Suckling with a tiny alarm clock or red ledger.

### `rental_status_list_embed`

Used by `/myrental` when a member has multiple active rentals.

Current visual: none.

Imagery suggestion: Suckling carrying a stack of checked-out tapes with due-date slips sticking out.

### `late_fees_embed`

Used by `/latefees`.

Current visual: none.

Imagery suggestion: cashier Suckling with a calculator, receipt roll, and a tiny late-fee ledger.

### `rental_stats_embed`

Used by `/rentalstats`.

Current visual: none.

Imagery suggestion: Suckling reviewing a member's rental history on a clipboard, with little returned/on-time/late stamps.

## Games

### Guess intro embed

Used by `/guess` when a poster/still guessing round starts.

Current visual: full puzzle image attachment.

Imagery suggestion: low priority because the puzzle image is the core visual. If used, Suckling with a magnifying glass as a small accompanying mascot.

### Guess reveal embed

Used by `/guess` when the answer is revealed.

Current visual: original movie image.

Imagery suggestion: Suckling dramatically lifting a curtain or flipping over a reveal card.

### `trivia_prompt_embed`

Used by `/play` when a trivia roulette round starts.

Current visual: none.

Imagery suggestion: quizmaster Suckling holding cue cards beside a little roulette wheel.

### `trivia_reveal_embed`

Used by `/play` when the answer is revealed.

Current visual: none.

Imagery suggestion: winner version: Suckling tossing confetti or ringing a bell. Timeout version: Suckling holding an answer card with an hourglass.

### Six Degrees intro embed

Used by `/six` when a Six Degrees round starts.

Current visual: none.

Imagery suggestion: detective Suckling with a corkboard, red string, actor headshots, and film cards.

### Six Degrees win embed

Used by `/six` when someone submits a winning chain.

Current visual: none.

Imagery suggestion: Suckling proudly connecting the final string on the corkboard, or holding a completed chain like a ribbon.

### Horror Guess leaderboard embed

Used by `/leaderboard`.

Current visual: none.

Imagery suggestion: Suckling beside an arcade-style scoreboard or podium.

### Six Degrees leaderboard embed

Used by `/sixleaderboard`.

Current visual: none.

Imagery suggestion: Suckling at a detective scoreboard with string-chain trophies.

## Achievements

### `unlock_embed`

Used by the achievement feed when a member unlocks a badge.

Current visual: member avatar in author icon.

Imagery suggestion: Suckling popping out with a trophy, gold badge, or enamel pin. Strong candidate for a reusable celebratory mascot image.

### Achievement shelf embed

Used by `/achievements`.

Current visual: member avatar in author icon.

Imagery suggestion: Suckling arranging badges on a little display shelf.

### Achievement board embed

Used by `/achievementboard`.

Current visual: none.

Imagery suggestion: Suckling maintaining a community badge leaderboard, with a chalkboard or trophy cabinet.

### Achievement catalog embed

Used by `/achievementcatalog`.

Current visual: none.

Imagery suggestion: Suckling holding open a catalog or binder of badges.

## MacGuffins

### `macguffin_drop_embed`

Used when a MacGuffin is claimed or dropped.

Current visual: none, except the item's emoji in text.

Imagery suggestion: Suckling presenting a mysterious object under a spotlight or velvet cloth. Strong candidate for brand imagery.

### `macguffin_card_embed`

Used when viewing one MacGuffin from a collection.

Current visual: none, except the item's emoji in text.

Imagery suggestion: Suckling holding a collectible card slab or museum placard.

### `macguffin_list_embed`

Used by `/myguffins`.

Current visual: none.

Imagery suggestion: Suckling opening a collector binder or sorting props into little cubbies.

## Letterboxd And Watchlists

### `lb_profile_embed`

Used by `/lb profile`.

Current visual: none.

Imagery suggestion: Suckling reading a film diary or flipping through a notebook of recent watches.

### `lb_activity_embed`

Used by the automatic Letterboxd activity feed for a single diary entry.

Current visual: Letterboxd thumbnail if available.

Imagery suggestion: critic Suckling with star cards or a little notebook. Keep the film thumbnail if present.

### `lb_activity_compact_embed`

Used by the automatic Letterboxd activity feed for several recent diary entries.

Current visual: none.

Imagery suggestion: Suckling carrying a stack of mini review cards.

### `lb_watchlist_embed`

Used by `/lb watchlist`.

Current visual: none.

Imagery suggestion: Suckling checking boxes on a watchlist clipboard.

### `lb_group_embed`

Used by `/lb group`.

Current visual: none.

Imagery suggestion: Suckling looking at a community watch board covered in member notes.

### `lb_tastecheck_embed`

Used by `/lb tastecheck`.

Current visual: none.

Imagery suggestion: Suckling comparing two scorecards, holding them side by side like a tiny compatibility judge.

### `mywatchlist_embed`

Used by `/watchlist` and personal watchlist pagination.

Current visual: none.

Imagery suggestion: Suckling adding tapes to a personal shelf or writing a "to watch" list.

### `lb_linked_embed`

Used by `/lblinked` admin view.

Current visual: none.

Imagery suggestion: low priority. Admin-facing roster. If used, Suckling connecting little profile cards with string.

## Admin, Info, And Maintenance

### `info_embed`

Used by `/info`.

Current visual: existing `logo.png` banner attachment.

Imagery suggestion: Suckling mascot portrait or help-desk pose. This could become the canonical "about Suckling" brand image, but preserve the logo banner unless you intentionally redesign the card.

### `bot_status_embed`

Used by `/botstatus`.

Current visual: none.

Imagery suggestion: low priority because it is admin-only. Suckling in a tiny control room with status lights if you want charm in admin tools.

### `plex_cleanup_embed`

Used by the monthly Plex cleanup review.

Current visual: none.

Imagery suggestion: Suckling sweeping dusty tapes into a review pile, not throwing them away.

### Tracked movies embed

Used by `/tracked`.

Current visual: none.

Imagery suggestion: Suckling watching a radar screen or pinning tracked movies to a board.

### FAQ index embed

Used by the generated Suckling FAQ thread.

Current visual: none.

Imagery suggestion: Suckling at an information desk with a small guidebook.

### FAQ section embeds

Used inside the generated Suckling FAQ thread.

Current visual: none.

Imagery suggestion: low priority. A shared guidebook/desk mascot could cover the whole FAQ family instead of unique art per section.

### FAQ thread starter embed

Used when posting the starter message for the Suckling FAQ thread.

Current visual: none.

Imagery suggestion: Suckling pointing toward an open thread or holding a "start here" sign.

### Update announcement embed

Used once at startup after a shipped version.

Current visual: none.

Imagery suggestion: Suckling flexing, painting a fresh version number on a sign, or popping out of a toolbox after an update.

