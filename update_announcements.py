from __future__ import annotations

import re
from pathlib import Path

import discord

import db
import version


UPDATE_ANNOUNCEMENT_CHANNEL_ID = 1509685120372834395
CHANGELOG_URL = "https://rupertosandez.github.io/sucklingsite/changelog/"
_CHANGELOG_PATH = Path(__file__).resolve().parent / "CHANGELOG.md"
_MAX_DESCRIPTION_LENGTH = 4096


def _iter_changelog_entries(changelog: str):
    header_pattern = re.compile(r"^## \[([^\]]+)\].*$", re.MULTILINE)
    matches = list(header_pattern.finditer(changelog))
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(changelog)
        yield match.group(1), changelog[start:end].strip()


def changelog_entry_for_version(bot_version: str) -> str | None:
    try:
        changelog = _CHANGELOG_PATH.read_text(encoding="utf-8")
    except OSError:
        return None

    for entry_version, body in _iter_changelog_entries(changelog):
        if entry_version != bot_version:
            continue
        lines = body.splitlines()
        if lines and lines[0].startswith("## "):
            lines = lines[1:]
        return "\n".join(lines).strip() or None
    return None


def _trim_description(text: str) -> str:
    if len(text) <= _MAX_DESCRIPTION_LENGTH:
        return text
    suffix = f"\n\n[ view full changelog ]({CHANGELOG_URL})"
    budget = _MAX_DESCRIPTION_LENGTH - len(suffix) - len("\n\n...")
    return text[: max(0, budget)].rstrip() + "\n\n..." + suffix


def update_announcement_embed(bot_version: str | None = None) -> discord.Embed:
    bot_version = bot_version or version.VERSION
    changelog_entry = changelog_entry_for_version(bot_version)
    lines = [
        f"yo check me out! i've been updated!!! v{bot_version} 💪",
    ]
    if changelog_entry:
        lines.extend(["", "**what changed**", changelog_entry])
    else:
        lines.extend(["", "no changelog details found for this version."])
    lines.extend(["", f"[ view changelog ]({CHANGELOG_URL})"])
    return discord.Embed(
        description=_trim_description("\n".join(lines)),
        color=0x8B0000,
    )


async def post_update_announcement(
    bot: discord.Client,
    *,
    mark_announced: bool = True,
) -> tuple[bool, str]:
    current_version = version.VERSION
    channel = bot.get_channel(UPDATE_ANNOUNCEMENT_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(UPDATE_ANNOUNCEMENT_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden) as e:
            return False, f"couldn't access update announcement channel: {e}"

    if not hasattr(channel, "send"):
        return False, "update announcement target is not a sendable channel."

    try:
        await channel.send(embed=update_announcement_embed(current_version))
    except discord.HTTPException as e:
        return False, f"failed to post update announcement: {e}"

    if mark_announced:
        db.set_last_update_announced_version(current_version)
    return True, f"posted update announcement for v{current_version}."
