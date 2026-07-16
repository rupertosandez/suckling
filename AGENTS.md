# codex project notes

## release workflow

when shipping bot updates, keep the public github pages site in sync alongside the bot repo.

- bot repo: `D:\git\Bots\sucklingbot`
- site repo: `D:\git\Sites\sucklingsite`
- site changelog: `D:\git\Sites\sucklingsite\CHANGELOG.md`
- site command reference: `D:\git\Sites\sucklingsite\COMMANDS.md`

for each public bot release:

- run `python scripts/prerelease_check.py` (against the live `DATABASE_URL` when the change touches `db.py` dialect handling) and confirm it passes.
- update the bot code and any internal project notes/changelog as needed.
- update the site `CHANGELOG.md` with member-facing release notes.
- update the site `COMMANDS.md` if commands were added, renamed, removed, or visibly changed.
- commit and push the bot repo and the site repo separately.

## site writing style

the github pages site is for rb9 community members, not maintainers.

- preserve jekyll front matter at the top of site pages.
- write in sentence case with a neutral tone (the voice from the 2026-07-15 portal copy pass). older site copy in the casual lowercase voice gets updated to match when touched.
- explain what changed for members and what the bot does now.
- avoid technical jargon, implementation details, dependency notes, database details, or internal architecture.
- prefer concise command-focused descriptions over developer release notes.
- when rewriting changelog entries, keep only functionality members would notice or care about.
