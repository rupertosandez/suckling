# Update Announcements

Member-facing copy for the Discord "i've been updated" announcement, separate from
the developer-focused `CHANGELOG.md`.

How it works:

- Each entry uses the same `## [version]` header as the changelog so the same parser
  can read it.
- When the bot announces a new version it looks here first. If a matching version is
  found, that casual copy is posted. If not, it falls back to the changelog entry, then
  to a generic message.
- Write in sentence case with a neutral tone (the voice from the 2026-07-15 portal
  copy pass; entries below from before that date are in the old casual lowercase
  voice). No em dashes. Skip the internal details (refactors, library names, db
  plumbing) unless a member would notice the difference. One or two short lines
  is plenty.

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

## [2.10.4]

sorry gang... returns now have a chance of not giving you a macguffin >.<

## [2.10.3.1]

database updates

## [2.10.0]

- added ownership history to macguffins. use `/guffinhistory` to view.
- weekly recap posts every sunday
- UX fixes for the `/myrental` and `/achievements` flows

## [2.10.3.2]

synced plex collections with online portal

## [2.10.3.3]

tiny bugfix for collection URL's in plex cache

## [2.10.4.1]

quiet plumbing behind the counter. the clerk is learning to take rental requests from the portal - nothing to click yet, but soon you won't have to walk to discord to check out a tape.

## [2.11.0]

rental functionality added to web portal

## [2.12.0]

returns and random rolls added to web portal

## [2.12.1]

roll polish: out of rerolls now means you pick your favorite of the three, same as discord

## [2.12.1.1]

bug fix for portal requests

## [2.13.0]

your watchlist lives on the portal now. add or drop films from any film page (even ones not in the library yet), tend the list from your profile, and spot your watchlisted films starring in watch together rooms.

## [2.13.1]

The "request it" link on films outside the library works again.

## [2.14.0]

You can now build your own collections on the portal. Put any films you like together, show the collection on your profile, and submit it for review. If it gets approved, it lands in Plex and on the Curation page with your name on it.

## [2.14.1]

Member avatars on the portal now stay up to date on their own, even if you never sign in there.

## [2.15.0]

Housekeeping behind the counter. Keep an eye on the MacGuffin pool though, you never know what might turn up in there.

## [2.16.0]

debug/troubleshooting tools

## [2.17.0]

The MacGuffin drop pool just got a lot deeper, and there are five new sets to complete, each with its own achievement. No spoilers. Happy hunting.
