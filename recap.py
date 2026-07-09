"""Weekly community recap: aggregates the last 7 days of rental, MacGuffin,
achievement, and game-leaderboard activity into a single feed post.

Pure aggregation over existing tables - no new integrations. Posts to the
same feed channel as achievement unlocks (`db.get_feed_channel_id`).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord

import achievements as achievement_module
import db
import embeds
import macguffin as macguffin_module

RECAP_WINDOW_DAYS = 7


async def post_weekly_recap(bot: discord.Client) -> bool:
    channel_id = await db.run(db.get_feed_channel_id)
    if not channel_id:
        print("[weekly-recap] No feed channel configured — skipping")
        return False

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden) as e:
            print(f"[weekly-recap] Couldn't access channel: {e}")
            return False

    embed = await db.run(_build_recap_embed)
    await channel.send(embed=embed)
    return True


def _build_recap_embed() -> discord.Embed:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=RECAP_WINDOW_DAYS)
    since_iso = since.isoformat()

    top_renters = db.get_weekly_top_renters(since_iso, limit=3)

    if not macguffin_module.CARDS:
        macguffin_module.load_cards()
    new_macguffins = db.get_macguffins_acquired_since(since_iso, limit=5)

    recent_unlocks = db.get_recent_achievement_unlocks(limit=50)
    new_achievements = [
        row for row in recent_unlocks if row.get("earned_at", "") >= since_iso
    ][:5]

    guess_leaders = db.get_leaderboard(limit=1)
    six_leaders = db.get_six_leaderboard(limit=1)

    return embeds.weekly_recap_embed(
        since=since,
        until=now,
        top_renters=top_renters,
        new_macguffins=new_macguffins,
        new_achievements=new_achievements,
        guess_leader=guess_leaders[0] if guess_leaders else None,
        six_leader=six_leaders[0] if six_leaders else None,
    )
