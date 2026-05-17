from datetime import datetime

import discord

import tmdb
import trivia_roulette


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


def roll_embed(details: dict, providers: dict) -> discord.Embed:
    """Embed for /roll - same content as movie_embed but with a fun preamble."""
    embed = movie_embed(details, providers, in_theaters=False)
    embed.title = f"🎲 {embed.title}"
    embed.color = 0x8B0000
    return embed


def daily_rec_embed(details: dict, providers: dict) -> discord.Embed:
    """Embed for the daily horror recommendation."""
    embed = movie_embed(details, providers, in_theaters=False)
    embed.title = f"🩸 Today's Horror Pick: {embed.title}"
    embed.color = 0x8B0000
    return embed


def rb9_pick_embed(movie: dict) -> discord.Embed:
    """Embed for /rb9 - shows a random movie from the Return by 9 library."""
    title = movie.get("title", "Unknown")
    year = movie.get("year") or "?"
    summary = movie.get("summary") or "*No summary available.*"
    duration = movie.get("duration_minutes")
    rating = movie.get("rating")

    if len(summary) > 500:
        summary = summary[:497].rstrip() + "..."

    embed = discord.Embed(
        title=f"📀 From Return by 9: {title} ({year})",
        description=summary,
        color=0xE5A00D,
    )

    if duration:
        embed.add_field(name="Runtime", value=f"{duration} min", inline=True)
    if rating:
        embed.add_field(name="Rated", value=rating, inline=True)

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

    if stats.get("oldest"):
        oldest = stats["oldest"]
        embed.add_field(
            name="Oldest",
            value=f"{oldest['title']} ({oldest['year']})",
            inline=True,
        )

    if stats.get("newest_by_year"):
        newest = stats["newest_by_year"]
        embed.add_field(
            name="Newest",
            value=f"{newest['title']} ({newest['year']})",
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
    summary = movie.get("summary") or ""

    if len(summary) > 300:
        summary = summary[:297].rstrip() + "..."

    embed = discord.Embed(
        title=f"{emoji} {label}",
        description=f"**{title} ({year})**" + (f"\n\n{summary}" if summary else ""),
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
        f"- **{total_minutes:,} minutes**",
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
        title=f"{cat_emoji} rb9 roulette: {cat_label}!",
        description=prompt,
        color=color,
    )
    embed.set_footer(text=f"started by {started_by} · 30 seconds to guess")
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
        title = "⏰ time's up!"
        description = f"the answer was **{answer}**{year_str}"

    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text=f"category: {cat_label}")
    return embed


# ---------- info ----------

def info_embed(version: str, uptime_seconds: float, guild_count: int) -> discord.Embed:
    """About card for /info. References attachment://logo.png for the wordmark banner."""
    embed = discord.Embed(
        title=f"sucklingbot v{version}",
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
            "\n\n-# this is your last reroll - if you reroll again, "
            "the next film will be locked in automatically."
        )

    embed = discord.Embed(
        title=f"📼 your rental: {title} ({year})",
        description=desc,
        color=0xE5A00D,
    )

    if duration:
        embed.add_field(name="Runtime", value=f"{duration} min", inline=True)

    if movie.get("thumb_url"):
        embed.set_thumbnail(url=movie["thumb_url"])

    embed.set_footer(text="from the return by 9 library")
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

    embed.add_field(name="checked out by", value=user_tag, inline=True)
    if duration:
        embed.add_field(name="runtime", value=f"{duration} min", inline=True)
    embed.add_field(
        name="due back",
        value=f"<t:{due_ts}:F> (<t:{due_ts}:R>)",
        inline=False,
    )

    if movie.get("thumb_url"):
        embed.set_thumbnail(url=movie["thumb_url"])

    embed.set_footer(text="use /return to post your review when you're done")
    return embed


def rental_review_embed(
    movie: dict,
    user_tag: str,
    rating: int,
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
    header = f"{'🔴 late return' if is_late else '✅ returned'} by {user_tag}"

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

    stars = "⭐" * rating + "☆" * (10 - rating)
    embed.add_field(name="rating", value=f"{rating}/10  {stars}", inline=False)
    embed.add_field(name="recommend?", value="yes" if recommend else "no", inline=True)
    embed.add_field(name="returned", value=returned_str, inline=True)

    if is_late:
        embed.add_field(name="late fee", value=f"${late_fee:.2f}", inline=True)

    if movie.get("poster_url") or movie.get("thumb_url"):
        embed.set_thumbnail(url=movie.get("poster_url") or movie.get("thumb_url"))

    embed.set_footer(text="from the return by 9 library")
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
        description=f"rental by **{user_tag}** was cancelled by an admin."
        + (f"\n\nreason: {reason}" if reason else ""),
        color=0x808080,
    )

    if movie.get("poster_url") or movie.get("thumb_url"):
        embed.set_thumbnail(url=movie.get("poster_url") or movie.get("thumb_url"))

    embed.set_footer(text="from the return by 9 library")
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
    status = "overdue!" if is_overdue else "checked out"

    embed = discord.Embed(
        title=f"📼 {title} ({year})",
        description=f"status: **{status}**",
        color=color,
    )

    embed.add_field(name="checked out", value=rented_str, inline=True)
    embed.add_field(name="due back", value=due_str, inline=True)

    rerolls = rental.get("rerolls_used", 0)
    if rerolls:
        embed.add_field(name="rerolls used", value=str(rerolls), inline=True)

    extensions = rental.get("extensions_used", 0)
    if extensions:
        embed.add_field(name="extensions used", value=str(extensions), inline=True)

    thread_id = rental.get("thread_id")
    if thread_id:
        embed.add_field(
            name="forum thread",
            value=f"<#{thread_id}>",
            inline=False,
        )

    if rental.get("poster_url"):
        embed.set_thumbnail(url=rental["poster_url"])

    embed.set_footer(text="use /return to post your review when you're done")
    return embed


def late_fees_embed(rows: list[dict]) -> discord.Embed:
    """Leaderboard of accumulated late fees."""
    if not rows:
        return discord.Embed(
            title="🏪 late fee ledger",
            description="no late fees on record. everyone's been returning on time.",
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
        title="🏪 late fee ledger",
        description="\n".join(lines),
        color=0xE5A00D,
    )
    embed.set_footer(text="$1/day for every day overdue")
    return embed


def rental_stats_embed(history: list[dict], user_tag: str) -> discord.Embed:
    """Personal rental stats for /rentalstats."""
    if not history:
        return discord.Embed(
            title=f"📼 rental history - {user_tag}",
            description="no rentals yet.",
            color=0xE5A00D,
        )

    total = len(history)
    returned = [r for r in history if r["status"] == "returned"]
    on_time = [r for r in returned if r.get("late_fee_dollars", 0) == 0]
    late = [r for r in returned if r.get("late_fee_dollars", 0) > 0]
    total_fees = sum(r.get("late_fee_dollars", 0) for r in history)
    active = [r for r in history if r["status"] == "active"]

    embed = discord.Embed(
        title=f"📼 rental history - {user_tag}",
        color=0xE5A00D,
    )

    embed.add_field(name="total rentals", value=str(total), inline=True)
    embed.add_field(name="returned on time", value=str(len(on_time)), inline=True)
    embed.add_field(name="returned late", value=str(len(late)), inline=True)

    if total_fees > 0:
        embed.add_field(name="total late fees", value=f"${total_fees:.2f}", inline=True)

    if active:
        r = active[0]
        try:
            due = datetime.fromisoformat(r["due_at"])
            due_ts = int(due.timestamp())
            active_str = f"{r['title']} - due <t:{due_ts}:R>"
        except (ValueError, TypeError):
            active_str = r["title"]
        embed.add_field(name="currently renting", value=active_str, inline=False)

    # Last 5 returned
    recent = [r for r in returned[:5]]
    if recent:
        lines = []
        for r in recent:
            rating_str = f"{r['rating']}/10" if r.get("rating") else "no rating"
            rec_str = " - recommended" if r.get("recommended") else ""
            lines.append(f"- **{r['title']}** ({r.get('year', '?')}) - {rating_str}{rec_str}")
        embed.add_field(name="recent returns", value="\n".join(lines), inline=False)

    embed.set_footer(text="from the return by 9 library")
    return embed
