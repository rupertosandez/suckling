from datetime import datetime

import discord

import tmdb
import trivia_roulette


SHUDDER_PROVIDER_NAME = "Shudder"
SEERR_BASE_URL = "https://seerr.cajou.enyo.bysh.me"


def _format_date(d: datetime) -> str:
    if not hasattr(d, "strftime"):
        return str(d)
    formatted = d.strftime("%b %d, %Y")
    return formatted.replace(" 0", " ", 1)


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

    embed.add_field(name="Total Movies", value=f"**{stats['count']:,}**", inline=True)

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
            value=f"{stats['min_year']} – {stats['max_year']}",
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
        f"⏱️ **{total_minutes:,} minutes**",
        f"📆 **{days:.1f} days** of nonstop watching",
        f"🗓️ **{weeks:.1f} weeks**",
        "",
        f"At a more reasonable 8 hours/day, you'd finish in **{realistic:.0f} days**.",
    ]

    embed = discord.Embed(
        title="⏰ Total Return by 9 Library Runtime",
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
        lines.append(f"**{name}** — {bar} {count}")

    embed = discord.Embed(
        title="🎭 Top Genres in Return by 9",
        description="\n".join(lines),
        color=0xE5A00D,
    )
    embed.set_footer(text="From the Return by 9 library")
    return embed


def rb9_random_scene_embed(scene: dict) -> discord.Embed:
    """Embed for /rb9randomscene — random film backdrop."""
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