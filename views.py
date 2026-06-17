import discord
from datetime import datetime, timezone
import re

import tmdb
import embeds
import db
import plex
import rental as rental_module
import achievements as achievement_module

MY_WATCHLIST_PAGE_SIZE = 10
LB_WATCHLIST_PAGE_SIZE = 5
MACGUFFIN_PAGE_SIZE = 5
_PERSISTENT_WATCHLIST_RE = re.compile(r"^s:wl:t:(?P<tmdb_id>\d+)$")
_PERSISTENT_RENT_RE = re.compile(r"^s:rent:t:(?P<tmdb_id>\d+)$")


def _film_year_from_details(details: dict) -> int | None:
    release_date = details.get("release_date") or ""
    return int(release_date[:4]) if release_date[:4].isdigit() else None


async def _watchlist_add_from_tmdb_id(
    interaction: discord.Interaction,
    tmdb_id: int,
) -> None:
    if not await _defer_component(interaction, ephemeral=True):
        return

    try:
        details = await tmdb.get_movie_details(tmdb_id)
    except tmdb.TMDBError as e:
        await interaction.followup.send(
            f"⚠️ couldn't look up that film on TMDB: {e}",
            ephemeral=True,
        )
        return

    title = details.get("title") or "Unknown"
    year = _film_year_from_details(details)
    poster_url = tmdb.poster_url(details.get("poster_path"))
    added = db.watchlist_add(
        user_id=str(interaction.user.id),
        title=title,
        year=year,
        tmdb_id=tmdb_id,
        poster_url=poster_url,
        source="button",
    )

    year_str = f" ({year})" if year else ""
    if added:
        await interaction.followup.send(
            f"📋 added **{title}{year_str}** to your watchlist.",
            ephemeral=True,
        )
        await achievement_module.award_for_user(
            interaction.client,
            interaction.user,
            source_type="watchlist_add",
            source_id=str(tmdb_id),
        )
    else:
        await interaction.followup.send(
            f"**{title}{year_str}** is already on your watchlist.",
            ephemeral=True,
        )


async def _rent_from_tmdb_id(
    interaction: discord.Interaction,
    tmdb_id: int,
) -> None:
    if not await _defer_component(interaction, ephemeral=True):
        return

    try:
        details = await tmdb.get_movie_details(tmdb_id)
    except tmdb.TMDBError as e:
        await interaction.followup.send(
            f"⚠️ couldn't look up that film on TMDB: {e}",
            ephemeral=True,
        )
        return

    title = details.get("title") or "Unknown"
    year = _film_year_from_details(details)
    try:
        movie = await plex.find_movie_by_title(title, year=year)
    except plex.PlexError as e:
        await interaction.followup.send(
            f"⚠️ couldn't reach the library right now: {e}",
            ephemeral=True,
        )
        return

    if movie is None:
        year_str = f" ({year})" if year else ""
        await interaction.followup.send(
            f"⚠️ **{title}{year_str}** doesn't seem to be in the library right now.",
            ephemeral=True,
        )
        return

    view = EmbedRentView(
        bot=interaction.client,
        user_id=str(interaction.user.id),
        user_name=str(interaction.user),
        movie=movie,
        initiated_by="selected",
    )
    await interaction.followup.send(
        f"📼 rent **{movie['title']}**?",
        view=view,
        ephemeral=True,
    )


async def _send_embed_rent_prompt(
    interaction: discord.Interaction,
    bot: discord.Client,
    *,
    movie: dict | None = None,
    title: str | None = None,
    year: int | None = None,
) -> None:
    if not await _defer_component(interaction, ephemeral=True):
        return

    view = EmbedRentView(
        bot=bot,
        user_id=str(interaction.user.id),
        user_name=str(interaction.user),
        movie=movie,
        title=title,
        year=year,
        initiated_by="selected",
    )
    display_title = (movie or {}).get("title") or title or "that movie"
    await interaction.followup.send(
        f"📼 rent **{display_title}**?",
        view=view,
        ephemeral=True,
    )


class RentalHistoryView(discord.ui.View):
    """Paginated view for /rentalstats."""

    def __init__(self, user_tag: str, history: list[dict]):
        super().__init__(timeout=120)
        self.user_tag = user_tag
        self.history = history
        self.page = 0
        self.total_pages = max(1, -(-len(history) // embeds.RENTAL_HISTORY_PAGE_SIZE))
        self._rebuild()

    @classmethod
    def for_page(
        cls,
        user_tag: str,
        history: list[dict],
        page: int,
    ) -> "RentalHistoryView":
        view = cls(user_tag=user_tag, history=history)
        view.page = page
        view._rebuild()
        return view

    def _rebuild(self):
        self.clear_items()
        if self.page > 0:
            prev_btn = discord.ui.Button(
                label="< prev",
                style=discord.ButtonStyle.secondary,
            )
            prev_btn.callback = self._prev
            self.add_item(prev_btn)

        if self.page < self.total_pages - 1:
            next_btn = discord.ui.Button(
                label="next >",
                style=discord.ButtonStyle.secondary,
            )
            next_btn.callback = self._next
            self.add_item(next_btn)

    async def _prev(self, interaction: discord.Interaction):
        if not await _defer_component(interaction):
            return
        next_view = RentalHistoryView.for_page(
            user_tag=self.user_tag,
            history=self.history,
            page=max(0, self.page - 1),
        )
        embed = embeds.rental_stats_embed(
            next_view.history,
            next_view.user_tag,
            next_view.page,
            next_view.total_pages,
        )
        self.stop()
        await interaction.edit_original_response(embed=embed, view=next_view)

    async def _next(self, interaction: discord.Interaction):
        if not await _defer_component(interaction):
            return
        next_view = RentalHistoryView.for_page(
            user_tag=self.user_tag,
            history=self.history,
            page=min(self.total_pages - 1, self.page + 1),
        )
        embed = embeds.rental_stats_embed(
            next_view.history,
            next_view.user_tag,
            next_view.page,
            next_view.total_pages,
        )
        self.stop()
        await interaction.edit_original_response(embed=embed, view=next_view)


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

    def __init__(self, candidates: list[dict], bot: discord.Client | None = None):
        self.bot = bot
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
        poster_url = tmdb.poster_url(details.get("poster_path"))
        film_view = FilmCardView(
            bot=self.bot,
            title=details.get("title", ""),
            year=plex_year,
            tmdb_id=details.get("id"),
            poster_url=poster_url,
            plex_available=bool(plex_available),
        ) if self.bot is not None else None
        await interaction.edit_original_response(content=None, embed=embed, view=film_view)


class MovieSelectView(discord.ui.View):
    """View wrapping MovieSelect for /watch."""

    def __init__(self, candidates: list[dict], bot: discord.Client | None = None):
        super().__init__(timeout=60)
        self.add_item(MovieSelect(candidates, bot=bot))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class TrackSelect(discord.ui.Select):
    """Dropdown for picking which movie to track when there's ambiguity."""

    def __init__(self, candidates: list[dict], added_by: str, added_by_id: str | None = None):
        self.added_by = added_by
        self.added_by_id = added_by_id
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
        if self.added_by_id and str(interaction.user.id) != self.added_by_id:
            await interaction.response.send_message(
                "only the member who ran `/track` can pick this movie.",
                ephemeral=True,
            )
            return

        movie_id = int(self.values[0])
        chosen = self._candidates_by_id.get(self.values[0], {})
        movie_title = chosen.get("title", "Unknown")
        release_date = chosen.get("release_date", "")
        movie_year = release_date[:4] if release_date else "—"

        added = db.add_tracked_movie(
            movie_id,
            movie_title,
            self.added_by,
            self.added_by_id,
        )
        if not added:
            msg = f"**{movie_title} ({movie_year})** is already on the tracked list."
            await interaction.response.edit_message(content=msg, view=None)
            return

        # Defer because the auto-check involves a TMDB call
        await interaction.response.defer()
        msg = await _build_track_response(movie_id, movie_title, movie_year)
        if self.added_by_id:
            await achievement_module.award_for_user(
                interaction.client,
                interaction.user,
                source_type="track",
                source_id=str(movie_id),
            )
        await interaction.edit_original_response(content=msg, view=None)


class TrackSelectView(discord.ui.View):
    """View wrapping TrackSelect for /track."""

    def __init__(self, candidates: list[dict], added_by: str, added_by_id: str | None = None):
        super().__init__(timeout=60)
        self.add_item(TrackSelect(candidates, added_by, added_by_id))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ---------- rental views ----------

def _disable_view_items(view: discord.ui.View) -> None:
    for item in view.children:
        item.disabled = True


async def _defer_component(
    interaction: discord.Interaction,
    *,
    ephemeral: bool = False,
) -> bool:
    """Acknowledge a component click; stale rapid-click interactions can be ignored."""
    try:
        await interaction.response.defer(ephemeral=ephemeral)
        return True
    except discord.NotFound:
        return False
    except discord.HTTPException as e:
        if getattr(e, "code", None) == 40060:
            return False
        raise


async def _send_component_denied(
    interaction: discord.Interaction,
    message: str,
) -> None:
    """Send a best-effort ephemeral denial for component clicks."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.NotFound:
        pass


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
    user_timezone = db.get_user_timezone(user_id)
    due_at = rental_module.compute_due_at(now, user_timezone)

    active_count = _active_rental_count_for_user(user_id)
    if active_count >= rental_module.MAX_ACTIVE_RENTALS_PER_USER:
        await interaction.edit_original_response(
            content=_active_rental_limit_message(active_count),
            embed=None,
            view=None,
        )
        return

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


def _active_rental_limit_message(active_count: int) -> str:
    return (
        f"you already have **{active_count}** active rentals. "
        "return one before checking out another."
    )


def _active_rental_count_for_user(user_id: str) -> int:
    return len(db.get_active_rentals(user_id))


class PickOwnRentalModal(discord.ui.Modal, title="pick a rental"):
    """Modal for choosing a specific rb9 library title from the /rent flow."""

    title_input = discord.ui.TextInput(
        label="movie title",
        placeholder="e.g. Thief",
        max_length=100,
    )
    year_input = discord.ui.TextInput(
        label="year (optional)",
        placeholder="e.g. 1981",
        required=False,
        max_length=4,
    )

    def __init__(self, bot: discord.Client, user_id: str, user_name: str):
        super().__init__()
        self.bot = bot
        self.user_id = user_id
        self.user_name = user_name

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        active_count = _active_rental_count_for_user(self.user_id)
        if active_count >= rental_module.MAX_ACTIVE_RENTALS_PER_USER:
            await interaction.followup.send(
                _active_rental_limit_message(active_count), ephemeral=True
            )
            return

        if not db.get_reviews_channel_id():
            await interaction.followup.send(
                "⚠️ the reviews forum hasn't been configured yet. "
                "ask an admin to run `/setreviews` first.",
                ephemeral=True,
            )
            return

        year_text = str(self.year_input.value or "").strip()
        year = int(year_text) if year_text.isdigit() else None
        title = str(self.title_input.value).strip()

        try:
            movie = await plex.find_movie_by_title(title, year=year)
        except plex.PlexError as e:
            await interaction.followup.send(
                f"⚠️ couldn't reach the library right now: {e}", ephemeral=True
            )
            return

        if movie is None:
            year_note = f" ({year})" if year else ""
            await interaction.followup.send(
                f"couldn't find **{title}{year_note}** in the rb9 library.",
                ephemeral=True,
            )
            return

        view = EmbedRentView(
            bot=self.bot,
            user_id=self.user_id,
            user_name=self.user_name,
            movie=movie,
            initiated_by="selected",
        )
        await interaction.followup.send(
            f"📼 rent **{movie['title']} ({movie.get('year', '?')})**?",
            view=view,
            ephemeral=True,
        )


class RentWarningView(discord.ui.View):
    """
    Step 1 of /rent: lets the user choose how to start a rental.
    """

    def __init__(self, bot: discord.Client, user_id: str, user_name: str):
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id
        self.user_name = user_name
        self._processing = False

        async def random_cb(interaction: discord.Interaction):
            await self._start_random(interaction)

        async def pick_cb(interaction: discord.Interaction):
            await self._pick_own(interaction)

        async def admin_cb(interaction: discord.Interaction):
            await self._ask_admin(interaction)

        async def cancel_cb(interaction: discord.Interaction):
            await self._cancel(interaction)

        random_btn = discord.ui.Button(
            label="roll random",
            style=discord.ButtonStyle.success,
            emoji="🎲",
        )
        random_btn.callback = random_cb

        pick_btn = discord.ui.Button(
            label="pick a movie",
            style=discord.ButtonStyle.primary,
            emoji="📼",
        )
        pick_btn.callback = pick_cb

        admin_btn = discord.ui.Button(
            label="ask an admin",
            style=discord.ButtonStyle.secondary,
            emoji="💬",
        )
        admin_btn.callback = admin_cb

        cancel_btn = discord.ui.Button(
            label="nevermind",
            style=discord.ButtonStyle.secondary,
        )
        cancel_btn.callback = cancel_cb

        self.add_item(random_btn)
        self.add_item(pick_btn)
        self.add_item(admin_btn)
        self.add_item(cancel_btn)

    async def _start_random(self, interaction: discord.Interaction):
        if not await _mark_view_processing(interaction, self, "checking the shelves..."):
            return

        self.stop()

        active_count = _active_rental_count_for_user(self.user_id)
        if active_count >= rental_module.MAX_ACTIVE_RENTALS_PER_USER:
            await interaction.edit_original_response(
                content=_active_rental_limit_message(active_count),
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
            shown_films=[movie],
        )
        embed = embeds.rental_offer_embed(movie, is_last_reroll=False)
        await interaction.edit_original_response(embed=embed, view=pick_view, content=None)

    async def _pick_own(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await _send_component_denied(interaction, "this rental isn't for you.")
            return
        await interaction.response.send_modal(
            PickOwnRentalModal(
                bot=self.bot,
                user_id=self.user_id,
                user_name=self.user_name,
            )
        )

    async def _ask_admin(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await _send_component_denied(interaction, "this rental isn't for you.")
            return
        if not await _mark_view_processing(interaction, self, "sending request..."):
            return

        self.stop()

        active_count = _active_rental_count_for_user(self.user_id)
        if active_count >= rental_module.MAX_ACTIVE_RENTALS_PER_USER:
            await interaction.edit_original_response(
                content=_active_rental_limit_message(active_count),
                embed=None,
                view=None,
            )
            return

        request_channel = None
        request_channel_id = db.get_rental_request_channel_id()
        if request_channel_id:
            request_channel = self.bot.get_channel(request_channel_id)
            if request_channel is None:
                try:
                    request_channel = await self.bot.fetch_channel(request_channel_id)
                except (discord.NotFound, discord.Forbidden):
                    request_channel = None
        request_channel = request_channel or interaction.channel

        if request_channel is not None:
            try:
                await request_channel.send(
                    f"📼 **rental recommendation request**\n"
                    f"{interaction.user.mention} wants an admin pick. "
                    "use `/assignrental` to assign something from rb9."
                )
            except discord.HTTPException:
                pass

        await interaction.edit_original_response(
            content="request sent. an admin can assign you a rental when they have a pick.",
            embed=None,
            view=None,
        )

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
        shown_films: list[dict],
    ):
        super().__init__(timeout=300)
        self.bot = bot
        self.current_film = current_film
        self.rerolls_remaining = rerolls_remaining
        self.shown_keys = shown_keys
        self.user_id = user_id
        self.user_name = user_name
        self.rerolls_used = rerolls_used
        self.shown_films = shown_films
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
            initiated_by="random",
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
        new_shown_films = self.shown_films + [new_film]

        if new_rerolls_remaining == 0:
            # Out of rerolls — let them pick any film they've seen so far
            embed = embeds.rental_offer_embed(new_film, is_last_reroll=False)
            choice_view = RentFinalChoiceView(
                bot=self.bot,
                films=new_shown_films,
                current_film=new_film,
                user_id=self.user_id,
                user_name=self.user_name,
                rerolls_used=new_rerolls_used,
            )
            await interaction.edit_original_response(embed=embed, view=choice_view, content=None)
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
                shown_films=new_shown_films,
            )
            await interaction.edit_original_response(embed=embed, view=new_view, content=None)

    async def on_timeout(self):
        self.stop()


class RentFinalChoiceView(discord.ui.View):
    """
    Final step of /rent once both rerolls are spent: lets the user pick any
    of the films they were shown during the roll/reroll flow. A dropdown
    switches which film is previewed in the embed; accept locks it in.
    """

    def __init__(
        self,
        bot: discord.Client,
        films: list[dict],
        current_film: dict,
        user_id: str,
        user_name: str,
        rerolls_used: int,
    ):
        super().__init__(timeout=300)
        self.bot = bot
        self.films = films
        self.current_film = current_film
        self.user_id = user_id
        self.user_name = user_name
        self.rerolls_used = rerolls_used
        self._processing = False

        select = discord.ui.Select(
            placeholder="pick one of your rolls...",
            min_values=1,
            max_values=1,
        )
        for film in films:
            year = film.get("year") or "?"
            select.add_option(
                label=f"{film['title']} ({year})"[:100],
                value=str(film["rating_key"]),
                default=str(film["rating_key"]) == str(current_film["rating_key"]),
            )

        async def select_cb(interaction: discord.Interaction):
            await self._select(interaction)

        async def accept_cb(interaction: discord.Interaction):
            await self._accept(interaction)

        select.callback = select_cb
        self.select = select
        self.add_item(select)

        accept_btn = discord.ui.Button(
            label="accept rental",
            style=discord.ButtonStyle.success,
            emoji="📼",
        )
        accept_btn.callback = accept_cb
        self.add_item(accept_btn)

    def _refresh_select_defaults(self):
        current_key = str(self.current_film["rating_key"])
        for option in self.select.options:
            option.default = option.value == current_key

    async def _select(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await _send_component_denied(interaction, "this rental isn't for you.")
            return
        if self._processing:
            await _defer_component(interaction)
            return

        chosen_key = self.select.values[0]
        film = next(
            (f for f in self.films if str(f["rating_key"]) == chosen_key),
            self.current_film,
        )
        self.current_film = film
        self._refresh_select_defaults()
        embed = embeds.rental_offer_embed(film, is_last_reroll=False)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _accept(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await _send_component_denied(interaction, "this rental isn't for you.")
            return
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
            initiated_by="random",
        )

    async def on_timeout(self):
        self.stop()


# ---------- embed-level rent view ----------

class EmbedRentView(discord.ui.View):
    """
    Confirm/cancel view for renting a specific film directly from a film card embed.
    Used on /suck, /roll, /rb9, /rb9randomscene, and the daily rec.
    No rerolls — the user already chose this film.
    Accepts either a pre-loaded Plex movie dict (has rating_key) or
    a title+year to look up on confirm.
    """

    def __init__(
        self,
        bot: discord.Client,
        user_id: str,
        user_name: str,
        movie: dict | None = None,
        title: str | None = None,
        year: int | None = None,
        initiated_by: str = "selected",
    ):
        super().__init__(timeout=None)
        self.bot = bot
        self.user_id = user_id
        self.user_name = user_name
        self._movie = movie          # pre-loaded Plex dict, if available
        self._title = title or (movie.get("title") if movie else None)
        self._year = year or (movie.get("year") if movie else None)
        self.initiated_by = initiated_by
        self._processing = False

        confirm_btn = discord.ui.Button(
            label="confirm rental",
            style=discord.ButtonStyle.success,
            emoji="📼",
        )
        confirm_btn.callback = self._confirm

        cancel_btn = discord.ui.Button(
            label="nevermind",
            style=discord.ButtonStyle.secondary,
        )
        cancel_btn.callback = self._cancel

        self.add_item(confirm_btn)
        self.add_item(cancel_btn)

    async def _confirm(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message(
                "this rental isn't for you.", ephemeral=True
            )
            return
        if not await _mark_view_processing(interaction, self, "checking the shelves..."):
            return
        self.stop()

        # Resolve Plex movie dict if we only have title/year
        movie = self._movie
        if movie is None:
            try:
                movie = await plex.find_movie_by_title(self._title, year=self._year)
            except plex.PlexError as e:
                await interaction.edit_original_response(
                    content=f"⚠️ couldn't reach the library right now: {e}",
                    embed=None, view=None,
                )
                return

        if movie is None:
            await interaction.edit_original_response(
                content=(
                    f"⚠️ **{self._title}** doesn't seem to be in the library right now. "
                    "try `/rent` for a random pick."
                ),
                embed=None, view=None,
            )
            return

        active_count = _active_rental_count_for_user(self.user_id)
        if active_count >= rental_module.MAX_ACTIVE_RENTALS_PER_USER:
            await interaction.edit_original_response(
                content=_active_rental_limit_message(active_count),
                embed=None, view=None,
            )
            return

        await _confirm_rental(
            interaction=interaction,
            bot=self.bot,
            movie=movie,
            user_id=self.user_id,
            user_name=self.user_name,
            rerolls_used=0,
            initiated_by=self.initiated_by,
        )

    async def _cancel(self, interaction: discord.Interaction):
        if not await _mark_view_processing(interaction, self, "canceling..."):
            return
        self.stop()
        await interaction.edit_original_response(
            content="no problem - come back when you're ready.",
            embed=None, view=None,
        )

    async def on_timeout(self):
        self.stop()


# ---------- film card view ----------

class FilmCardView(discord.ui.View):
    """
    Combined view attached to public film card embeds (/suck, /roll, /rb9, daily rec).
    Always shows the watchlist button. Shows the rent button only when the film
    is confirmed available in the Plex library.
    """

    def __init__(
        self,
        bot: discord.Client,
        title: str,
        year: int | None,
        tmdb_id: int | None = None,
        poster_url: str | None = None,
        plex_available: bool = False,
        plex_movie: dict | None = None,
    ):
        super().__init__(timeout=None)

        watchlist_custom_id = f"s:wl:t:{tmdb_id}" if tmdb_id else None
        self.add_item(AddToWatchlistButton(
            title=title,
            year=year,
            tmdb_id=tmdb_id,
            poster_url=poster_url,
            source="button",
            custom_id=watchlist_custom_id,
        ))

        if plex_available:
            rent_custom_id = f"s:rent:t:{tmdb_id}" if tmdb_id else None
            rent_btn = discord.ui.Button(
                label="rent this",
                style=discord.ButtonStyle.success,
                emoji="📼",
                custom_id=rent_custom_id,
            )
            _bot = bot
            _title = title
            _year = year
            _plex_movie = plex_movie
            _tmdb_id = tmdb_id

            async def rent_cb(interaction: discord.Interaction):
                if _tmdb_id:
                    await _rent_from_tmdb_id(interaction, _tmdb_id)
                    return
                await _send_embed_rent_prompt(
                    interaction,
                    _bot,
                    movie=_plex_movie,
                    title=_title,
                    year=_year,
                )

            rent_btn.callback = rent_cb
            self.add_item(rent_btn)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class RentThisView(discord.ui.View):
    """View for cards that should offer rental without re-adding to watchlist."""

    def __init__(
        self,
        bot: discord.Client,
        title: str,
        year: int | None,
        plex_movie: dict | None = None,
    ):
        super().__init__(timeout=None)

        rent_btn = discord.ui.Button(
            label="rent this",
            style=discord.ButtonStyle.success,
            emoji="📼",
        )
        _bot = bot
        _title = title
        _year = year
        _plex_movie = plex_movie

        async def rent_cb(interaction: discord.Interaction):
            await _send_embed_rent_prompt(
                interaction,
                _bot,
                movie=_plex_movie,
                title=_title,
                year=_year,
            )

        rent_btn.callback = rent_cb
        self.add_item(rent_btn)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ---------- watchlist button ----------

class AddToWatchlistButton(discord.ui.Button):
    """
    A standalone button that can be added to any film card view.
    Adds the film to the clicking user's internal watchlist.
    Works with TMDB-sourced films (tmdb_id known) and Plex-sourced films
    (tmdb_id=None, will attempt a TMDB search to resolve it).
    """

    def __init__(
        self,
        title: str,
        year: int | None,
        tmdb_id: int | None = None,
        poster_url: str | None = None,
        source: str = "button",
        custom_id: str | None = None,
    ):
        super().__init__(
            label="+ watchlist",
            style=discord.ButtonStyle.secondary,
            emoji="📋",
            custom_id=custom_id,
        )
        self.film_title = title
        self.film_year = year
        self.film_tmdb_id = tmdb_id
        self.film_poster_url = poster_url
        self.film_source = source

    async def callback(self, interaction: discord.Interaction):
        if not await _defer_component(interaction, ephemeral=True):
            return

        user_id = str(interaction.user.id)
        resolved_tmdb_id = self.film_tmdb_id

        # If no tmdb_id (e.g. Plex-sourced film), try to resolve via search
        if resolved_tmdb_id is None:
            try:
                results = await tmdb.search_movie(self.film_title, year=self.film_year)
                if results:
                    resolved_tmdb_id = results[0]["id"]
            except tmdb.TMDBError:
                pass  # store without tmdb_id, roll will handle it

        added = db.watchlist_add(
            user_id=user_id,
            title=self.film_title,
            year=self.film_year,
            tmdb_id=resolved_tmdb_id,
            poster_url=self.film_poster_url,
            source=self.film_source,
        )

        year_str = f" ({self.film_year})" if self.film_year else ""
        if added:
            await interaction.followup.send(
                f"📋 added **{self.film_title}{year_str}** to your watchlist.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"**{self.film_title}{year_str}** is already on your watchlist.",
                ephemeral=True,
            )


class PersistentWatchlistButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=_PERSISTENT_WATCHLIST_RE,
):
    """Persistent handler for public film-card + watchlist buttons."""

    def __init__(self, tmdb_id: int):
        super().__init__(
            discord.ui.Button(
                label="+ watchlist",
                style=discord.ButtonStyle.secondary,
                emoji="📋",
                custom_id=f"s:wl:t:{tmdb_id}",
            )
        )
        self.tmdb_id = tmdb_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item,
        match: re.Match[str],
        /,
    ) -> "PersistentWatchlistButton":
        return cls(int(match.group("tmdb_id")))

    async def callback(self, interaction: discord.Interaction):
        await _watchlist_add_from_tmdb_id(interaction, self.tmdb_id)


class PersistentRentButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=_PERSISTENT_RENT_RE,
):
    """Persistent handler for public film-card rent buttons."""

    def __init__(self, tmdb_id: int):
        super().__init__(
            discord.ui.Button(
                label="rent this",
                style=discord.ButtonStyle.success,
                emoji="📼",
                custom_id=f"s:rent:t:{tmdb_id}",
            )
        )
        self.tmdb_id = tmdb_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item,
        match: re.Match[str],
        /,
    ) -> "PersistentRentButton":
        return cls(int(match.group("tmdb_id")))

    async def callback(self, interaction: discord.Interaction):
        await _rent_from_tmdb_id(interaction, self.tmdb_id)


def register_persistent_public_film_buttons(bot: discord.Client) -> None:
    """Register restart-safe handlers for public film-card buttons."""
    bot.add_dynamic_items(PersistentWatchlistButton, PersistentRentButton)


# ---------- LB watchlist view ----------

class LBWatchlistView(discord.ui.View):
    """
    Paginated view of a Letterboxd watchlist with prev/next,
    a roll button (random pick -> TMDB film card), and an import button.
    """

    def __init__(
        self,
        bot: discord.Client,
        lb_username: str,
        films: list[dict],
        requesting_user_id: str,
        requesting_user_tag: str,
    ):
        super().__init__(timeout=120)
        self.bot = bot
        self.lb_username = lb_username
        self.films = films
        self.requesting_user_id = requesting_user_id
        self.requesting_user_tag = requesting_user_tag
        self.page = 0
        self.total_pages = max(1, -(-len(films) // 5))  # ceil div
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()

        if self.page > 0:
            prev_btn = discord.ui.Button(label="◀ prev", style=discord.ButtonStyle.secondary)
            prev_btn.callback = self._prev
            self.add_item(prev_btn)

        if self.page < self.total_pages - 1:
            next_btn = discord.ui.Button(label="next ▶", style=discord.ButtonStyle.secondary)
            next_btn.callback = self._next
            self.add_item(next_btn)

        if self.films:
            roll_btn = discord.ui.Button(
                label="🎲 roll from this",
                style=discord.ButtonStyle.primary,
            )
            roll_btn.callback = self._roll
            self.add_item(roll_btn)

            import_btn = discord.ui.Button(
                label="📥 import all",
                style=discord.ButtonStyle.success,
            )
            import_btn.callback = self._import
            self.add_item(import_btn)

    async def _prev(self, interaction: discord.Interaction):
        if not await _defer_component(interaction):
            return
        self.page -= 1
        self._build_buttons()
        embed = embeds.lb_watchlist_embed(
            self.lb_username, self.films, self.page, self.total_pages
        )
        await interaction.edit_original_response(embed=embed, view=self)

    async def _next(self, interaction: discord.Interaction):
        if not await _defer_component(interaction):
            return
        self.page += 1
        self._build_buttons()
        embed = embeds.lb_watchlist_embed(
            self.lb_username, self.films, self.page, self.total_pages
        )
        await interaction.edit_original_response(embed=embed, view=self)

    async def _roll(self, interaction: discord.Interaction):
        import random
        if not await _defer_component(interaction):
            return
        if not self.films:
            await interaction.followup.send(
                "that watchlist is empty.", ephemeral=True
            )
            return

        pick = random.choice(self.films)
        title = pick.get("film_title", "")
        year = pick.get("year")

        try:
            results = await tmdb.search_movie(title, year=year)
        except tmdb.TMDBError as e:
            await interaction.followup.send(
                f"⚠️ couldn't look up **{title}** on TMDB: {e}", ephemeral=True
            )
            return

        if not results:
            await interaction.followup.send(
                f"⚠️ couldn't find **{title}** on TMDB. try a different roll.", ephemeral=True
            )
            return

        top = results[0]
        try:
            details = await tmdb.get_movie_details(top["id"])
            providers = await tmdb.get_watch_providers(top["id"], region="US")
        except tmdb.TMDBError as e:
            await interaction.followup.send(f"⚠️ TMDB error: {e}", ephemeral=True)
            return

        release_date = details.get("release_date") or ""
        plex_year = int(release_date[:4]) if release_date[:4].isdigit() else None
        plex_available = await plex.check_availability(details.get("title"), year=plex_year)

        embed = embeds.movie_embed(details, providers, in_theaters=False, plex_available=plex_available)
        embed.title = f"🎲 {embed.title}"

        poster_url = tmdb.poster_url(details.get("poster_path"))
        roll_view = discord.ui.View(timeout=120)
        roll_view.add_item(AddToWatchlistButton(
            title=details.get("title", title),
            year=plex_year,
            tmdb_id=top["id"],
            poster_url=poster_url,
            source="button",
        ))
        if plex_available:
            rent_btn = discord.ui.Button(
                label="rent this", style=discord.ButtonStyle.success, emoji="📼"
            )
            async def rent_cb(intr: discord.Interaction):
                if not await _defer_component(intr, ephemeral=True):
                    return
                movie = await plex.find_movie_by_title(details.get("title"), year=plex_year)
                if movie is None:
                    await intr.followup.send(
                        "⚠️ couldn't find that film in the library right now.", ephemeral=True
                    )
                    return
                view = EmbedRentView(bot=self.bot, movie=movie, user_id=str(intr.user.id), user_name=str(intr.user))
                await intr.followup.send(
                    f"📼 rent **{movie['title']}**?", view=view, ephemeral=True
                )
            rent_btn.callback = rent_cb
            roll_view.add_item(rent_btn)

        await interaction.edit_original_response(embed=embed, view=roll_view, content=None)

    async def _import(self, interaction: discord.Interaction):
        if not await _defer_component(interaction, ephemeral=True):
            return

        user_id = str(interaction.user.id)
        added = 0
        skipped = 0

        for film in self.films:
            ok = db.watchlist_add(
                user_id=user_id,
                title=film.get("film_title", ""),
                year=film.get("year"),
                tmdb_id=None,
                poster_url=film.get("thumb"),
                source="letterboxd",
            )
            if ok:
                added += 1
            else:
                skipped += 1

        parts = [f"📥 imported **{added}** film(s) from {self.lb_username}'s letterboxd watchlist."]
        if skipped:
            parts.append(f"{skipped} already on your watchlist and skipped.")
        if added:
            await achievement_module.award_for_user(
                interaction.client,
                interaction.user,
                source_type="watchlist_import",
                source_id=self.lb_username,
            )
        await interaction.followup.send(" ".join(parts), ephemeral=True)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True



# ---------- my watchlist view ----------

class _RemoveSelect(discord.ui.Select):
    """Dropdown to remove a film from the current page of the watchlist."""

    def __init__(self, watchlist_view: "MyWatchlistView"):
        self.watchlist_view = watchlist_view
        start = watchlist_view.page * MY_WATCHLIST_PAGE_SIZE
        page_entries = watchlist_view.entries[start : start + MY_WATCHLIST_PAGE_SIZE]

        options = [
            discord.SelectOption(
                label=f"{e.get('title', '?')} ({e.get('year') or '?'})"[:100],
                value=str(e["id"]),
            )
            for e in page_entries
        ]
        super().__init__(placeholder="remove a film...", options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.watchlist_view.user_id:
            await _send_component_denied(interaction, "this isn't your watchlist.")
            return
        if not await _defer_component(interaction):
            return
        entry_id = int(self.values[0])
        removed = db.watchlist_remove_by_id(entry_id, self.watchlist_view.user_id)
        if removed:
            achievement_module.record_event(
                self.watchlist_view.user_id,
                str(interaction.user),
                "watchlist_remove",
                str(entry_id),
            )
            await achievement_module.award_for_user(
                interaction.client,
                interaction.user,
                source_type="watchlist_remove",
                source_id=str(entry_id),
            )
        self.watchlist_view.entries = db.get_watchlist(self.watchlist_view.user_id)
        self.watchlist_view.total_pages = max(
            1,
            -(-len(self.watchlist_view.entries) // MY_WATCHLIST_PAGE_SIZE),
        )
        if self.watchlist_view.page >= self.watchlist_view.total_pages:
            self.watchlist_view.page = max(0, self.watchlist_view.total_pages - 1)
        next_view = MyWatchlistView.for_page(
            bot=self.watchlist_view.bot,
            user_id=self.watchlist_view.user_id,
            user_tag=self.watchlist_view.user_tag,
            entries=self.watchlist_view.entries,
            page=self.watchlist_view.page,
        )
        embed = embeds.mywatchlist_embed(
            next_view.user_tag,
            next_view.entries,
            next_view.page,
            next_view.total_pages,
            len(next_view.entries),
        )
        self.watchlist_view.stop()
        await interaction.edit_original_response(embed=embed, view=next_view)


class MyWatchlistView(discord.ui.View):
    """
    Paginated view of the user's personal watchlist with prev/next,
    a roll button, and a dropdown to remove films from the current page.
    """

    def __init__(
        self,
        bot: discord.Client,
        user_id: str,
        user_tag: str,
        entries: list[dict],
    ):
        super().__init__(timeout=120)
        self.bot = bot
        self.user_id = user_id
        self.user_tag = user_tag
        self.entries = entries
        self.page = 0
        self.total_pages = max(1, -(-len(entries) // MY_WATCHLIST_PAGE_SIZE))
        self._rebuild()

    @classmethod
    def for_page(
        cls,
        bot: discord.Client,
        user_id: str,
        user_tag: str,
        entries: list[dict],
        page: int,
    ) -> "MyWatchlistView":
        view = cls(bot=bot, user_id=user_id, user_tag=user_tag, entries=entries)
        view.page = page
        view._rebuild()
        return view

    def _rebuild(self):
        self.clear_items()

        # Row 0: remove dropdown (only if there are entries on this page)
        start = self.page * MY_WATCHLIST_PAGE_SIZE
        page_entries = self.entries[start : start + MY_WATCHLIST_PAGE_SIZE]
        if page_entries:
            self.add_item(_RemoveSelect(self))

        # Row 1: navigation + roll
        if self.page > 0:
            prev_btn = discord.ui.Button(label="◀ prev", style=discord.ButtonStyle.secondary, row=1)
            prev_btn.callback = self._prev
            self.add_item(prev_btn)

        if self.page < self.total_pages - 1:
            next_btn = discord.ui.Button(label="next ▶", style=discord.ButtonStyle.secondary, row=1)
            next_btn.callback = self._next
            self.add_item(next_btn)

        if self.entries:
            roll_btn = discord.ui.Button(
                label="🎲 roll from list", style=discord.ButtonStyle.primary, row=1
            )
            roll_btn.callback = self._roll
            self.add_item(roll_btn)

    async def _ensure_owner(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) == self.user_id:
            return True
        await _send_component_denied(interaction, "this isn't your watchlist.")
        return False

    async def _prev(self, interaction: discord.Interaction):
        if not await self._ensure_owner(interaction):
            return
        if not await _defer_component(interaction):
            return
        next_view = MyWatchlistView.for_page(
            bot=self.bot,
            user_id=self.user_id,
            user_tag=self.user_tag,
            entries=self.entries,
            page=max(0, self.page - 1),
        )
        embed = embeds.mywatchlist_embed(
            next_view.user_tag,
            next_view.entries,
            next_view.page,
            next_view.total_pages,
            len(next_view.entries),
        )
        self.stop()
        await interaction.edit_original_response(embed=embed, view=next_view)

    async def _next(self, interaction: discord.Interaction):
        if not await self._ensure_owner(interaction):
            return
        if not await _defer_component(interaction):
            return
        next_view = MyWatchlistView.for_page(
            bot=self.bot,
            user_id=self.user_id,
            user_tag=self.user_tag,
            entries=self.entries,
            page=min(self.total_pages - 1, self.page + 1),
        )
        embed = embeds.mywatchlist_embed(
            next_view.user_tag,
            next_view.entries,
            next_view.page,
            next_view.total_pages,
            len(next_view.entries),
        )
        self.stop()
        await interaction.edit_original_response(embed=embed, view=next_view)

    async def _roll(self, interaction: discord.Interaction):
        import random
        if not await self._ensure_owner(interaction):
            return
        if not self.entries:
            await _send_component_denied(interaction, "your watchlist is empty.")
            return

        if not await _defer_component(interaction):
            return
        pick = random.choice(self.entries)
        title = pick.get("title", "")
        year = pick.get("year")
        tmdb_id = pick.get("tmdb_id")

        try:
            if tmdb_id:
                details = await tmdb.get_movie_details(tmdb_id)
                providers = await tmdb.get_watch_providers(tmdb_id, region="US")
            else:
                results = await tmdb.search_movie(title, year=year)
                if not results:
                    await interaction.followup.send(
                        f"⚠️ couldn't find **{title}** on TMDB.", ephemeral=True
                    )
                    return
                details = await tmdb.get_movie_details(results[0]["id"])
                providers = await tmdb.get_watch_providers(results[0]["id"], region="US")
        except tmdb.TMDBError as e:
            await interaction.followup.send(f"⚠️ TMDB error: {e}", ephemeral=True)
            return

        release_date = details.get("release_date") or ""
        plex_year = int(release_date[:4]) if release_date[:4].isdigit() else None
        plex_available = await plex.check_availability(details.get("title"), year=plex_year)

        embed = embeds.movie_embed(details, providers, in_theaters=False, plex_available=plex_available)
        embed.title = f"🎲 from your watchlist: {embed.title}"

        roll_view = None
        if plex_available:
            roll_view = RentThisView(
                bot=self.bot,
                title=details.get("title", title),
                year=plex_year,
            )

        await interaction.edit_original_response(embed=embed, view=roll_view, content=None)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ---------- watchlist add disambiguation ----------

class WatchlistAddSelect(discord.ui.Select):
    """Dropdown to pick which film to add when /watchlist add returns multiple matches."""

    def __init__(self, candidates: list[dict], user_id: str):
        self.user_id = user_id
        self._candidates_by_id = {str(m["id"]): m for m in candidates[:25]}

        options = []
        for movie in candidates[:25]:
            title = movie.get("title", "Unknown")
            release_date = movie.get("release_date", "")
            year = release_date[:4] if release_date else "-"
            overview = movie.get("overview") or ""
            desc = overview[:80] + "..." if len(overview) > 80 else overview
            options.append(
                discord.SelectOption(
                    label=f"{title} ({year})"[:100],
                    description=desc[:100],
                    value=str(movie["id"]),
                )
            )

        super().__init__(placeholder="pick which one to add", options=options)

    async def callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message(
                "this isn't your command.", ephemeral=True
            )
            return

        movie_id = int(self.values[0])
        chosen = self._candidates_by_id.get(self.values[0], {})
        title = chosen.get("title", "Unknown")
        release_date = chosen.get("release_date") or ""
        year = int(release_date[:4]) if release_date[:4].isdigit() else None
        poster_url = tmdb.poster_url(chosen.get("poster_path"))

        added = db.watchlist_add(
            user_id=self.user_id,
            title=title,
            year=year,
            tmdb_id=movie_id,
            poster_url=poster_url,
            source="manual",
        )

        year_str = f" ({year})" if year else ""
        if added:
            msg = f"📋 added **{title}{year_str}** to your watchlist."
        else:
            msg = f"**{title}{year_str}** is already on your watchlist."

        await interaction.response.edit_message(content=msg, view=None)


class WatchlistAddSelectView(discord.ui.View):
    def __init__(self, candidates: list[dict], user_id: str):
        super().__init__(timeout=60)
        self.add_item(WatchlistAddSelect(candidates, user_id))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ---------- macguffin views ----------

class MacGuffinListView(discord.ui.View):
    """Paginated MacGuffin inventory with one view button per visible card."""

    def __init__(
        self,
        user_id: str,
        user_tag: str,
        cards: list[dict],
        bot: discord.Client,
    ):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.user_tag = user_tag
        self.cards = cards
        self.bot = bot
        self.page = 0
        self.total_pages = max(1, -(-len(cards) // MACGUFFIN_PAGE_SIZE))
        self._rebuild()

    @classmethod
    def for_page(
        cls,
        user_id: str,
        user_tag: str,
        cards: list[dict],
        bot: discord.Client,
        page: int,
    ) -> "MacGuffinListView":
        view = cls(user_id=user_id, user_tag=user_tag, cards=cards, bot=bot)
        view.page = page
        view._rebuild()
        return view

    def _rebuild(self):
        self.clear_items()

        start = self.page * MACGUFFIN_PAGE_SIZE
        page_cards = self.cards[start : start + MACGUFFIN_PAGE_SIZE]
        for card in page_cards:
            label = f"{card.get('emoji', '')} view".strip()
            button = discord.ui.Button(
                label=label[:80],
                style=discord.ButtonStyle.secondary,
                row=0,
            )

            async def callback(
                interaction: discord.Interaction,
                selected_card: dict = card,
            ):
                await self._show_card(interaction, selected_card)

            button.callback = callback
            self.add_item(button)

        if self.page > 0:
            prev_btn = discord.ui.Button(
                label="prev",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
            prev_btn.callback = self._prev
            self.add_item(prev_btn)

        if self.page < self.total_pages - 1:
            next_btn = discord.ui.Button(
                label="next",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
            next_btn.callback = self._next
            self.add_item(next_btn)

    async def _guard_user(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) == self.user_id:
            return True
        await interaction.response.send_message(
            "this isn't your collection.", ephemeral=True
        )
        return False

    async def _show_card(self, interaction: discord.Interaction, card: dict):
        if not await self._guard_user(interaction):
            return
        if not await _defer_component(interaction):
            return
        view = MacGuffinCardView(
            user_id=self.user_id,
            user_tag=self.user_tag,
            cards=self.cards,
            page=self.page,
            bot=self.bot,
        )
        embed = embeds.macguffin_card_embed(card, card)
        self.stop()
        await interaction.edit_original_response(embed=embed, view=view)

    async def _prev(self, interaction: discord.Interaction):
        if not await self._guard_user(interaction):
            return
        if not await _defer_component(interaction):
            return
        next_view = MacGuffinListView.for_page(
            user_id=self.user_id,
            user_tag=self.user_tag,
            cards=self.cards,
            bot=self.bot,
            page=max(0, self.page - 1),
        )
        embed = embeds.macguffin_list_embed(
            next_view.user_tag,
            next_view.cards,
            next_view.page,
            next_view.total_pages,
        )
        self.stop()
        await interaction.edit_original_response(embed=embed, view=next_view)

    async def _next(self, interaction: discord.Interaction):
        if not await self._guard_user(interaction):
            return
        if not await _defer_component(interaction):
            return
        next_view = MacGuffinListView.for_page(
            user_id=self.user_id,
            user_tag=self.user_tag,
            cards=self.cards,
            bot=self.bot,
            page=min(self.total_pages - 1, self.page + 1),
        )
        embed = embeds.macguffin_list_embed(
            next_view.user_tag,
            next_view.cards,
            next_view.page,
            next_view.total_pages,
        )
        self.stop()
        await interaction.edit_original_response(embed=embed, view=next_view)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class MacGuffinCardView(discord.ui.View):
    """Single MacGuffin card view with a back button to the inventory page."""

    def __init__(
        self,
        user_id: str,
        user_tag: str,
        cards: list[dict],
        page: int,
        bot: discord.Client,
    ):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.user_tag = user_tag
        self.cards = cards
        self.page = page
        self.bot = bot

        back_btn = discord.ui.Button(
            label="back",
            style=discord.ButtonStyle.secondary,
            row=0,
        )
        back_btn.callback = self._back
        self.add_item(back_btn)

    async def _back(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message(
                "this isn't your collection.", ephemeral=True
            )
            return
        if not await _defer_component(interaction):
            return
        list_view = MacGuffinListView.for_page(
            user_id=self.user_id,
            user_tag=self.user_tag,
            cards=self.cards,
            bot=self.bot,
            page=self.page,
        )
        embed = embeds.macguffin_list_embed(
            list_view.user_tag,
            list_view.cards,
            list_view.page,
            list_view.total_pages,
        )
        self.stop()
        await interaction.edit_original_response(embed=embed, view=list_view)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
