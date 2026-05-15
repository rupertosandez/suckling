import discord
from datetime import datetime, timezone

import tmdb
import embeds
import db
import plex
import rental as rental_module


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


# ---------- rental views ----------

def _disable_view_items(view: discord.ui.View) -> None:
    for item in view.children:
        item.disabled = True


async def _mark_view_processing(
    interaction: discord.Interaction,
    view: discord.ui.View,
    content: str,
) -> bool:
    """Immediately acknowledge a rental button click and prevent double-clicks."""
    if getattr(view, "_processing", False):
        if not interaction.response.is_done():
            await interaction.response.defer()
        return False

    setattr(view, "_processing", True)
    _disable_view_items(view)
    await interaction.response.edit_message(content=content, embed=None, view=view)
    return True


async def _confirm_rental(
    interaction: discord.Interaction,
    bot: discord.Client,
    movie: dict,
    user_id: str,
    user_name: str,
    rerolls_used: int,
    initiated_by: str,
) -> None:
    """
    Shared finalization: creates the DB record, forum thread, DMs the user,
    and edits the ephemeral message to a confirmation.
    Called from RentPickView and EmbedRentView on acceptance.
    """
    now = datetime.now(timezone.utc)
    due_at = rental_module.compute_due_at(now)

    # Check reviews channel is configured before committing
    reviews_channel_id = db.get_reviews_channel_id()
    if not reviews_channel_id:
        await interaction.edit_original_response(
            content=(
                "⚠️ the reviews forum hasn't been configured yet. "
                "ask an admin to run `/setreviews` first."
            ),
            embed=None,
            view=None,
        )
        return

    rental_id = db.create_rental(
        user_id=user_id,
        user_name=user_name,
        plex_key=movie["rating_key"],
        title=movie["title"],
        year=movie.get("year"),
        poster_url=movie.get("thumb_url"),
        rented_at=now.isoformat(),
        due_at=due_at.isoformat(),
        rerolls_used=rerolls_used,
        initiated_by=initiated_by,
    )

    # Create the forum thread
    thread_ok = await rental_module.create_forum_thread(
        bot=bot,
        rental_id=rental_id,
        movie=movie,
        user_tag=user_name,
        due_at=due_at,
    )

    due_ts = int(due_at.timestamp())
    title_str = f"**{movie['title']} ({movie.get('year', '?')})**"

    if thread_ok:
        rental = db.get_rental_by_id(rental_id)
        thread_id = rental.get("thread_id") if rental else None
        thread_mention = f" - check <#{thread_id}>" if thread_id else ""
        confirm_text = (
            f"📼 rental confirmed: {title_str}\n"
            f"due back <t:{due_ts}:F> (<t:{due_ts}:R>){thread_mention}\n\n"
            f"-# use `/return` when you're done."
        )
    else:
        confirm_text = (
            f"📼 rental confirmed: {title_str}\n"
            f"due back <t:{due_ts}:F> (<t:{due_ts}:R>)\n\n"
            f"-# use `/return` when you're done. "
            f"(couldn't post to the reviews forum - check bot permissions)"
        )

    await interaction.edit_original_response(content=confirm_text, embed=None, view=None)


class RentWarningView(discord.ui.View):
    """
    Step 1 of /rent: shown before any film is revealed. Gives the user
    a chance to bail before committing to the rental flow.
    """

    def __init__(self, bot: discord.Client, user_id: str, user_name: str):
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id
        self.user_name = user_name
        self._processing = False

        async def start_cb(interaction: discord.Interaction):
            await self._start(interaction)

        async def cancel_cb(interaction: discord.Interaction):
            await self._cancel(interaction)

        start_btn = discord.ui.Button(
            label="start rental",
            style=discord.ButtonStyle.success,
            emoji="📼",
        )
        start_btn.callback = start_cb

        cancel_btn = discord.ui.Button(
            label="nevermind",
            style=discord.ButtonStyle.secondary,
        )
        cancel_btn.callback = cancel_cb

        self.add_item(start_btn)
        self.add_item(cancel_btn)

    async def _start(self, interaction: discord.Interaction):
        if not await _mark_view_processing(interaction, self, "checking the shelves..."):
            return

        self.stop()

        # Check for existing active rental
        existing = db.get_active_rental(self.user_id)
        if existing:
            title = existing.get("title", "a film")
            await interaction.edit_original_response(
                content=(
                    f"you already have **{title}** checked out. "
                    "use `/return` to return it before renting something new."
                ),
                embed=None,
                view=None,
            )
            return

        # Check reviews channel is set
        if not db.get_reviews_channel_id():
            await interaction.edit_original_response(
                content=(
                    "⚠️ the reviews forum hasn't been configured yet. "
                    "ask an admin to run `/setreviews` first."
                ),
                embed=None,
                view=None,
            )
            return

        # Pick the first film
        exclude_keys = db.get_user_rented_plex_keys(self.user_id)
        try:
            movie = await plex.pick_random_for_rental(exclude_keys)
        except plex.PlexError as e:
            await interaction.edit_original_response(
                content=f"⚠️ couldn't reach the library right now: {e}",
                embed=None,
                view=None,
            )
            return

        if movie is None:
            await interaction.edit_original_response(
                content=(
                    "⚠️ looks like you've rented everything in the library! "
                    "nothing left to offer you."
                ),
                embed=None,
                view=None,
            )
            return

        shown_keys = exclude_keys | {movie["rating_key"]}
        pick_view = RentPickView(
            bot=self.bot,
            current_film=movie,
            rerolls_remaining=2,
            shown_keys=shown_keys,
            user_id=self.user_id,
            user_name=self.user_name,
            rerolls_used=0,
        )
        embed = embeds.rental_offer_embed(movie, is_last_reroll=False)
        await interaction.edit_original_response(embed=embed, view=pick_view, content=None)

    async def _cancel(self, interaction: discord.Interaction):
        if not await _mark_view_processing(interaction, self, "canceling rental..."):
            return

        self.stop()
        await interaction.edit_original_response(
            content="no problem - come back when you're ready.",
            embed=None,
            view=None,
        )

    async def on_timeout(self):
        self.stop()


class RentPickView(discord.ui.View):
    """
    Step 2+ of /rent: shows a film offer with accept/reroll buttons.
    On the last reroll the next pick is auto-confirmed with no buttons.
    """

    def __init__(
        self,
        bot: discord.Client,
        current_film: dict,
        rerolls_remaining: int,
        shown_keys: set[str],
        user_id: str,
        user_name: str,
        rerolls_used: int,
    ):
        super().__init__(timeout=300)
        self.bot = bot
        self.current_film = current_film
        self.rerolls_remaining = rerolls_remaining
        self.shown_keys = shown_keys
        self.user_id = user_id
        self.user_name = user_name
        self.rerolls_used = rerolls_used
        self._processing = False

        async def accept_cb(interaction: discord.Interaction):
            await self._accept(interaction)

        async def reroll_cb(interaction: discord.Interaction):
            await self._reroll(interaction)

        accept_btn = discord.ui.Button(
            label="accept rental",
            style=discord.ButtonStyle.success,
            emoji="📼",
        )
        accept_btn.callback = accept_cb
        self.add_item(accept_btn)

        reroll_label = "re-roll (last one)" if rerolls_remaining == 1 else "re-roll"
        reroll_btn = discord.ui.Button(
            label=reroll_label,
            style=discord.ButtonStyle.secondary,
        )
        reroll_btn.callback = reroll_cb
        self.add_item(reroll_btn)

    async def _accept(self, interaction: discord.Interaction):
        if not await _mark_view_processing(interaction, self, "processing rental..."):
            return

        self.stop()
        await _confirm_rental(
            interaction=interaction,
            bot=self.bot,
            movie=self.current_film,
            user_id=self.user_id,
            user_name=self.user_name,
            rerolls_used=self.rerolls_used,
            initiated_by="command",
        )

    async def _reroll(self, interaction: discord.Interaction):
        if not await _mark_view_processing(interaction, self, "finding another tape..."):
            return

        self.stop()

        new_rerolls_remaining = self.rerolls_remaining - 1
        new_rerolls_used = self.rerolls_used + 1

        # Pick a new film, excluding everything shown so far + user's history
        exclude_keys = self.shown_keys | db.get_user_rented_plex_keys(self.user_id)
        try:
            new_film = await plex.pick_random_for_rental(exclude_keys)
        except plex.PlexError as e:
            await interaction.edit_original_response(
                content=f"⚠️ couldn't reach the library right now: {e}",
                embed=None,
                view=None,
            )
            return

        if new_film is None:
            await interaction.edit_original_response(
                content="⚠️ couldn't find another film to offer. try `/rent` again.",
                embed=None,
                view=None,
            )
            return

        new_shown = self.shown_keys | {new_film["rating_key"]}

        if new_rerolls_remaining == 0:
            # Auto-confirm — no more choices
            await interaction.edit_original_response(
                content=f"🎲 rerolled to **{new_film['title']}** - locking it in...",
                embed=None,
                view=None,
            )
            await _confirm_rental(
                interaction=interaction,
                bot=self.bot,
                movie=new_film,
                user_id=self.user_id,
                user_name=self.user_name,
                rerolls_used=new_rerolls_used,
                initiated_by="command",
            )
        else:
            # Show the next pick with updated buttons
            is_last = (new_rerolls_remaining == 1)
            embed = embeds.rental_offer_embed(new_film, is_last_reroll=is_last)
            new_view = RentPickView(
                bot=self.bot,
                current_film=new_film,
                rerolls_remaining=new_rerolls_remaining,
                shown_keys=new_shown,
                user_id=self.user_id,
                user_name=self.user_name,
                rerolls_used=new_rerolls_used,
            )
            await interaction.edit_original_response(embed=embed, view=new_view, content=None)

    async def on_timeout(self):
        self.stop()