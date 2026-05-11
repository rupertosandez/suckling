import discord

import tmdb
import embeds
import db
import plex


def _record_existing_providers(movie_id: int, providers: dict) -> list[str]:
    """
    Record any current subscription providers as seen, so the daily job won't
    re-announce them. Returns the list of provider names found.
    """
    flatrate = providers.get("flatrate", [])
    names = []
    for provider in flatrate:
        provider_name = provider.get("provider_name", "")
        if provider_name:
            db.record_provider(movie_id, provider_name)
            names.append(provider_name)
    return names


async def _build_track_response(movie_id: int, movie_title: str, movie_year: str) -> str:
    """
    Build the response text for a successful /track action.
    Auto-checks streaming availability and includes it in the response.
    """
    try:
        providers = await tmdb.get_watch_providers(movie_id, region="US")
    except tmdb.TMDBError:
        # Tracking still succeeded; just couldn't fetch providers
        return (
            f"✅ Now tracking **{movie_title} ({movie_year})**. "
            "You'll get an alert when it becomes streamable."
        )

    current_providers = _record_existing_providers(movie_id, providers)
    justwatch_link = providers.get("link")

    base = f"✅ Now tracking **{movie_title} ({movie_year})**."

    if current_providers:
        provider_list = ", ".join(current_providers)
        if justwatch_link:
            return (
                f"{base}\n💻 Already streaming on **{provider_list}** "
                f"→ [See where to watch]({justwatch_link})"
            )
        return f"{base}\n💻 Already streaming on **{provider_list}**."

    return f"{base}\n⏳ Not yet streaming — you'll get an alert when it becomes streamable."


class MovieSelect(discord.ui.Select):
    """Dropdown showing movie candidates for /watch disambiguation."""

    def __init__(self, candidates: list[dict]):
        options = []
        for movie in candidates[:25]:
            title = movie.get("title", "Unknown")
            release_date = movie.get("release_date", "")
            year = release_date[:4] if release_date else "—"
            overview = movie.get("overview") or ""
            description = overview[:80] + "…" if len(overview) > 80 else overview

            options.append(
                discord.SelectOption(
                    label=f"{title} ({year})"[:100],
                    description=description[:100],
                    value=str(movie["id"]),
                )
            )

        super().__init__(
            placeholder="Multiple matches found — pick one",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        movie_id = int(self.values[0])
        await interaction.response.defer()

        try:
            details = await tmdb.get_movie_details(movie_id)
            providers = await tmdb.get_watch_providers(movie_id, region="US")
        except tmdb.TMDBError as e:
            await interaction.followup.send(f"Sorry, couldn't load details: {e}")
            return

        release_date = details.get("release_date") or ""
        plex_year = int(release_date[:4]) if release_date[:4].isdigit() else None
        plex_available = await plex.check_availability(details.get("title"), year=plex_year)

        embed = embeds.movie_embed(
            details, providers, in_theaters=False, plex_available=plex_available
        )
        await interaction.edit_original_response(content=None, embed=embed, view=None)


class MovieSelectView(discord.ui.View):
    """View wrapping MovieSelect for /watch."""

    def __init__(self, candidates: list[dict]):
        super().__init__(timeout=60)
        self.add_item(MovieSelect(candidates))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class TrackSelect(discord.ui.Select):
    """Dropdown for picking which movie to track when there's ambiguity."""

    def __init__(self, candidates: list[dict], added_by: str):
        self.added_by = added_by
        # Store the candidates so we can look up year/title later
        self._candidates_by_id = {str(m["id"]): m for m in candidates[:25]}

        options = []
        for movie in candidates[:25]:
            title = movie.get("title", "Unknown")
            release_date = movie.get("release_date", "")
            year = release_date[:4] if release_date else "—"
            overview = movie.get("overview") or ""
            description = overview[:80] + "…" if len(overview) > 80 else overview

            options.append(
                discord.SelectOption(
                    label=f"{title} ({year})"[:100],
                    description=description[:100],
                    value=str(movie["id"]),
                )
            )

        super().__init__(
            placeholder="Pick which one to track",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        movie_id = int(self.values[0])
        chosen = self._candidates_by_id.get(self.values[0], {})
        movie_title = chosen.get("title", "Unknown")
        release_date = chosen.get("release_date", "")
        movie_year = release_date[:4] if release_date else "—"

        added = db.add_tracked_movie(movie_id, movie_title, self.added_by)
        if not added:
            msg = f"**{movie_title} ({movie_year})** is already on the tracked list."
            await interaction.response.edit_message(content=msg, view=None)
            return

        # Defer because the auto-check involves a TMDB call
        await interaction.response.defer()
        msg = await _build_track_response(movie_id, movie_title, movie_year)
        await interaction.edit_original_response(content=msg, view=None)


class TrackSelectView(discord.ui.View):
    """View wrapping TrackSelect for /track."""

    def __init__(self, candidates: list[dict], added_by: str):
        super().__init__(timeout=60)
        self.add_item(TrackSelect(candidates, added_by))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True