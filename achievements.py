from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import discord

import db
import logger


MAX_DISPLAYED_ACHIEVEMENTS = 3
ROLE_PREFIX = "badge:"
ROLE_COLOR = discord.Color.from_rgb(142, 36, 52)


@dataclass(frozen=True)
class Achievement:
    id: str
    name: str
    description: str
    hint: str
    category: str
    threshold: int
    progress: Callable[[str], int]
    hidden: bool = False


def _returned_rentals(user_id: str) -> list[dict]:
    return [
        rental
        for rental in db.get_user_rental_history(user_id)
        if rental.get("status") == "returned"
    ]


def _returned_count(user_id: str) -> int:
    return len(_returned_rentals(user_id))


def _on_time_count(user_id: str) -> int:
    return sum(1 for rental in _returned_rentals(user_id) if not rental.get("late_fee_dollars"))


def _recommended_count(user_id: str) -> int:
    return sum(1 for rental in _returned_rentals(user_id) if rental.get("recommended"))


def _written_review_count(user_id: str) -> int:
    return sum(1 for rental in _returned_rentals(user_id) if (rental.get("thoughts") or "").strip())


def _perfect_rating_count(user_id: str) -> int:
    return sum(1 for rental in _returned_rentals(user_id) if rental.get("rating") == 10)


def _admin_pick_count(user_id: str) -> int:
    return sum(
        1 for rental in _returned_rentals(user_id)
        if rental.get("initiated_by") == "admin_recommended"
    )


def _random_no_reroll_count(user_id: str) -> int:
    return sum(
        1 for rental in _returned_rentals(user_id)
        if rental.get("initiated_by") in ("random", "command")
        and int(rental.get("rerolls_used") or 0) == 0
    )


def _decades_returned_count(user_id: str) -> int:
    decades = set()
    for rental in _returned_rentals(user_id):
        year = rental.get("year")
        if isinstance(year, int):
            decades.add((year // 10) * 10)
    return len(decades)


def _pre_1970_count(user_id: str) -> int:
    return sum(
        1 for rental in _returned_rentals(user_id)
        if isinstance(rental.get("year"), int) and rental["year"] < 1970
    )


def _current_decade_count(user_id: str) -> int:
    current_decade = (datetime.now(timezone.utc).year // 10) * 10
    return sum(
        1 for rental in _returned_rentals(user_id)
        if isinstance(rental.get("year"), int)
        and (rental["year"] // 10) * 10 == current_decade
    )


def _macguffin_count(user_id: str) -> int:
    return len(db.get_macguffin_inventory(user_id))


def _guess_wins(user_id: str) -> int:
    score = db.get_guess_score(user_id)
    return int(score.get("wins") or 0) if score else 0


def _six_wins(user_id: str) -> int:
    score = db.get_six_score(user_id)
    return int(score.get("wins") or 0) if score else 0


def _watchlist_count(user_id: str) -> int:
    return db.get_watchlist_count(user_id)


def _tracked_count(user_id: str) -> int:
    return db.tracked_movie_count_for_user(user_id)


def _letterboxd_linked(user_id: str) -> int:
    return 1 if db.get_lb_username(user_id) else 0


def _event_count(event_type: str) -> Callable[[str], int]:
    return lambda user_id: db.achievement_event_count(user_id, event_type)


ACHIEVEMENTS: tuple[Achievement, ...] = (
    Achievement("be-kind-rewind", "be kind, rewind", "returned your first rb9 rental.", "return your first rb9 rental.", "rentals", 1, _returned_count),
    Achievement("return-by-9", "return by 9", "returned 9 rentals on time.", "return 9 rentals on time.", "rentals", 9, _on_time_count),
    Achievement("the-final-cut", "the final cut", "returned 25 rentals.", "return 25 rentals.", "rentals", 25, _returned_count),
    Achievement("certified-sicko", "certified sicko", "returned 50 rentals.", "return 50 rentals.", "rentals", 50, _returned_count),
    Achievement("staff-pick-survivor", "staff pick survivor", "returned an admin-assigned rental.", "return a rental picked by an admin.", "rentals", 1, _admin_pick_count),
    Achievement("no-trailers-no-mercy", "no trailers, no mercy", "returned a random rental without rerolling.", "return a random rental without rerolling.", "rentals", 1, _random_no_reroll_count),
    Achievement("criterion-creature", "criterion creature", "returned rentals from 5 different decades.", "return rentals from 5 different decades.", "rentals", 5, _decades_returned_count),
    Achievement("grave-robber", "grave robber", "returned a pre-1970 rental.", "return something from before 1970.", "rentals", 1, _pre_1970_count),
    Achievement("fresh-blood", "fresh blood", "returned a rental from the current decade.", "return something from the current decade.", "rentals", 1, _current_decade_count),
    Achievement("two-thumbs-up", "two thumbs up", "recommended 10 returned rentals.", "recommend 10 rentals when returning them.", "reviews", 10, _recommended_count),
    Achievement("perfect-score", "perfect score", "gave a rental a 10/10.", "give a returned rental a 10/10.", "reviews", 1, _perfect_rating_count),
    Achievement("notes-app-auteur", "notes app auteur", "left written thoughts on 10 returned rentals.", "leave thoughts on 10 rental returns.", "reviews", 10, _written_review_count),
    Achievement("it-belongs-in-a-museum", "it belongs in a museum", "claimed your first macguffin.", "get your first macguffin.", "macguffins", 1, _macguffin_count),
    Achievement("prop-department", "prop department", "owned 5 macguffins.", "own 5 macguffins.", "macguffins", 5, _macguffin_count),
    Achievement("cursed-object-enjoyer", "cursed object enjoyer", "owned 10 macguffins.", "own 10 macguffins.", "macguffins", 10, _macguffin_count),
    Achievement("gift-shop", "the gift shop", "gifted a macguffin.", "gift a macguffin to another member.", "macguffins", 1, _event_count("macguffin_gift_sent")),
    Achievement("poster-child", "poster child", "won 5 guess/trivia rounds.", "win 5 /guess or /play rounds.", "games", 5, _guess_wins),
    Achievement("quote-machine", "quote machine", "won 10 guess/trivia rounds.", "win 10 /guess or /play rounds.", "games", 10, _guess_wins),
    Achievement("six-degrees-menace", "six degrees menace", "won 5 six degrees rounds.", "win 5 /six rounds.", "games", 5, _six_wins),
    Achievement("watchlist-whisperer", "watchlist whisperer", "kept 25 films on your watchlist.", "add 25 films to your watchlist.", "discovery", 25, _watchlist_count),
    Achievement("coming-soon", "coming soon", "tracked 10 movies.", "add 10 movies to streaming tracking.", "discovery", 10, _tracked_count),
    Achievement("letterboxed-in", "letterboxed in", "linked your letterboxd account.", "link your letterboxd account.", "letterboxd", 1, _letterboxd_linked),
)

ACHIEVEMENT_BY_ID = {achievement.id: achievement for achievement in ACHIEVEMENTS}


def visible_name(achievement: Achievement) -> str:
    return f"{ROLE_PREFIX} {achievement.name}"


def progress_for(user_id: str, achievement_id: str) -> tuple[int, int] | None:
    achievement = ACHIEVEMENT_BY_ID.get(achievement_id)
    if not achievement:
        return None
    return achievement.progress(user_id), achievement.threshold


def evaluate_user(
    user_id: str,
    user_tag: str,
    *,
    source_type: str | None = None,
    source_id: str | None = None,
) -> list[Achievement]:
    earned_ids = db.get_earned_achievement_ids(user_id)
    newly_earned: list[Achievement] = []
    for achievement in ACHIEVEMENTS:
        if achievement.id in earned_ids:
            continue
        try:
            value = achievement.progress(user_id)
        except Exception as e:
            logger.log_exception(f"achievement_progress:{achievement.id}", e)
            continue
        if value >= achievement.threshold and db.add_earned_achievement(
            user_id,
            achievement.id,
            user_tag,
            source_type,
            source_id,
        ):
            newly_earned.append(achievement)
    return newly_earned


async def post_unlocks(
    bot: discord.Client,
    user: discord.abc.User | discord.Member,
    achievements: list[Achievement],
    *,
    rental: dict | None = None,
) -> None:
    if not achievements:
        return
    channel_id = db.get_feed_channel_id()
    if not channel_id:
        return

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
    if not hasattr(channel, "send"):
        return

    for achievement in achievements:
        description = achievement.description
        if rental:
            year = f" ({rental.get('year')})" if rental.get("year") else ""
            description += f"\nreturned *{rental.get('title', 'a rental')}{year}*."
        embed = discord.Embed(
            title=f"achievement unlocked: {achievement.name}",
            description=description,
            color=ROLE_COLOR,
        )
        icon_url = getattr(getattr(user, "display_avatar", None), "url", None)
        if icon_url:
            embed.set_author(name=str(user), icon_url=icon_url)
        else:
            embed.set_author(name=str(user))
        try:
            await channel.send(content=user.mention, embed=embed)
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.log_exception("achievement_feed_post", e)


async def ensure_role(guild: discord.Guild, achievement: Achievement) -> discord.Role | None:
    role_id = db.get_achievement_role_id(achievement.id)
    if role_id:
        role = guild.get_role(role_id)
        if role:
            return role

    name = visible_name(achievement)
    existing = discord.utils.get(guild.roles, name=name)
    if existing:
        db.set_achievement_role_id(achievement.id, existing.id)
        return existing

    try:
        role = await guild.create_role(
            name=name,
            permissions=discord.Permissions.none(),
            color=ROLE_COLOR,
            hoist=False,
            mentionable=False,
            reason="Suckling achievement badge",
        )
        db.set_achievement_role_id(achievement.id, role.id)
        return role
    except (discord.Forbidden, discord.HTTPException) as e:
        logger.log_exception("achievement_role_create", e)
        return None


async def sync_member_roles(member: discord.Member) -> tuple[bool, str]:
    all_role_ids = db.get_all_achievement_role_ids()
    displayed = db.get_displayed_achievements(str(member.id))
    desired_ids = [row["achievement_id"] for row in displayed]
    desired_roles = []

    for achievement_id in desired_ids:
        achievement = ACHIEVEMENT_BY_ID.get(achievement_id)
        if not achievement:
            continue
        role = await ensure_role(member.guild, achievement)
        if role is None:
            return False, "i couldn't create or find one of those badge roles."
        desired_roles.append(role)
        all_role_ids.add(role.id)

    stale_roles = [
        role for role in member.roles
        if role.id in all_role_ids and role not in desired_roles
    ]
    to_add = [role for role in desired_roles if role not in member.roles]

    try:
        if stale_roles:
            await member.remove_roles(*stale_roles, reason="Suckling achievement badge sync")
        if to_add:
            await member.add_roles(*to_add, reason="Suckling achievement badge sync")
    except discord.Forbidden:
        return False, "i need manage roles, and my bot role has to be above badge roles."
    except discord.HTTPException as e:
        logger.log_exception("achievement_role_sync", e)
        return False, "discord wouldn't let me update those badge roles right now."

    return True, "badge roles synced."
