from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord

import db
import logger
import macguffin as macguffin_module


MAX_DISPLAYED_ACHIEVEMENTS = 3
ROLE_COLOR = discord.Color.from_rgb(142, 36, 52)
UNLOCK_COLOR = discord.Color.gold()


@dataclass(frozen=True)
class Achievement:
    id: str
    name: str
    description: str
    hint: str
    category: str
    threshold: int
    progress: Callable[[str], int]
    emoji: str = "🏆"
    hidden: bool = False


def _returned_rentals(user_id: str) -> list[dict]:
    return [
        rental
        for rental in db.get_user_rental_history(user_id)
        if rental.get("status") == "returned"
    ]


def _returned_count(user_id: str) -> int:
    return len(_returned_rentals(user_id))


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _user_timezone(user_id: str) -> ZoneInfo:
    timezone_name = db.get_user_timezone(user_id)
    if timezone_name:
        try:
            return ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            pass
    return ZoneInfo("UTC")


def _on_time_count(user_id: str) -> int:
    return sum(1 for rental in _returned_rentals(user_id) if not rental.get("late_fee_dollars"))


def _late_fee_total(user_id: str) -> int:
    return int(sum(float(rental.get("late_fee_dollars") or 0) for rental in _returned_rentals(user_id)))


def _recommended_count(user_id: str) -> int:
    return sum(1 for rental in _returned_rentals(user_id) if rental.get("recommended"))


def _not_recommended_count(user_id: str) -> int:
    return sum(1 for rental in _returned_rentals(user_id) if rental.get("recommended") == 0)


def _balanced_taste_count(user_id: str) -> int:
    return min(_recommended_count(user_id), _not_recommended_count(user_id))


def _written_review_count(user_id: str) -> int:
    return sum(1 for rental in _returned_rentals(user_id) if (rental.get("thoughts") or "").strip())


def _perfect_rating_count(user_id: str) -> int:
    return sum(1 for rental in _returned_rentals(user_id) if rental.get("rating") == 10)


def _same_day_return_count(user_id: str) -> int:
    tz = _user_timezone(user_id)
    counts: dict[str, int] = {}
    for rental in _returned_rentals(user_id):
        returned = _parse_dt(rental.get("returned_at"))
        if not returned:
            continue
        day = returned.astimezone(tz).date().isoformat()
        counts[day] = counts.get(day, 0) + 1
    return max(counts.values(), default=0)


def _due_day_return_count(user_id: str) -> int:
    tz = _user_timezone(user_id)
    count = 0
    for rental in _returned_rentals(user_id):
        returned = _parse_dt(rental.get("returned_at"))
        due = _parse_dt(rental.get("due_at"))
        if returned and due and returned.astimezone(tz).date() == due.astimezone(tz).date():
            count += 1
    return count


def _after_midnight_return_count(user_id: str) -> int:
    tz = _user_timezone(user_id)
    count = 0
    for rental in _returned_rentals(user_id):
        returned = _parse_dt(rental.get("returned_at"))
        if returned and returned.astimezone(tz).hour < 5:
            count += 1
    return count


def _admin_pick_count(user_id: str) -> int:
    return sum(
        1 for rental in _returned_rentals(user_id)
        if rental.get("initiated_by") == "admin_recommended"
    )


def _random_rental_count(user_id: str) -> int:
    return sum(
        1 for rental in _returned_rentals(user_id)
        if rental.get("initiated_by") in ("random", "command")
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


def _pre_1980_count(user_id: str) -> int:
    return sum(
        1 for rental in _returned_rentals(user_id)
        if isinstance(rental.get("year"), int) and rental["year"] < 1980
    )


def _silent_era_adjacent_count(user_id: str) -> int:
    return sum(
        1 for rental in _returned_rentals(user_id)
        if isinstance(rental.get("year"), int) and rental["year"] <= 1929
    )


def _current_decade_count(user_id: str) -> int:
    current_decade = (datetime.now(timezone.utc).year // 10) * 10
    return sum(
        1 for rental in _returned_rentals(user_id)
        if isinstance(rental.get("year"), int)
        and (rental["year"] // 10) * 10 == current_decade
    )


def _modern_return_count(user_id: str) -> int:
    return sum(
        1 for rental in _returned_rentals(user_id)
        if isinstance(rental.get("year"), int) and rental["year"] >= 2020
    )


def _range_count(user_id: str) -> int:
    groups = set()
    for rental in _returned_rentals(user_id):
        year = rental.get("year")
        if not isinstance(year, int):
            continue
        if year < 1980:
            groups.add("pre-1980")
        elif 1980 <= year <= 1989:
            groups.add("1980s")
        elif 1990 <= year <= 1999:
            groups.add("1990s")
        elif 2000 <= year <= 2009:
            groups.add("2000s")
        elif 2010 <= year <= 2019:
            groups.add("2010s")
        elif year >= 2020:
            groups.add("2020s")
    return len(groups)


def _macguffin_count(user_id: str) -> int:
    return len(db.get_macguffin_inventory(user_id))


def _owns_suckling_macguffin(user_id: str) -> int:
    return 1 if db.user_owns_macguffin(user_id, "the-suckling") else 0


def _owns_iconic_macguffin(user_id: str) -> int:
    try:
        if not macguffin_module.CARDS:
            macguffin_module.load_cards()
    except Exception as e:
        logger.log_exception("achievement_iconic_macguffin_load", e)
        return 0
    for record in db.get_macguffin_inventory(user_id):
        card = macguffin_module.CARDS.get(record.get("macguffin_id"))
        if card and card.get("rarity") == "iconic":
            return 1
    return 0


def _game_wins(user_id: str) -> int:
    return _guess_wins(user_id) + _six_wins(user_id)


def _guess_wins(user_id: str) -> int:
    score = db.get_guess_score(user_id)
    return int(score.get("wins") or 0) if score else 0


def _six_wins(user_id: str) -> int:
    score = db.get_six_score(user_id)
    return int(score.get("wins") or 0) if score else 0


def _watchlist_count(user_id: str) -> int:
    return db.get_watchlist_count(user_id)


def _letterboxd_watchlist_count(user_id: str) -> int:
    return sum(1 for entry in db.get_watchlist(user_id) if entry.get("source") == "letterboxd")


def _tracked_count(user_id: str) -> int:
    return db.tracked_movie_count_for_user(user_id)


def _letterboxd_linked(user_id: str) -> int:
    return 1 if db.get_lb_username(user_id) else 0


def _event_count(event_type: str) -> Callable[[str], int]:
    return lambda user_id: db.achievement_event_count(user_id, event_type)


ACHIEVEMENTS: tuple[Achievement, ...] = (
    Achievement("be-kind-rewind", "be kind, rewind", "returned your first rb9 rental.", "return your first rb9 rental.", "rentals", 1, _returned_count, "📼"),
    Achievement("video-store-regular", "video store regular", "returned 5 rentals.", "return 5 rentals.", "rentals", 5, _returned_count, "🏪"),
    Achievement("double-feature", "double feature", "returned 2 rentals on the same day.", "return 2 rentals on the same day.", "rentals", 2, _same_day_return_count, "🍿"),
    Achievement("return-by-9", "return by 9", "returned 9 rentals on time.", "return 9 rentals on time.", "rentals", 9, _on_time_count, "⏰"),
    Achievement("clean-account", "clean account", "returned 10 rentals with zero late fees.", "return 10 rentals with zero late fees.", "rentals", 10, _on_time_count, "🧾"),
    Achievement("late-fee-legend", "late fee legend", "racked up $10 in late fees.", "rack up $10 in late fees.", "rentals", 10, _late_fee_total, "💸"),
    Achievement("last-minute-save", "last minute save", "returned a rental on its due date.", "return a rental on its due date.", "rentals", 1, _due_day_return_count, "⏳"),
    Achievement("still-up", "still up?", "returned a rental after midnight.", "return a rental after midnight.", "rentals", 1, _after_midnight_return_count, "🛏️"),
    Achievement("the-final-cut", "the final cut", "returned 25 rentals.", "return 25 rentals.", "rentals", 25, _returned_count, "✂️"),
    Achievement("certified-sicko", "certified sicko", "returned 50 rentals.", "return 50 rentals.", "rentals", 50, _returned_count, "🩸"),
    Achievement("staff-pick-survivor", "staff pick survivor", "returned an admin-assigned rental.", "return a rental picked by an admin.", "rentals", 1, _admin_pick_count, "🎟️"),
    Achievement("management-material", "management material", "returned 5 admin-assigned rentals.", "return 5 rentals picked by an admin.", "rentals", 5, _admin_pick_count, "🧑‍💼"),
    Achievement("dice-goblin", "dice goblin", "returned 10 random rentals.", "return 10 random rentals.", "rentals", 10, _random_rental_count, "🎲"),
    Achievement("no-trailers-no-mercy", "no trailers, no mercy", "returned a random rental without rerolling.", "return a random rental without rerolling.", "rentals", 1, _random_no_reroll_count, "🎲"),
    Achievement("criterion-creature", "criterion creature", "returned rentals from 5 different decades.", "return rentals from 5 different decades.", "rentals", 5, _decades_returned_count, "🎞️"),
    Achievement("time-traveler", "time traveler", "returned rentals from 7 different decades.", "return rentals from 7 different decades.", "rentals", 7, _decades_returned_count, "🗓️"),
    Achievement("range", "range", "returned rentals across 6 era buckets.", "return rentals from pre-1980, the 1980s, 1990s, 2000s, 2010s, and 2020s.", "rentals", 6, _range_count, "🌈"),
    Achievement("grave-robber", "grave robber", "returned a pre-1970 rental.", "return something from before 1970.", "rentals", 1, _pre_1970_count, "⚰️"),
    Achievement("old-soul", "old soul", "returned 5 rentals from before 1980.", "return 5 rentals from before 1980.", "rentals", 5, _pre_1980_count, "📽️"),
    Achievement("silent-era-adjacent", "silent era adjacent", "returned a rental from 1929 or earlier.", "return a rental from 1929 or earlier.", "rentals", 1, _silent_era_adjacent_count, "🪦"),
    Achievement("fresh-blood", "fresh blood", "returned a rental from the current decade.", "return something from the current decade.", "rentals", 1, _current_decade_count, "🩸"),
    Achievement("modern-problems", "modern problems", "returned 5 rentals from 2020 or later.", "return 5 rentals from 2020 or later.", "rentals", 5, _modern_return_count, "🧪"),
    Achievement("two-thumbs-up", "two thumbs up", "recommended 10 returned rentals.", "recommend 10 rentals when returning them.", "reviews", 10, _recommended_count, "👍"),
    Achievement("easy-recommend", "easy recommend", "recommended 25 returned rentals.", "recommend 25 rentals when returning them.", "reviews", 25, _recommended_count, "❤️"),
    Achievement("not-for-me", "not for me", "marked 5 rentals as not recommended.", "mark 5 rentals as not recommended.", "reviews", 5, _not_recommended_count, "🚫"),
    Achievement("balanced-taste", "balanced taste", "recommended at least 5 rentals and rejected at least 5.", "recommend 5 rentals and mark 5 as not recommended.", "reviews", 5, _balanced_taste_count, "⚖️"),
    Achievement("perfect-score", "perfect score", "gave a rental a 10/10.", "give a returned rental a 10/10.", "reviews", 1, _perfect_rating_count, "💯"),
    Achievement("taste-has-spoken", "taste has spoken", "gave three rentals a 10/10.", "give three returned rentals a 10/10.", "reviews", 3, _perfect_rating_count, "💯"),
    Achievement("notes-app-auteur", "notes app auteur", "left written thoughts on 10 returned rentals.", "leave thoughts on 10 rental returns.", "reviews", 10, _written_review_count, "📝"),
    Achievement("film-critic", "film critic", "left written thoughts on 25 returned rentals.", "leave thoughts on 25 rental returns.", "reviews", 25, _written_review_count, "📝"),
    Achievement("it-belongs-in-a-museum", "it belongs in a museum", "claimed your first macguffin.", "get your first macguffin.", "macguffins", 1, _macguffin_count, "🏛️"),
    Achievement("prop-department", "prop department", "owned 5 macguffins.", "own 5 macguffins.", "macguffins", 5, _macguffin_count, "🗝️"),
    Achievement("cursed-object-enjoyer", "cursed object enjoyer", "owned 10 macguffins.", "own 10 macguffins.", "macguffins", 10, _macguffin_count, "🔮"),
    Achievement("prop-collector", "prop collector", "owned 15 macguffins.", "own 15 macguffins.", "macguffins", 15, _macguffin_count, "🧳"),
    Achievement("iconic-behavior", "iconic behavior", "owned an iconic macguffin.", "own any iconic macguffin.", "macguffins", 1, _owns_iconic_macguffin, "👑"),
    Achievement("gift-shop", "the gift shop", "gifted a macguffin.", "gift a macguffin to another member.", "macguffins", 1, _event_count("macguffin_gift_sent"), "🎁"),
    Achievement("community-chest", "community chest", "gifted 3 macguffins.", "gift 3 macguffins.", "macguffins", 3, _event_count("macguffin_gift_sent"), "🤲"),
    Achievement("pass-it-on", "pass it on", "received a gifted macguffin.", "receive a gifted macguffin.", "macguffins", 1, _event_count("macguffin_gift_received"), "🔁"),
    Achievement("mutant-mommy", "mutant mommy", "held the iconic the suckling macguffin.", "hold the iconic the suckling macguffin.", "macguffins", 1, _owns_suckling_macguffin, "🍼"),
    Achievement("first-blood", "first blood", "won your first game.", "win any Suckling game.", "games", 1, _game_wins, "🎯"),
    Achievement("poster-child", "poster child", "won 5 guess/trivia rounds.", "win 5 /guess or /play rounds.", "games", 5, _guess_wins, "🎬"),
    Achievement("poster-child-ii", "poster child II", "won 25 guess/trivia rounds.", "win 25 /guess or /play rounds.", "games", 25, _guess_wins, "🎬"),
    Achievement("quote-machine", "quote machine", "won 10 guess/trivia rounds.", "win 10 /guess or /play rounds.", "games", 10, _guess_wins, "💬"),
    Achievement("trivia-goblin", "trivia goblin", "won 10 trivia roulette rounds.", "win 10 /play rounds.", "games", 10, _event_count("trivia_win"), "🧠"),
    Achievement("speedrun-brain", "speedrun brain", "won a guess or trivia round in 10 seconds.", "win /guess or /play in 10 seconds.", "games", 1, _event_count("speedrun_win"), "⚡"),
    Achievement("six-degrees-menace", "six degrees menace", "won 5 six degrees rounds.", "win 5 /six rounds.", "games", 5, _six_wins, "🕸️"),
    Achievement("connected-universe", "connected universe", "won 10 six degrees rounds.", "win 10 /six rounds.", "games", 10, _six_wins, "🕸️"),
    Achievement("watchlist-whisperer", "watchlist whisperer", "kept 25 films on your watchlist.", "add 25 films to your watchlist.", "discovery", 25, _watchlist_count, "📋"),
    Achievement("the-pile", "the pile", "kept 50 films on your watchlist.", "add 50 films to your watchlist.", "discovery", 50, _watchlist_count, "📋"),
    Achievement("fresh-start", "fresh start", "removed 10 films from your watchlist.", "remove 10 films from your watchlist.", "discovery", 10, _event_count("watchlist_remove"), "🧹"),
    Achievement("watchlist-importer", "watchlist importer", "imported a Letterboxd watchlist.", "import a Letterboxd watchlist.", "discovery", 1, _letterboxd_watchlist_count, "📚"),
    Achievement("coming-soon", "coming soon", "tracked 10 movies.", "add 10 movies to streaming tracking.", "discovery", 10, _tracked_count, "📣"),
    Achievement("town-crier", "town crier", "tracked 25 movies.", "add 25 movies to streaming tracking.", "discovery", 25, _tracked_count, "📣"),
    Achievement("stream-prophet", "stream prophet", "tracked a movie before it was announced as streaming.", "track a movie before it hits the streaming feed.", "discovery", 1, _event_count("stream_prophet"), "🔮"),
    Achievement("letterboxed-in", "letterboxed in", "linked your letterboxd account.", "link your letterboxd account.", "letterboxd", 1, _letterboxd_linked, "📗"),
)

ACHIEVEMENT_BY_ID = {achievement.id: achievement for achievement in ACHIEVEMENTS}


def _title_case_name(name: str) -> str:
    return " ".join(word if word.isupper() else word.capitalize() for word in name.split(" "))


def visible_name(achievement: Achievement) -> str:
    return f"{achievement.emoji} {_title_case_name(achievement.name)}"


def display_name(achievement: Achievement) -> str:
    return visible_name(achievement)


def legacy_visible_name(achievement: Achievement) -> str:
    return f"badge: {achievement.name}"


def progress_for(user_id: str, achievement_id: str) -> tuple[int, int] | None:
    achievement = ACHIEVEMENT_BY_ID.get(achievement_id)
    if not achievement:
        return None
    return achievement.progress(user_id), achievement.threshold


def record_event(
    user_id: str,
    user_tag: str,
    event_type: str,
    source_id: str | None = None,
) -> bool:
    try:
        db.record_achievement_event(user_id, user_tag, event_type, source_id)
    except Exception as e:
        logger.log_exception(f"achievement_event:{event_type}", e)
        return False
    return True


def unlock_embed(
    achievement: Achievement,
    *,
    user_label: str,
    user_mention: str | None = None,
    icon_url: str | None = None,
    rental_title: str | None = None,
) -> discord.Embed:
    label = user_mention or f"**{user_label}**"
    lines = [
        f"**{display_name(achievement)}**",
        "",
        f"{label} earned a new badge.",
        "",
        achievement.description,
    ]
    if rental_title:
        lines.extend([
            "",
            "**rental**",
            f"*{rental_title}*",
        ])

    embed = discord.Embed(
        title="🏆 Achievement Unlocked!",
        description="\n".join(lines),
        color=UNLOCK_COLOR,
    )
    embed.add_field(
        name="category",
        value=achievement.category,
        inline=True,
    )
    embed.set_footer(text="use /achievements to see your shelf")
    if icon_url:
        embed.set_author(name=user_label, icon_url=icon_url)
    else:
        embed.set_author(name=user_label)
    return embed


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


async def award_for_user(
    bot: discord.Client,
    user: discord.abc.User | discord.Member,
    *,
    source_type: str | None = None,
    source_id: str | None = None,
    rental: dict | None = None,
) -> list[Achievement]:
    unlocked = evaluate_user(
        str(user.id),
        str(user),
        source_type=source_type,
        source_id=source_id,
    )
    await post_unlocks(bot, user, unlocked, rental=rental)
    return unlocked


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
        rental_title = None
        if rental:
            year = f" ({rental.get('year')})" if rental.get("year") else ""
            rental_title = f"{rental.get('title', 'a rental')}{year}"
        icon_url = getattr(getattr(user, "display_avatar", None), "url", None)
        embed = unlock_embed(
            achievement,
            user_label=str(user),
            user_mention=user.mention,
            icon_url=icon_url,
            rental_title=rental_title,
        )
        try:
            await channel.send(content=user.mention, embed=embed)
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.log_exception("achievement_feed_post", e)


async def ensure_role(guild: discord.Guild, achievement: Achievement) -> discord.Role | None:
    role_id = db.get_achievement_role_id(achievement.id)
    if role_id:
        role = guild.get_role(role_id)
        if role:
            name = visible_name(achievement)
            if role.name != name:
                try:
                    await role.edit(name=name, reason="Suckling achievement badge rename")
                except (discord.Forbidden, discord.HTTPException) as e:
                    logger.log_exception("achievement_role_rename", e)
            return role

    name = visible_name(achievement)
    existing = discord.utils.get(guild.roles, name=name)
    if existing:
        db.set_achievement_role_id(achievement.id, existing.id)
        return existing

    legacy = discord.utils.get(guild.roles, name=legacy_visible_name(achievement))
    if legacy:
        try:
            await legacy.edit(name=name, reason="Suckling achievement badge rename")
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.log_exception("achievement_role_rename", e)
        db.set_achievement_role_id(achievement.id, legacy.id)
        return legacy

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
