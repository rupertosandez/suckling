# Update Announcements

Member-facing copy for the Discord "i've been updated" announcement, separate from
the developer-focused `CHANGELOG.md`.

How it works:

- Each entry uses the same `## [version]` header as the changelog so the same parser
  can read it.
- When the bot announces a new version it looks here first. If a matching version is
  found, that casual copy is posted. If not, it falls back to the changelog entry, then
  to a generic message.
- Keep the copy casual and lowercase to match the bot's voice. No em dashes. Skip the
  internal details (refactors, library names, db plumbing) unless a member would notice
  the difference. One or two short lines is plenty.

## [2.7.7]

quicker on my feet now. commands don't make me freeze up while i talk to the database, so everything should feel snappier.

## [2.9.1]

just some housekeeping under the hood, nothing you'll notice day to day.

## [2.9.1.1]

more behind-the-scenes tidying. searching for a movie works the same now no matter how you type it, caps or not. nothing new to click, just keeping things running smooth.

## [2.9.1.2]

another quiet one under the hood. nothing to click, nothing you'll notice.

## [2.10.2]

fixed guessing games to not take so long to register guesses.

## [2.10.3]

unlocked speedrun-brain on myself. i'm fast now, try me.

## [2.10.3.1]

database updates

## [2.10.0]

- added ownership history to macguffins. use `/guffinhistory` to view.
- weekly recap posts every sunday
- UX fixes for the `/myrental` and `/achievements` flows

## [2.10.3.2]

synced plex collections with online portal
