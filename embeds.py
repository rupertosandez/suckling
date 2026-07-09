from datetime import datetime

import discord

import achievements as achievement_module
import macguffin as macguffin_module
import tmdb
import trivia_roulette

MY_WATCHLIST_PAGE_SIZE = 10
LB_WATCHLIST_PAGE_SIZE = 5


SHUDDER_PROVIDER_NAME = "Shudder"
SEERR_BASE_URL = "https://seerr.cajou.enyo.bysh.me"

BOT_DESCRIPTION = (
    "a discord bot for the return by 9 movie community. looks up films, "
    "tracks streaming availability, runs poster guessing, trivia roulette, "
    "and six degrees games, and surfaces stats from the RB9 plex library."
)


def _format_date(d: datetime) -> str:
    if not hasattr(d, "strftime"):
        return str(d)
    formatted = d.strftime("%b %d, %Y")
    return formatted.replace(" 0", " ", 1)


def _format_uptime(seconds: float) -> str:
    """Convert seconds into a short 'Xd Yh Zm' / 'Yh Zm' / 'Zm' string."""
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _theaters_status(details: dict) -> str:
    theatrical_date = tmdb.get_theatrical_date(details)
    if not theatrical_date:
        return "🎬 **Theaters:** TBA"

    now = datetime.now()
    if theatrical_date > now:
        return f"🎬 **Theaters:** Releases {_format_date(theatrical_date)}"

    days_since = (now - theatrical_date).days
    if days_since <= 90:
        return f"🎬 **Theaters:** In theaters now (since {_format_date(theatrical_date)})"

    return f"🎬 **Theaters:** Released {_format_date(theatrical_date)}"


def _streaming_status(details: dict, providers: dict) -> str:
    has_digital_now = bool(
        providers.get("flatrate")
        or providers.get("rent")
        or providers.get("buy")
        or providers.get("free")
        or providers.get("ads")
    )
    justwatch_link = providers.get("link")

    if has_digital_now:
        if justwatch_link:
            return f"💻 **Streaming:** [Available now]({justwatch_link})"
        return "💻 **Streaming:** Available now"

    digital_date = tmdb.get_digital_date(details)
    now = datetime.now()
    if digital_date and digital_date > now:
        return f"💻 **Streaming:** Releases {_format_date(digital_date)}"

    theatrical_date = tmdb.get_theatrical_date(details)
    if theatrical_date and theatrical_date <= now:
        return "💻 **Streaming:** Not yet streaming"

    return "💻 **Streaming:** TBA"


def movie_embed(
    details: dict,
    providers: dict,
    in_theaters: bool,
    plex_available: bool | None = None,
) -> discord.Embed:
    title = details.get("title", "Unknown")
    release_date = details.get("release_date", "")
    year = release_date[:4] if release_date else "TBA"
    runtime = details.get("runtime")
    runtime_str = f"{runtime} min" if runtime else "Unknown"
    overview = details.get("overview") or "*No synopsis available.*"
    director = tmdb.get_director(details) or "Unknown"
    tmdb_url = f"https://www.themoviedb.org/movie/{details['id']}"

    embed = discord.Embed(
        title=f"{title} ({year})",
        description=overview,
        url=tmdb_url,
        color=0x01B4E4,
    )
    embed.add_field(name="Director", value=director, inline=True)
    embed.add_field(name="Runtime", value=runtime_str, inline=True)

    availability_lines = [
        _theaters_status(details),
        _streaming_status(details, providers),
    ]
    if plex_available is True:
        availability_lines.append("📀 **Return by 9:** In the library")
    elif plex_available is False:
        tmdb_id = details.get("id")
        if tmdb_id:
            seerr_url = f"{SEERR_BASE_URL}/movie/{tmdb_id}"
            availability_lines.append(
                f"📀 **Return by 9:** Not in the library · [request it]({seerr_url})"
            )
        else:
            availability_lines.append("📀 **Return by 9:** Not in the library")
    embed.add_field(name="Availability", value="\n".join(availability_lines), inline=False)

    poster = tmdb.poster_url(details.get("poster_path"))
    if poster:
        embed.set_thumbnail(url=poster)

    embed.set_footer(text="Data from TMDB")
    return embed


def streaming_announcement_embed(details: dict, new_providers: list[str]) -> discord.Embed:
    title = details.get("title", "Unknown")
    release_date = details.get("release_date", "")
    year = release_date[:4] if release_date else "TBA"
    overview = details.get("overview") or ""
    director = tmdb.get_director(details)
    tmdb_url = f"https://www.themoviedb.org/movie/{details['id']}"

    is_shudder = SHUDDER_PROVIDER_NAME in new_providers
    color = 0x8B0000 if is_shudder else 0x9B59B6
    emoji = "🩸" if is_shudder else "📺"

    if len(new_providers) == 1:
        header = f"{emoji} Now streaming on **{new_providers[0]}**"
    else:
        provider_list = ", ".join(f"**{p}**" for p in new_providers)
        header = f"{emoji} Now streaming on {provider_list}"

    if len(overview) > 300:
        overview = overview[:297].rstrip() + "..."

    description_parts = [header]
    if overview:
        description_parts.append("")
        description_parts.append(overview)

    embed = discord.Embed(
        title=f"{title} ({year})",
        description="\n".join(description_parts),
        url=tmdb_url,
        color=color,
    )

    if director:
        embed.add_field(name="Director", value=director, inline=True)

    poster = tmdb.poster_url(details.get("poster_path"))
    if poster:
        embed.set_thumbnail(url=poster)

    embed.set_footer(text="Data from TMDB")
    return embed


def digital_announcement_embed(details: dict, new_providers: list[str]) -> discord.Embed:
    title = details.get("title", "Unknown")
    release_date = details.get("release_date", "")
    year = release_date[:4] if release_date else "TBA"
    overview = details.get("overview") or ""
    director = tmdb.get_director(details)
    tmdb_url = f"https://www.themoviedb.org/movie/{details['id']}"

    if len(new_providers) == 1:
        header = f"📀 Now available to rent or buy on **{new_providers[0]}**"
    else:
        provider_list = ", ".join(f"**{p}**" for p in new_providers)
        header = f"📀 Now available to rent or buy on {provider_list}"

    if len(overview) > 300:
        overview = overview[:297].rstrip() + "..."

    description_parts = [header]
    if overview:
        description_parts.append("")
        description_parts.append(overview)

    embed = discord.Embed(
        title=f"{title} ({year})",
        description="\n".join(description_parts),
        url=tmdb_url,
        color=0x1ABC9C,
    )

    if director:
        embed.add_field(name="Director", value=director, inline=True)

    poster = tmdb.poster_url(details.get("poster_path"))
    if poster:
        embed.set_thumbnail(url=poster)

    embed.set_footer(text="Data from TMDB")
    return embed


def _cleanup_date_label(value) -> str:
    if not value:
        return "Never watched"
    try:
        return value.strftime("%b %-d, %Y")
    except ValueError:
        return value.strftime("%b %#d, %Y")


def plex_cleanup_embed(candidates: list) -> discord.Embed:
    embed = discord.Embed(
        title="Plex Cleanup Candidates",
        description=(
            "Big, quiet titles that are easy to stream elsewhere. "
            "Nothing has been removed; this is just the monthly review pile."
        ),
        color=0x8B0000,
    )

    if not candidates:
        embed.description = "No cleanup candidates this month."
        return embed

    for index, candidate in enumerate(candidates, start=1):
        providers = ", ".join(candidate.providers[:4])
        if len(candidate.providers) > 4:
            providers += f", +{len(candidate.providers) - 4} more"

        last_played = _cleanup_date_label(candidate.last_played)
        year = candidate.year or "????"
        value = (
            f"{candidate.size_gb:.1f} GB • {candidate.play_count} play(s) • {last_played}\n"
            f"Streaming on {providers}"
        )
        embed.add_field(
            name=f"{index}. {candidate.title} ({year})",
            value=value,
            inline=False,
        )

    embed.set_footer(text="Based on Plex size, Tautulli activity, and TMDB watch providers")
    return embed


def roll_embed(
    details: dict,
    providers: dict,
    plex_available: bool | None = None,
) -> discord.Embed:
    """Embed for /roll - same content as movie_embed but with a fun preamble."""
    embed = movie_embed(
        details,
        providers,
        in_theaters=False,
        plex_available=plex_available,
    )
    embed.title = f"🎲 {embed.title}"
    embed.color = 0x8B0000
    return embed


def daily_rec_embed(
    details: dict,
    providers: dict,
    plex_available: bool | None = None,
) -> discord.Embed:
    """Embed for the daily horror recommendation."""
    embed = movie_embed(
        details,
        providers,
        in_theaters=False,
        plex_available=plex_available,
    )
    embed.title = f"🩸 Today's Horror Pick: {embed.title}"
    embed.color = 0x8B0000
    return embed


def rb9_pick_embed(movie: dict) -> discord.Embed:
    """Embed for /rb9 - shows a random movie from the Return by 9 library."""
    title = movie.get("title", "Unknown")
    year = movie.get("year") or "?"
    summary = movie.get("summary") or "*No summary available.*"
    duration = movie.get("duration_minutes")

    if len(summary) > 500:
        summary = summary[:497].rstrip() + "..."

    embed = discord.Embed(
        title=f"📀 From Return by 9: {title} ({year})",
        description=summary,
        color=0xE5A00D,
    )

    if duration:
        embed.add_field(name="Runtime", value=f"{duration} min", inline=True)

    if movie.get("thumb_url"):
        embed.set_thumbnail(url=movie["thumb_url"])

    embed.set_footer(text="From the Return by 9 library")
    return embed


def _format_runtime_long(minutes: int) -> str:
    """Convert minutes into a 'X days, Y hours, Z minutes' string."""
    days = minutes // 1440
    remaining = minutes % 1440
    hours = remaining // 60
    mins = remaining % 60
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins and not days:
        parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
    return ", ".join(parts) if parts else "0 minutes"


def rb9_stats_embed(stats: dict) -> discord.Embed:
    """Overall library stats."""
    if stats.get("count", 0) == 0:
        return discord.Embed(
            title="📊 Return by 9 Library Stats",
            description="No movies found in the library.",
            color=0xE5A00D,
        )

    embed = discord.Embed(
        title="📊 Return by 9 Library Stats",
        color=0xE5A00D,
    )
    embed.add_field(name="Total Films", value=f"{stats['count']:,}", inline=True)

    if stats.get("total_minutes"):
        embed.add_field(
            name="Total Runtime",
            value=_format_runtime_long(stats["total_minutes"]),
            inline=True,
        )

    if stats.get("avg_rating") is not None:
        embed.add_field(
            name="Avg Rating",
            value=f"{stats['avg_rating']:.1f} ({stats['rated_count']} rated)",
            inline=True,
        )

    if stats.get("min_year") and stats.get("max_year"):
        embed.add_field(
            name="Year Range",
            value=f"{stats['min_year']} - {stats['max_year']}",
            inline=True,
        )

    if stats.get("newest_added"):
        added = stats["newest_added"]
        embed.add_field(
            name="Most Recently Added",
            value=f"{added['title']} ({added['year']})",
            inline=False,
        )

    embed.set_footer(text="From the Return by 9 library")
    return embed


def rb9_single_movie_embed(movie: dict, label: str, emoji: str = "📀") -> discord.Embed:
    """Generic embed for a single-movie stat (longest, shortest, oldest, etc)."""
    title = movie.get("title", "Unknown")
    year = movie.get("year") or "?"
    duration = movie.get("duration_minutes")

    embed = discord.Embed(
        title=f"{emoji} {label}",
        description=f"**{title} ({year})**",
        color=0xE5A00D,
    )

    if duration:
        embed.add_field(name="Runtime", value=f"{duration} min", inline=True)

    if movie.get("thumb_url"):
        embed.set_thumbnail(url=movie["thumb_url"])

    embed.set_footer(text="From the Return by 9 library")
    return embed


def rb9_total_runtime_embed(stats: dict) -> discord.Embed:
    """Fun embed for /rb9totalruntime."""
    count = stats.get("count", 0)
    total_minutes = stats.get("total_minutes", 0)
    days = stats.get("total_days", 0)
    weeks = stats.get("total_weeks", 0)
    realistic = stats.get("realistic_days_at_8h", 0)

    desc_lines = [
        f"Return by 9 has **{count:,} movies** with a combined runtime of:",
        "",
        f"- **{days:.1f} days** of nonstop watching",
        f"- **{weeks:.1f} weeks**",
        "",
        f"At a more reasonable 8 hours/day, you'd finish in **{realistic:.0f} days**.",
    ]

    embed = discord.Embed(
        title="Total Return by 9 Library Runtime",
        description="\n".join(desc_lines),
        color=0xE5A00D,
    )
    embed.set_footer(text="From the Return by 9 library")
    return embed


def rb9_decade_embed(decades: list[tuple[str, int]]) -> discord.Embed:
    """Bar-chart-ish breakdown of films per decade."""
    if not decades:
        return discord.Embed(
            title="📅 Return by 9 by Decade",
            description="No data available.",
            color=0xE5A00D,
        )

    max_count = max(count for _, count in decades)
    bar_width = 20

    lines = []
    for decade, count in decades:
        bar_length = max(1, round((count / max_count) * bar_width))
        bar = "█" * bar_length
        lines.append(f"`{decade}` {bar} **{count}**")

    embed = discord.Embed(
        title="📅 Return by 9 Library by Decade",
        description="\n".join(lines),
        color=0xE5A00D,
    )
    embed.set_footer(text="From the Return by 9 library")
    return embed


def rb9_genre_embed(genres: list[tuple[str, int]]) -> discord.Embed:
    """Top genres in the library."""
    if not genres:
        return discord.Embed(
            title="🎭 Top Genres",
            description="No genre data available.",
            color=0xE5A00D,
        )

    max_count = genres[0][1]
    bar_width = 20

    lines = []
    for name, count in genres:
        bar_length = max(1, round((count / max_count) * bar_width))
        bar = "█" * bar_length
        lines.append(f"**{name}** - {bar} {count}")

    embed = discord.Embed(
        title="🎭 Top Genres in Return by 9",
        description="\n".join(lines),
        color=0xE5A00D,
    )
    embed.set_footer(text="From the Return by 9 library")
    return embed


def rb9_random_scene_embed(scene: dict) -> discord.Embed:
    """Embed for /rb9randomscene - random film backdrop."""
    title = scene.get("title", "Unknown")
    year = scene.get("year") or "?"
    summary = scene.get("summary") or ""

    if len(summary) > 200:
        summary = summary[:197].rstrip() + "..."

    embed = discord.Embed(
        title=f"🎬 Random Scene: {title} ({year})",
        description=summary,
        color=0xE5A00D,
    )

    if scene.get("art_url"):
        embed.set_image(url=scene["art_url"])

    embed.set_footer(text="From the Return by 9 library")
    return embed


# ---------- trivia roulette ----------

def trivia_prompt_embed(category: str, prompt: str, started_by: str) -> discord.Embed:
    """Round-start embed showing the category and the clue."""
    meta = trivia_roulette.CATEGORIES.get(category, {})
    cat_emoji = meta.get("emoji", "🎲")
    cat_label = meta.get("label", category)
    color = meta.get("color", 0x808080)

    embed = discord.Embed(
        title=f"{cat_emoji} RB9 Roulette: {cat_label}!",
        description=prompt,
        color=color,
    )
    embed.set_footer(text=f"Started by {started_by} · 30 seconds to guess")
    return embed


def trivia_reveal_embed(
    category: str,
    answer: str,
    year: int | None,
    winner_tag: str | None = None,
    new_total: int | None = None,
) -> discord.Embed:
    """
    Reveal embed for both win and timeout. If winner_tag is set, shows the
    winner; otherwise shows a time's-up message. If new_total is provided
    alongside a winner, the running total is shown below the answer.
    """
    meta = trivia_roulette.CATEGORIES.get(category, {})
    cat_label = meta.get("label", category)
    color = meta.get("color", 0x808080)

    year_str = f" ({year})" if year else ""

    if winner_tag:
        title = f"✅ {winner_tag} got it!"
        description = f"**{answer}**{year_str}"
        if new_total is not None:
            description += f"\n\n+1 point · total: **{new_total}**"
    else:
        title = "⏰ Time's Up!"
        description = f"The answer was **{answer}**{year_str}"

    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text=f"Category: {cat_label}")
    return embed


# ---------- info ----------

def info_embed(version: str, uptime_seconds: float, guild_count: int) -> discord.Embed:
    """About card for /info. References attachment://logo.png for the wordmark banner."""
    embed = discord.Embed(
        title=f"Suckling v{version}",
        description=BOT_DESCRIPTION,
        color=0x8B0000,
    )

    embed.add_field(name="Uptime", value=_format_uptime(uptime_seconds), inline=True)
    server_word = "server" if guild_count == 1 else "servers"
    embed.add_field(name="Serving", value=f"{guild_count} {server_word}", inline=True)
    embed.add_field(name="Commands", value="type `/` to see them", inline=True)

    embed.set_image(url="attachment://logo.png")
    embed.set_footer(text="caj's little mutant")
    return embed


# ---------- admin ----------

def bot_status_embed(status: dict) -> discord.Embed:
    """Admin dashboard for /botstatus."""
    embed = discord.Embed(
        title="bot status",
        description="admin overview for the current runtime and configured features.",
        color=0x8B0000,
    )

    embed.add_field(
        name="runtime",
        value=(
            f"version: **{status['version']}**\n"
            f"uptime: **{_format_uptime(status['uptime_seconds'])}**\n"
            f"latency: **{status['latency_ms']:.0f} ms**\n"
            f"cache: **{status['cache_size']}** entries"
        ),
        inline=True,
    )
    embed.add_field(
        name="counts",
        value=(
            f"tracked films: **{status['tracked_count']}**\n"
            f"linked lb accounts: **{status['lb_account_count']}**\n"
            f"active rentals: **{status['active_rental_count']}**\n"
            f"overdue rentals: **{status['overdue_rental_count']}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="channels",
        value=(
            f"reviews: {status['reviews_channel']}\n"
            f"rental requests: {status['rental_request_channel']}\n"
            f"streaming: {status['announcement_channel']}\n"
            f"daily rec: {status['daily_channel']}\n"
            f"lb activity: {status['lb_activity_channel']}"
        ),
        inline=False,
    )
    embed.add_field(
        name="auto-posting",
        value=(
            f"streaming announcements: **{status['announcements_enabled']}**\n"
            f"daily recommendation: **{status['daily_rec_enabled']}**\n"
            f"letterboxd activity: **{status['lb_activity_enabled']}**"
        ),
        inline=False,
    )

    warnings = status.get("warnings") or []
    if warnings:
        embed.add_field(name="needs attention", value="\n".join(warnings), inline=False)

    embed.set_footer(text="admin only")
    return embed


def lb_linked_embed(
    accounts: list[dict],
    *,
    page: int,
    total_pages: int,
    total: int,
) -> discord.Embed:
    """Admin roster of linked Letterboxd accounts."""
    embed = discord.Embed(
        title="linked letterboxd accounts",
        color=0x8B0000,
    )

    if not accounts:
        embed.description = "no linked letterboxd accounts yet."
        return embed

    lines = []
    for account in accounts:
        username = account.get("lb_username", "unknown")
        discord_label = account.get("discord_label", "unknown member")
        linked_at = account.get("linked_at_display", "unknown date")
        missing = " - left server?" if not account.get("in_server", True) else ""
        lines.append(
            f"**{discord_label}**{missing}\n"
            f"<https://letterboxd.com/{username}/> - linked {linked_at}"
        )

    embed.description = "\n\n".join(lines)
    embed.set_footer(text=f"{total} linked account(s) - page {page + 1}/{total_pages}")
    return embed


# ---------- rentals ----------

def rental_offer_embed(movie: dict, is_last_reroll: bool = False) -> discord.Embed:
    """
    Shown during the /rent reroll flow (ephemeral). Displays the current
    film offer before the user commits.
    """
    title = movie.get("title", "Unknown")
    year = movie.get("year") or "?"
    summary = movie.get("summary") or "*No summary available.*"
    duration = movie.get("duration_minutes")

    if len(summary) > 400:
        summary = summary[:397].rstrip() + "..."

    desc = summary
    if is_last_reroll:
        desc += (
            "\n\n-# this is your last reroll - after it you'll get to pick "
            "from any of the films you've been shown."
        )

    embed = discord.Embed(
        title=f"📼 Your Rental: {title} ({year})",
        description=desc,
        color=0xE5A00D,
    )

    if duration:
        embed.add_field(name="Runtime", value=f"{duration} min", inline=True)

    if movie.get("thumb_url"):
        embed.set_thumbnail(url=movie["thumb_url"])

    embed.set_footer(text="From the Return by 9 library")
    return embed


def rental_confirmed_embed(movie: dict, user_tag: str, due_at: datetime) -> discord.Embed:
    """
    Forum thread opener posted when a rental is confirmed. Edited to a
    review embed when the user returns the film.
    """
    title = movie.get("title", "Unknown")
    year = movie.get("year") or "?"
    summary = movie.get("summary") or "*No summary available.*"
    duration = movie.get("duration_minutes")

    if len(summary) > 400:
        summary = summary[:397].rstrip() + "..."

    due_ts = int(due_at.timestamp())

    embed = discord.Embed(
        title=f"📼 {title} ({year})",
        description=summary,
        color=0xE5A00D,
    )

    embed.add_field(name="Checked Out By", value=user_tag, inline=True)
    if duration:
        embed.add_field(name="Runtime", value=f"{duration} min", inline=True)
    embed.add_field(
        name="Due Back",
        value=f"<t:{due_ts}:F> (<t:{due_ts}:R>)",
        inline=False,
    )

    if movie.get("thumb_url"):
        embed.set_thumbnail(url=movie["thumb_url"])

    embed.set_footer(text="Use /return to post your review when you're done")
    return embed


def rental_review_embed(
    movie: dict,
    user_tag: str,
    rating: int | None,
    thoughts: str | None,
    recommend: bool,
    returned_at_iso: str,
    late_fee: float,
) -> discord.Embed:
    """
    Replaces the confirmed embed in the forum thread after /return is used.
    """
    title = movie.get("title", "Unknown")
    year = movie.get("year") or "?"
    is_late = late_fee > 0

    color = 0xED4245 if is_late else 0x57F287  # red if late, green if on time
    header = f"{'🔴 Late Return' if is_late else '✅ Returned'} by {user_tag}"

    try:
        returned_dt = datetime.fromisoformat(returned_at_iso)
        returned_ts = int(returned_dt.timestamp())
        returned_str = f"<t:{returned_ts}:F>"
    except (ValueError, TypeError):
        returned_str = "unknown"

    embed = discord.Embed(
        title=f"📼 {title} ({year})",
        color=color,
    )

    desc_parts = [f"**{header}**"]
    if thoughts:
        desc_parts.append(f"\n{thoughts}")
    embed.description = "\n".join(desc_parts)

    if rating is None:
        embed.add_field(name="Rating", value="No rating", inline=False)
    else:
        embed.add_field(name="Rating", value=f"{rating}/10", inline=False)
    embed.add_field(name="Recommend?", value="👍" if recommend else "👎", inline=True)
    embed.add_field(name="Returned", value=returned_str, inline=True)

    if is_late:
        embed.add_field(name="Late Fee", value=f"${late_fee:.2f}", inline=True)

    if movie.get("poster_url") or movie.get("thumb_url"):
        embed.set_thumbnail(url=movie.get("poster_url") or movie.get("thumb_url"))

    embed.set_footer(text="From the Return by 9 library")
    return embed


def rental_unwatched_return_embed(
    movie: dict,
    user_tag: str,
    returned_at_iso: str,
    late_fee: float,
    reason: str | None = None,
) -> discord.Embed:
    """Replaces the confirmed embed when a rental is returned unwatched."""
    title = movie.get("title", "Unknown")
    year = movie.get("year") or "?"
    is_late = late_fee > 0

    try:
        returned_dt = datetime.fromisoformat(returned_at_iso)
        returned_ts = int(returned_dt.timestamp())
        returned_str = f"<t:{returned_ts}:F>"
    except (ValueError, TypeError):
        returned_str = "unknown"

    embed = discord.Embed(
        title=f"📼 {title} ({year})",
        description=(
            f"**↩ Returned unwatched by {user_tag}**\n\n"
            "No review posted, no rating recorded."
            + (f"\n\nReason: {reason}" if reason else "")
        ),
        color=0x808080 if not is_late else 0xED4245,
    )
    embed.add_field(name="Returned", value=returned_str, inline=True)
    if is_late:
        embed.add_field(name="Late Fee", value=f"${late_fee:.2f}", inline=True)

    if movie.get("poster_url") or movie.get("thumb_url"):
        embed.set_thumbnail(url=movie.get("poster_url") or movie.get("thumb_url"))

    embed.set_footer(text="From the Return by 9 library")
    return embed


def rental_cancelled_embed(
    movie: dict,
    user_tag: str,
    reason: str | None = None,
) -> discord.Embed:
    """Replaces the forum thread opener when an admin cancels a rental."""
    title = movie.get("title", "Unknown")
    year = movie.get("year") or "?"

    embed = discord.Embed(
        title=f"📼 {title} ({year})",
        description=f"Rental by **{user_tag}** was cancelled by an admin."
        + (f"\n\nReason: {reason}" if reason else ""),
        color=0x808080,
    )

    if movie.get("poster_url") or movie.get("thumb_url"):
        embed.set_thumbnail(url=movie.get("poster_url") or movie.get("thumb_url"))

    embed.set_footer(text="From the Return by 9 library")
    return embed


def rental_status_embed(rental: dict) -> discord.Embed:
    """Shown by /myrental - current rental status for the requesting user."""
    title = rental.get("title", "Unknown")
    year = rental.get("year") or "?"
    due_at_iso = rental.get("due_at", "")
    rented_at_iso = rental.get("rented_at", "")

    now = datetime.now(datetime.now().astimezone().tzinfo)

    try:
        due = datetime.fromisoformat(due_at_iso)
        due_ts = int(due.timestamp())
        due_str = f"<t:{due_ts}:F> (<t:{due_ts}:R>)"
        is_overdue = due < datetime.now(due.tzinfo)
    except (ValueError, TypeError):
        due_str = "unknown"
        is_overdue = False

    try:
        rented = datetime.fromisoformat(rented_at_iso)
        rented_ts = int(rented.timestamp())
        rented_str = f"<t:{rented_ts}:F>"
    except (ValueError, TypeError):
        rented_str = "unknown"

    color = 0xED4245 if is_overdue else 0xE5A00D

    embed = discord.Embed(
        title=f"📼 {title} ({year})",
        description="⚠️ This rental is overdue." if is_overdue else None,
        color=color,
    )

    embed.add_field(name="Checked Out", value=rented_str, inline=True)
    embed.add_field(name="Due Back", value=due_str, inline=True)

    rerolls = rental.get("rerolls_used", 0)
    if rerolls:
        embed.add_field(name="Rerolls Used", value=str(rerolls), inline=True)

    extensions = rental.get("extensions_used", 0)
    if extensions:
        embed.add_field(name="Extensions Used", value=str(extensions), inline=True)

    thread_id = rental.get("thread_id")
    if thread_id:
        embed.add_field(
            name="Forum Thread",
            value=f"<#{thread_id}>",
            inline=False,
        )

    if rental.get("poster_url"):
        embed.set_thumbnail(url=rental["poster_url"])

    embed.set_footer(text="Use /return to post your review when you're done")
    return embed


def rental_status_list_embed(
    rentals: list[dict],
    user_tag: str,
    max_active: int,
) -> discord.Embed:
    """Shown by /myrental when a user has multiple active rentals."""
    any_overdue = False
    lines = []
    for rental in rentals:
        title = rental.get("title", "Unknown")
        year = rental.get("year") or "?"
        due_at_iso = rental.get("due_at", "")
        try:
            due = datetime.fromisoformat(due_at_iso)
            due_str = f"<t:{int(due.timestamp())}:R>"
            is_overdue = due < datetime.now(due.tzinfo)
        except (ValueError, TypeError):
            due_str = "due time unknown"
            is_overdue = False
        any_overdue = any_overdue or is_overdue

        line = f"`{rental['id']}` **{title} ({year})** - {'⚠️ overdue' if is_overdue else due_str}"

        thread_id = rental.get("thread_id")
        if thread_id:
            line += f" - <#{thread_id}>"

        extensions = rental.get("extensions_used", 0)
        if extensions:
            line += " - extended"

        lines.append(line)

    embed = discord.Embed(
        title=f"📼 Active Rentals - {user_tag}",
        description="\n".join(lines),
        color=0xED4245 if any_overdue else 0xE5A00D,
    )
    embed.set_footer(
        text=f"{len(rentals)}/{max_active} active - use rental ID with /return or /extend"
    )
    return embed


def late_fees_embed(rows: list[dict]) -> discord.Embed:
    """Leaderboard of accumulated late fees."""
    if not rows:
        return discord.Embed(
            title="🏪 Late Fee Ledger",
            description="No late fees on record. Everyone's been returning on time.",
            color=0xE5A00D,
        )

    lines = []
    medals = ["🥇", "🥈", "🥉"]
    for i, row in enumerate(rows):
        prefix = medals[i] if i < 3 else f"#{i + 1}"
        name = row.get("user_name", "unknown")
        fees = row.get("total_fees", 0)
        late_count = row.get("late_count", 0)
        lines.append(
            f"{prefix} **{name}** - ${fees:.2f} "
            f"({late_count} late return{'s' if late_count != 1 else ''})"
        )

    embed = discord.Embed(
        title="🏪 Late Fee Ledger",
        description="\n".join(lines),
        color=0xE5A00D,
    )
    embed.set_footer(text="$1/day for every day overdue")
    return embed


RENTAL_HISTORY_PAGE_SIZE = 8


def _clip_text(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."


def _rental_history_status_emoji(rental: dict) -> str:
    status = rental.get("status", "unknown")
    if status == "returned_unwatched":
        return "↩"

    late_fee = float(rental.get("late_fee_dollars") or 0)
    if late_fee > 0:
        return "🔴"

    if status == "active":
        try:
            due = datetime.fromisoformat(rental.get("due_at", ""))
            if due < datetime.now(due.tzinfo):
                return "🔴"
        except (ValueError, TypeError):
            pass
        return "📼"

    if status == "returned":
        return "✅"

    if status == "cancelled":
        return "⚪"

    return "•"



def _rental_history_title(rental: dict) -> str:
    title = rental.get("title", "Unknown")
    year = rental.get("year")
    year_str = f" ({year})" if year else ""
    return _clip_text(f"{_rental_history_status_emoji(rental)} {title}{year_str}", 256)


def _rental_history_details(rental: dict, *, include_divider: bool = False) -> str:
    status = rental.get("status")
    if status == "returned_unwatched":
        details = ["Unwatched"]
    elif status == "cancelled":
        details = ["Cancelled"]
    else:
        details = [f"{rental['rating']}/10" if rental.get("rating") else "Unrated"]

    if rental.get("recommended") is not None:
        details.append("👍" if rental.get("recommended") else "👎")

    detail_text = " · ".join(details)
    if include_divider:
        detail_text += "\n\n────────────"
    return detail_text


def rental_stats_embed(
    history: list[dict],
    user_tag: str,
    page: int = 0,
    total_pages: int | None = None,
) -> discord.Embed:
    """Personal rental stats for /rentalstats."""
    if not history:
        return discord.Embed(
            title=f"📼 Rental History - {user_tag}",
            description="No rentals yet.",
            color=0xE5A00D,
        )

    total = len(history)
    completed = [
        r for r in history
        if r["status"] in ("returned", "returned_unwatched")
    ]
    on_time = [r for r in completed if r.get("late_fee_dollars", 0) == 0]
    late = [r for r in completed if r.get("late_fee_dollars", 0) > 0]
    total_fees = sum(r.get("late_fee_dollars", 0) for r in history)
    embed = discord.Embed(
        title=f"📼 Rental History - {user_tag}",
        color=0xE5A00D,
    )

    embed.add_field(name="Total Rentals", value=str(total), inline=True)
    embed.add_field(name="Returned on Time", value=str(len(on_time)), inline=True)
    embed.add_field(name="Returned Late", value=str(len(late)), inline=True)

    if total_fees > 0:
        embed.add_field(name="Total Late Fees", value=f"${total_fees:.2f}", inline=True)

    total_pages = total_pages or max(1, -(-len(history) // RENTAL_HISTORY_PAGE_SIZE))
    page = min(max(page, 0), total_pages - 1)
    start = page * RENTAL_HISTORY_PAGE_SIZE
    page_history = history[start : start + RENTAL_HISTORY_PAGE_SIZE]

    if page_history:
        for index, rental in enumerate(page_history):
            embed.add_field(
                name=_rental_history_title(rental),
                value=_rental_history_details(
                    rental,
                    include_divider=index < len(page_history) - 1,
                ),
                inline=False,
            )

    embed.set_footer(text=f"{total} rental(s) - page {page + 1}/{total_pages}")
    return embed


# ---------- letterboxd ----------

_LB_COLOR = 0x00C030  # Letterboxd green


def _lb_rating_text(entry: dict) -> str:
    stars = entry.get("stars", "")
    rating = entry.get("rating")
    if rating is None:
        return stars or "unrated"
    rating_text = f"{rating:g}/5"
    return f"{stars} ({rating_text})" if stars else rating_text


def lb_profile_embed(lb_username: str, entries: list[dict], discord_tag: str | None = None) -> discord.Embed:
    """
    Recent Letterboxd diary entries for a user.
    Shows up to 8 entries with ratings and watch dates.
    """
    lb_url = f"https://letterboxd.com/{lb_username}/"

    embed = discord.Embed(
        title=f"🎬 {lb_username}'s recent watches",
        url=lb_url,
        color=_LB_COLOR,
    )

    if not entries:
        embed.description = "no diary entries found - the account might be empty or private."
        return embed

    lines = []
    for entry in entries[:8]:
        title = entry.get("film_title", "Unknown")
        year = entry.get("year")
        stars = entry.get("stars", "")
        date = entry.get("watch_date", "")
        rewatch = entry.get("rewatch", False)
        link = entry.get("link", "")

        year_str = f" ({year})" if year else ""
        stars_str = f" {stars}" if stars else ""
        rewatch_str = " ↩" if rewatch else ""
        date_str = f" · {date}" if date else ""

        if link:
            line = f"[{title}{year_str}]({link}){stars_str}{rewatch_str}{date_str}"
        else:
            line = f"**{title}{year_str}**{stars_str}{rewatch_str}{date_str}"

        review = entry.get("review")
        if review:
            line += f"\n-# *{review}*"

        lines.append(line)

    embed.description = "\n\n".join(lines)
    embed.set_footer(text=f"Letterboxd - {lb_username}")
    return embed


def lb_activity_embed(
    lb_username: str,
    entry: dict,
    discord_tag: str | None = None,
) -> discord.Embed:
    """Single Letterboxd diary entry for the auto-posting activity feed."""
    title = entry.get("film_title", "Unknown")
    year = entry.get("year")
    link = entry.get("link", "")
    watch_date = entry.get("watch_date", "")
    rewatch = entry.get("rewatch", False)
    review = entry.get("review")
    thumb = entry.get("thumb")

    year_str = f" ({year})" if year else ""
    who = discord_tag or lb_username
    embed = discord.Embed(
        title=f"{title}{year_str}",
        url=link or None,
        description=f"**{who}** logged a watch on Letterboxd.",
        color=_LB_COLOR,
    )

    rating_text = _lb_rating_text(entry)
    embed.add_field(name="Rating", value=rating_text, inline=True)

    details = []
    if watch_date:
        details.append(watch_date)
    if rewatch:
        details.append("rewatch")
    if details:
        embed.add_field(name="Watched", value=" - ".join(details), inline=True)

    if review:
        embed.add_field(name="Review", value=f"*{review}*", inline=False)

    if thumb:
        embed.set_thumbnail(url=thumb)
    embed.set_footer(text=f"Letterboxd - {lb_username}")
    return embed


def lb_activity_compact_embed(
    lb_username: str,
    entries: list[dict],
    discord_tag: str | None = None,
) -> discord.Embed:
    """Compact auto-post for several recent Letterboxd diary entries."""
    who = discord_tag or lb_username
    count = len(entries)
    noun = "watch" if count == 1 else "watches"
    lb_url = f"https://letterboxd.com/{lb_username}/"

    lines = []
    shown = 0
    max_description_chars = 3900
    intro = f"**{who}** logged {count} {noun} on Letterboxd."
    for entry in entries:
        title = entry.get("film_title", "Unknown")
        year = entry.get("year")
        link = entry.get("link", "")
        watch_date = entry.get("watch_date", "")
        rewatch = entry.get("rewatch", False)

        year_str = f" ({year})" if year else ""
        if link:
            line = f"- [{title}{year_str}]({link})"
        else:
            line = f"- **{title}{year_str}**"

        details = []
        rating_text = _lb_rating_text(entry)
        if rating_text != "unrated":
            details.append(rating_text)
        if watch_date:
            details.append(watch_date)
        if rewatch:
            details.append("rewatch")
        if details:
            line += f" - {' - '.join(details)}"

        next_description = intro + "\n\n" + "\n".join([*lines, line])
        if len(next_description) > max_description_chars:
            break
        lines.append(line)
        shown += 1

    description = intro
    if lines:
        description += "\n\n" + "\n".join(lines)
    if shown < count:
        description += f"\n\nPlus {count - shown} more."

    embed = discord.Embed(
        title="Letterboxd Catch-Up",
        url=lb_url,
        description=description,
        color=_LB_COLOR,
    )
    embed.set_footer(text=f"Letterboxd - {lb_username}")
    return embed


def lb_watchlist_embed(
    lb_username: str,
    films: list[dict],
    page: int,
    total_pages: int,
) -> discord.Embed:
    """Paginated view of a user's Letterboxd watchlist (5 per page)."""
    lb_url = f"https://letterboxd.com/{lb_username}/watchlist/"
    total = len(films)

    embed = discord.Embed(
        title=f"📋 {lb_username}'s Letterboxd Watchlist",
        url=lb_url,
        color=_LB_COLOR,
    )

    if not films:
        embed.description = "Watchlist is empty or private."
        return embed

    start = page * 5
    page_films = films[start : start + 5]

    lines = []
    for film in page_films:
        title = film.get("film_title", "Unknown")
        year = film.get("year")
        link = film.get("link", "")
        year_str = f" ({year})" if year else ""

        if link:
            lines.append(f"[{title}{year_str}]({link})")
        else:
            lines.append(f"**{title}{year_str}**")

    embed.description = "\n".join(lines)
    embed.set_footer(text=f"{total} films - page {page + 1}/{total_pages} - Letterboxd")
    return embed


def lb_group_embed(activity: list[dict]) -> discord.Embed:
    """
    Aggregated recent diary activity across all linked server members.
    activity = list of {discord_tag, lb_username, entries: list[dict]}
    """
    embed = discord.Embed(
        title="🎬 What Everyone's Been Watching",
        color=_LB_COLOR,
    )

    if not activity:
        embed.description = (
            "No linked Letterboxd accounts yet. "
            "Use `/lb link <username>` to connect yours."
        )
        return embed

    def _trim(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."

    def latest_date(member: dict) -> str:
        entries = member.get("entries") or []
        if not entries:
            return ""
        return max(entry.get("watch_date", "") for entry in entries)

    members = sorted(activity, key=latest_date, reverse=True)

    max_embed_chars = 5600
    max_fields = 20
    used_chars = len(embed.title or "")
    shown = 0
    for member in members:
        tag = member["discord_tag"]
        lb_user = member["lb_username"]
        entries = sorted(
            member.get("entries") or [],
            key=lambda entry: entry.get("watch_date", ""),
            reverse=True,
        )

        field_name = _trim(f"{tag} ({lb_user})", 256)
        if member.get("error"):
            value = "Couldn't fetch recent watches"
        elif not entries:
            value = "No recent public watches"
        else:
            films = []
            for entry in entries[:2]:
                title = entry.get("film_title", "Unknown")
                year = entry.get("year")
                stars = entry.get("stars", "")
                link = entry.get("link", "")
                date = entry.get("watch_date", "")

                year_str = f" ({year})" if year else ""
                stars_str = f" {stars}" if stars else ""
                date_str = f" - {date}" if date else ""
                if link:
                    film_str = f"[{title}{year_str}]({link})"
                else:
                    film_str = f"**{title}{year_str}**"
                films.append(f"- {film_str}{stars_str}{date_str}")
            value = "\n".join(films)
        value = _trim(value, 1024)

        field_chars = len(field_name) + len(value)
        if shown >= max_fields or used_chars + field_chars > max_embed_chars:
            break

        embed.add_field(name=field_name, value=value, inline=False)
        used_chars += field_chars
        shown += 1

    footer = f"Letterboxd - showing {shown}/{len(activity)} linked member(s)"
    if len(activity) > shown:
        footer += " - newest activity first"
    embed.set_footer(text=footer)
    return embed


def _rating_pair(left: dict) -> str | None:
    left_stars = left.get("left_stars") or (str(left.get("left_rating")) if left.get("left_rating") else "")
    right_stars = left.get("right_stars") or (str(left.get("right_rating")) if left.get("right_rating") else "")
    if not left_stars and not right_stars:
        return None
    left_stars = left_stars or "unrated"
    right_stars = right_stars or "unrated"
    return f"{left_stars} / {right_stars}"


def _film_line(item: dict, include_ratings: bool = False) -> str:
    title = item.get("title") or item.get("film_title") or "Unknown"
    year = item.get("year")
    link = item.get("link", "")
    year_str = f" ({year})" if year else ""
    film = f"[{title}{year_str}]({link})" if link else f"**{title}{year_str}**"
    if include_ratings:
        ratings = _rating_pair(item)
        if ratings:
            return f"{film} - {ratings}"
    return film


def lb_tastecheck_embed(payload: dict, watchlist_note: str | None = None) -> discord.Embed:
    """Compatibility snapshot for /lb tastecheck."""
    score = payload["score"]
    label = payload["label"]
    left = payload["label_a"]
    right = payload["label_b"]

    embed = discord.Embed(
        title=f"🎞️ Tastecheck: {left} x {right}",
        description=f"**{score}% - {label}**",
        color=_LB_COLOR,
    )

    stats = [
        f"Shared recent watches: **{payload['shared_count']}**",
        f"Rated overlap: **{payload['rated_overlap_count']}**",
        f"Shared watchlist wants: **{payload['shared_watchlist_count']}**",
    ]
    if payload.get("avg_diff") is not None:
        stats.append(f"Avg rating gap: **{payload['avg_diff']:.1f} stars**")
    embed.add_field(name="Overview", value="\n".join(stats), inline=False)

    agreements = payload.get("agreements", [])
    if agreements:
        lines = [_film_line(item, include_ratings=True) for item in agreements[:3]]
        embed.add_field(name="Closest Agreements", value="\n".join(lines), inline=False)

    disagreements = payload.get("disagreements", [])
    if disagreements:
        lines = [_film_line(item, include_ratings=True) for item in disagreements[:3]]
        embed.add_field(name="Biggest Splits", value="\n".join(lines), inline=False)

    shared_watchlist = payload.get("shared_watchlist", [])
    if shared_watchlist:
        lines = [_film_line(item) for item in shared_watchlist[:5]]
        embed.add_field(name="Shared Watchlist Wants", value="\n".join(lines), inline=False)
    elif watchlist_note:
        embed.add_field(name="Shared Watchlist Wants", value=watchlist_note, inline=False)

    embed.set_footer(text="Based on recent Letterboxd activity and public watchlists")
    return embed


# ---------- personal watchlist ----------

_WL_COLOR = 0x5865F2  # Discord blurple


def mywatchlist_embed(
    user_tag: str,
    entries: list[dict],
    page: int,
    total_pages: int,
    total_count: int,
) -> discord.Embed:
    """Paginated view of a user's personal internal watchlist."""
    embed = discord.Embed(
        title=f"📋 {user_tag}'s watchlist",
        color=_WL_COLOR,
    )

    if not entries:
        embed.description = (
            "your watchlist is empty.\n\n"
            "add films with `/watchlist add`, the **+ watchlist** button on any film card, "
            "or import from letterboxd."
        )
        return embed

    start = page * MY_WATCHLIST_PAGE_SIZE
    page_entries = entries[start : start + MY_WATCHLIST_PAGE_SIZE]

    lines = []
    for entry in page_entries:
        title = entry.get("title", "Unknown")
        year = entry.get("year")
        year_str = f" ({year})" if year else ""
        lines.append(f"**{title}{year_str}**")

    embed.description = "\n".join(lines)
    embed.set_footer(text=f"{total_count} films - page {page + 1}/{total_pages}")
    return embed


# ---------- macguffins ----------

MACGUFFIN_PAGE_SIZE = 5

_MACGUFFIN_COLORS = {
    "common": 0xAAAAAA,
    "rare": 0x4169E1,
    "iconic": 0xFFD700,
}

_MACGUFFIN_TITLES = {
    "common": "COMMON DROP",
    "rare": "RARE DROP",
    "iconic": "ICONIC DROP",
}

_MACGUFFIN_RARITIES = {
    "common": "\u2B1C common",
    "rare": "\U0001F535 rare",
    "iconic": "\U0001F31F iconic",
}
_EMBED_SPACER = "\u200b"


def _macguffin_color(card: dict) -> int:
    rarity = str(card.get("rarity", "common")).lower()
    return _MACGUFFIN_COLORS.get(rarity, _MACGUFFIN_COLORS["common"])


def _macguffin_description(card: dict) -> str:
    emoji = card.get("emoji", "")
    flavor = card.get("flavor", "")
    return f"{emoji}\n\n*{flavor}*"


def _macguffin_drop_title(card: dict) -> str:
    emoji = str(card.get("emoji") or "").strip()
    name = card.get("name", "Unknown MacGuffin")
    return f"{emoji}\n{name}" if emoji else str(name)


def _macguffin_drop_description(card: dict) -> str:
    flavor = str(card.get("flavor") or "").strip()
    return f"*{flavor}*" if flavor else None


def _macguffin_owner_text(owner_tag: str) -> str:
    if owner_tag.startswith("@") or owner_tag.startswith("<@"):
        return owner_tag
    return f"@{owner_tag}"


def _macguffin_set_text(card: dict) -> str:
    labels = macguffin_module.set_labels_for_card(str(card.get("id", "")))
    return ", ".join(labels)


def _format_macguffin_acquired(value) -> str:
    if not value:
        return "unknown"
    if isinstance(value, datetime):
        acquired = value
    else:
        text = str(value)
        try:
            acquired = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
    return acquired.strftime("%B %d, %Y").replace(" 0", " ", 1)


def macguffin_drop_embed(
    card: dict,
    owner_tag: str,
    claimed_count: int,
    total_count: int,
) -> discord.Embed:
    """Public announcement for a newly claimed MacGuffin."""
    rarity = str(card.get("rarity", "common")).lower()
    embed = discord.Embed(
        title=_macguffin_drop_title(card),
        description=_macguffin_drop_description(card),
        color=_macguffin_color(card),
    )
    embed.set_author(
        name=_MACGUFFIN_TITLES.get(rarity, _MACGUFFIN_TITLES["common"]),
    )
    embed.add_field(name="FROM", value=card.get("source", "Unknown"), inline=True)
    embed.add_field(name="SET", value=_macguffin_set_text(card), inline=True)
    embed.add_field(
        name="CLAIMED BY",
        value=_macguffin_owner_text(owner_tag),
        inline=False,
    )
    embed.set_footer(text=f"MacGuffin {claimed_count} of {total_count} claimed")
    return embed


def macguffin_card_embed(card: dict, record: dict) -> discord.Embed:
    """Single-card view for a user's MacGuffin inventory."""
    rarity = str(card.get("rarity", "common")).lower()
    embed = discord.Embed(
        title=card.get("name", "Unknown MacGuffin"),
        description=_macguffin_description(card),
        color=_macguffin_color(card),
    )
    embed.add_field(
        name="RARITY",
        value=_MACGUFFIN_RARITIES.get(rarity, rarity),
        inline=True,
    )
    embed.add_field(name="FROM", value=card.get("source", "Unknown"), inline=True)
    embed.add_field(name="SET", value=_macguffin_set_text(card), inline=True)
    embed.add_field(
        name="ACQUIRED",
        value=_format_macguffin_acquired(record.get("acquired_at")),
        inline=True,
    )
    embed.add_field(
        name="VIA",
        value=record.get("acquired_via", "Unknown"),
        inline=True,
    )
    embed.add_field(name=_EMBED_SPACER, value=_EMBED_SPACER, inline=True)
    return embed


def macguffin_list_embed(
    user_tag: str,
    cards: list[dict],
    page: int,
    total_pages: int,
) -> discord.Embed:
    """Paginated view of a user's MacGuffin collection."""
    embed = discord.Embed(
        title=f"{user_tag}'s MacGuffins",
        color=0x5865F2,
    )

    total_count = len(cards)
    if not cards:
        embed.description = "You don't have any MacGuffins yet."
        return embed

    start = page * MACGUFFIN_PAGE_SIZE
    page_cards = cards[start : start + MACGUFFIN_PAGE_SIZE]
    lines = []
    for card in page_cards:
        emoji = card.get("emoji", "")
        name = card.get("name", "Unknown MacGuffin")
        rarity = card.get("rarity", "unknown")
        lines.append(f"{emoji} **{name}** - {rarity}")

    embed.description = "\n".join(lines)
    embed.set_footer(text=f"{total_count} MacGuffins - page {page + 1}/{total_pages}")
    return embed


def _macguffin_event_line(event: dict) -> str:
    event_type = event.get("event_type")
    from_tag = event.get("from_user_tag")
    to_tag = event.get("to_user_tag")

    try:
        when = f"<t:{int(datetime.fromisoformat(event.get('created_at', '')).timestamp())}:D>"
    except (ValueError, TypeError):
        when = "unknown date"

    if event_type == "removed":
        return f"🗑️ removed from {_macguffin_owner_text(from_tag)} - {when}"
    if not from_tag:
        verb = "assigned by an admin to" if event_type == "admin" else "claimed by"
        return f"🎉 {verb} {_macguffin_owner_text(to_tag)} - {when}"
    if event_type == "admin":
        return (
            f"🛠️ moved by an admin from {_macguffin_owner_text(from_tag)} "
            f"to {_macguffin_owner_text(to_tag)} - {when}"
        )
    return (
        f"🎁 gifted from {_macguffin_owner_text(from_tag)} "
        f"to {_macguffin_owner_text(to_tag)} - {when}"
    )


def macguffin_history_embed(card: dict, events: list[dict]) -> discord.Embed:
    """Ownership trail for a single MacGuffin, shown by /guffinhistory."""
    rarity = str(card.get("rarity", "common")).lower()
    embed = discord.Embed(
        title=f"{card.get('emoji', '')} {card.get('name', 'Unknown MacGuffin')}".strip(),
        color=_macguffin_color(card),
    )
    embed.add_field(name="RARITY", value=_MACGUFFIN_RARITIES.get(rarity, rarity), inline=True)
    embed.add_field(name="FROM", value=card.get("source", "Unknown"), inline=True)
    embed.add_field(name="SET", value=_macguffin_set_text(card), inline=True)

    if not events:
        embed.description = "no ownership history recorded yet - this card hasn't moved since history tracking began."
        return embed

    lines = [_macguffin_event_line(event) for event in events]
    embed.description = "\n".join(lines[-15:])
    if len(lines) > 15:
        embed.set_footer(text=f"showing the 15 most recent of {len(lines)} events")
    return embed


def weekly_recap_embed(
    since: datetime,
    until: datetime,
    top_renters: list[dict],
    new_macguffins: list[dict],
    new_achievements: list[dict],
    guess_leader: dict | None,
    six_leader: dict | None,
) -> discord.Embed:
    """Weekly community digest posted to the feed channel."""
    embed = discord.Embed(
        title="📅 This Week at Return by 9",
        description=f"{_format_date(since)} - {_format_date(until)}",
        color=0x5865F2,
    )

    if top_renters:
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, row in enumerate(top_renters):
            prefix = medals[i] if i < len(medals) else f"#{i + 1}"
            count = row.get("returned_count", 0)
            lines.append(
                f"{prefix} **{row.get('user_name', 'unknown')}** - "
                f"{count} return{'s' if count != 1 else ''}"
            )
        embed.add_field(name="🎬 Top Renters", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="🎬 Top Renters", value="No returns this week.", inline=False)

    if new_macguffins:
        lines = []
        for row in new_macguffins:
            card = macguffin_module.CARDS.get(row.get("macguffin_id"))
            emoji = card.get("emoji", "") if card else ""
            name = card.get("name") if card else row.get("macguffin_id", "unknown")
            lines.append(
                f"{emoji} **{name}** - {_macguffin_owner_text(row.get('owner_tag', 'unknown'))}"
            )
        embed.add_field(name="🎁 New MacGuffins", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="🎁 New MacGuffins", value="No new pulls this week.", inline=False)

    if new_achievements:
        lines = []
        for row in new_achievements:
            achievement = achievement_module.ACHIEVEMENT_BY_ID.get(row.get("achievement_id"))
            name = (
                achievement_module.display_name(achievement)
                if achievement
                else row.get("achievement_id", "unknown")
            )
            lines.append(f"🏅 **{row.get('user_tag', 'unknown')}** unlocked **{name}**")
        embed.add_field(name="🏆 New Achievements", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="🏆 New Achievements", value="No unlocks this week.", inline=False)

    leader_lines = []
    if guess_leader:
        leader_lines.append(
            f"🎯 **{guess_leader.get('user_tag', 'unknown')}** leads /guess & /play "
            f"with {guess_leader.get('points', 0)} pts"
        )
    if six_leader:
        leader_lines.append(
            f"🔗 **{six_leader.get('user_tag', 'unknown')}** leads /six "
            f"with {six_leader.get('points', 0)} pts"
        )
    if leader_lines:
        embed.add_field(name="🕹️ Leaderboard Leaders", value="\n".join(leader_lines), inline=False)

    embed.set_footer(text="new here? check /suck, /rent, /play, and /claimguffin")
    return embed
