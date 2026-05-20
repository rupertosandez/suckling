import asyncio
import re

import discord
from discord import app_commands
from discord.ext import commands

import db
import embeds
import views
import letterboxd as lb_module


_bot: commands.Bot | None = None

lb_group = app_commands.Group(name="lb", description="letterboxd integration")


def _film_key(item: dict) -> str:
    title = item.get("film_title") or item.get("title") or ""
    year = item.get("year")
    normalized = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    return f"{normalized}:{year or ''}"


def _resolve_lb_target(
    interaction: discord.Interaction,
    user: discord.Member | None,
    username: str | None,
) -> tuple[str | None, str | None, str | None]:
    if user is not None:
        lb_user = db.get_lb_username(str(user.id))
        if not lb_user:
            return None, None, f"**{user.display_name}** hasn't linked a letterboxd account."
        return lb_user, user.display_name, None

    if username is not None:
        return username, username, None

    lb_user = db.get_lb_username(str(interaction.user.id))
    if not lb_user:
        return (
            None,
            None,
            "you haven't linked a letterboxd account yet. use `/lb link <username>` to connect one.",
        )
    return lb_user, interaction.user.display_name, None


def _resolve_explicit_lb_target(
    user: discord.Member | None,
    username: str | None,
    label: str,
) -> tuple[str | None, str | None, str | None]:
    if user is not None and username is not None:
        return None, None, f"pick either `{label}_user` or `{label}_username`, not both."

    if user is not None:
        lb_user = db.get_lb_username(str(user.id))
        if not lb_user:
            return None, None, f"**{user.display_name}** hasn't linked a letterboxd account."
        return lb_user, user.display_name, None

    if username is not None:
        return username, username, None

    return None, None, f"pick `{label}_user` or `{label}_username`."


def _tastecheck_payload(
    user_a: str,
    label_a: str,
    diary_a: list[dict],
    watchlist_a: list[dict],
    user_b: str,
    label_b: str,
    diary_b: list[dict],
    watchlist_b: list[dict],
) -> dict:
    diary_by_key_a = {_film_key(entry): entry for entry in diary_a}
    diary_by_key_b = {_film_key(entry): entry for entry in diary_b}
    shared_keys = set(diary_by_key_a) & set(diary_by_key_b)

    shared = []
    rated_diffs = []
    for key in shared_keys:
        left = diary_by_key_a[key]
        right = diary_by_key_b[key]
        diff = None
        if left.get("rating") is not None and right.get("rating") is not None:
            diff = abs(float(left["rating"]) - float(right["rating"]))
            rated_diffs.append(diff)
        shared.append({
            "title": left.get("film_title", "Unknown"),
            "year": left.get("year"),
            "left_rating": left.get("rating"),
            "right_rating": right.get("rating"),
            "left_stars": left.get("stars", ""),
            "right_stars": right.get("stars", ""),
            "diff": diff,
            "link": left.get("link") or right.get("link") or "",
        })

    shared.sort(key=lambda item: (item["diff"] is None, item["diff"] or 0, item["title"]))
    agreements = [item for item in shared if item["diff"] is not None]
    disagreements = sorted(
        agreements,
        key=lambda item: (item["diff"], item["title"]),
        reverse=True,
    )

    watchlist_by_key_a = {_film_key(film): film for film in watchlist_a}
    watchlist_by_key_b = {_film_key(film): film for film in watchlist_b}
    shared_watchlist = [
        watchlist_by_key_a[key]
        for key in sorted(
            set(watchlist_by_key_a) & set(watchlist_by_key_b),
            key=lambda k: watchlist_by_key_a[k].get("film_title", ""),
        )
    ]

    base_score = 35 if shared else 15
    overlap_score = min(30, len(shared) * 6)
    watchlist_score = min(20, len(shared_watchlist) * 4)
    rating_score = 0
    avg_diff = None
    if rated_diffs:
        avg_diff = sum(rated_diffs) / len(rated_diffs)
        rating_score = max(0, round(35 * (1 - (avg_diff / 5))))

    score = max(0, min(100, base_score + overlap_score + watchlist_score + rating_score))
    if not shared and not shared_watchlist:
        score = 10

    if score >= 85:
        label = "video store soulmates"
    elif score >= 70:
        label = "double-feature material"
    elif score >= 50:
        label = "solid shelf neighbors"
    elif score >= 30:
        label = "interesting programming meeting"
    else:
        label = "chaotic rental energy"

    return {
        "user_a": user_a,
        "user_b": user_b,
        "label_a": label_a,
        "label_b": label_b,
        "score": score,
        "label": label,
        "shared": shared,
        "agreements": agreements[:3],
        "disagreements": disagreements[:3],
        "shared_watchlist": shared_watchlist[:8],
        "shared_count": len(shared),
        "rated_overlap_count": len(rated_diffs),
        "shared_watchlist_count": len(shared_watchlist),
        "avg_diff": avg_diff,
    }


@lb_group.command(name="link", description="link your letterboxd account to the bot")
@app_commands.describe(username="your letterboxd username")
async def lb_link(interaction: discord.Interaction, username: str):
    await interaction.response.defer(ephemeral=True)
    try:
        valid = await lb_module.validate_username(username)
    except lb_module.LetterboxdError as e:
        await interaction.followup.send(
            f"⚠️ couldn't reach letterboxd right now: {e}", ephemeral=True
        )
        return

    if not valid:
        await interaction.followup.send(
            f"❌ couldn't find a public letterboxd account for **{username}**. "
            "check the username and make sure the account is public.",
            ephemeral=True,
        )
        return

    db.link_lb_account(str(interaction.user.id), username)
    await interaction.followup.send(
        f"✅ linked your letterboxd account: **{username}**\n"
        "use `/lb profile` to see your recent watches.",
        ephemeral=True,
    )


@lb_group.command(name="unlink", description="unlink your letterboxd account")
async def lb_unlink(interaction: discord.Interaction):
    removed = db.unlink_lb_account(str(interaction.user.id))
    if removed:
        await interaction.response.send_message(
            "✅ letterboxd account unlinked.", ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "you don't have a linked letterboxd account.", ephemeral=True
        )


@lb_group.command(
    name="profile",
    description="see recent letterboxd watches for yourself or another member",
)
@app_commands.describe(
    user="a server member (uses their linked lb account)",
    username="or enter a letterboxd username directly",
)
async def lb_profile(
    interaction: discord.Interaction,
    user: discord.Member | None = None,
    username: str | None = None,
):
    await interaction.response.defer()

    if user is not None:
        lb_user = db.get_lb_username(str(user.id))
        discord_tag = str(user)
        if not lb_user:
            await interaction.followup.send(
                f"**{user.display_name}** hasn't linked a letterboxd account. "
                "they can use `/lb link` to connect one.",
            )
            return
    elif username is not None:
        lb_user = username
        discord_tag = None
    else:
        lb_user = db.get_lb_username(str(interaction.user.id))
        discord_tag = str(interaction.user)
        if not lb_user:
            await interaction.followup.send(
                "you haven't linked a letterboxd account yet. "
                "use `/lb link <username>` to connect one.",
            )
            return

    try:
        entries = await lb_module.get_diary(lb_user)
    except lb_module.LetterboxdError as e:
        msg = str(e)
        if "not_found" in msg:
            await interaction.followup.send(f"❌ no letterboxd account found for **{lb_user}**.")
        elif "private" in msg:
            await interaction.followup.send(f"❌ **{lb_user}**'s letterboxd account is private.")
        else:
            await interaction.followup.send(f"⚠️ couldn't fetch letterboxd data: {e}")
        return

    embed = embeds.lb_profile_embed(lb_user, entries, discord_tag=discord_tag)
    await interaction.followup.send(embed=embed)


@lb_group.command(
    name="watchlist",
    description="browse a letterboxd watchlist, roll from it, or import it",
)
@app_commands.describe(
    user="a server member (uses their linked lb account)",
    username="or enter a letterboxd username directly",
)
async def lb_watchlist_cmd(
    interaction: discord.Interaction,
    user: discord.Member | None = None,
    username: str | None = None,
):
    await interaction.response.defer()

    if user is not None:
        lb_user = db.get_lb_username(str(user.id))
        if not lb_user:
            await interaction.followup.send(
                f"**{user.display_name}** hasn't linked a letterboxd account."
            )
            return
    elif username is not None:
        lb_user = username
    else:
        lb_user = db.get_lb_username(str(interaction.user.id))
        if not lb_user:
            await interaction.followup.send(
                "you haven't linked a letterboxd account yet. "
                "use `/lb link <username>` to connect one.",
            )
            return

    try:
        films = await lb_module.get_watchlist(lb_user)
    except lb_module.LetterboxdError as e:
        msg = str(e)
        if "not_found" in msg:
            await interaction.followup.send(f"❌ no letterboxd account found for **{lb_user}**.")
        elif "private" in msg:
            await interaction.followup.send(f"❌ **{lb_user}**'s watchlist is private.")
        else:
            await interaction.followup.send(f"⚠️ couldn't fetch watchlist: {e}")
        return

    total_pages = max(1, -(-len(films) // 5))
    embed = embeds.lb_watchlist_embed(lb_user, films, page=0, total_pages=total_pages)
    view = views.LBWatchlistView(
        bot=_bot,
        lb_username=lb_user,
        films=films,
        requesting_user_id=str(interaction.user.id),
        requesting_user_tag=str(interaction.user),
    )
    await interaction.followup.send(embed=embed, view=view)


@lb_group.command(
    name="group",
    description="see what everyone in the server has been watching lately",
)
async def lb_group_cmd(interaction: discord.Interaction):
    await interaction.response.defer()

    accounts = db.get_all_lb_accounts()
    if not accounts:
        await interaction.followup.send(
            "no one has linked a letterboxd account yet. use `/lb link` to be first."
        )
        return

    activity = []
    for account in accounts:
        uid = account["user_id"]
        lb_user = account["lb_username"]
        try:
            member = interaction.guild.get_member(int(uid))
            discord_tag = member.display_name if member else lb_user
        except Exception:
            discord_tag = lb_user

        try:
            entries = await lb_module.get_diary(lb_user)
            activity.append({
                "discord_tag": discord_tag,
                "lb_username": lb_user,
                "entries": entries,
            })
        except lb_module.LetterboxdError:
            continue

    embed = embeds.lb_group_embed(activity)
    await interaction.followup.send(embed=embed)


@lb_group.command(
    name="tastecheck",
    description="compare recent letterboxd taste between two people",
)
@app_commands.describe(
    a_user="first server member",
    b_user="second server member",
    a_username="or enter the first letterboxd username directly",
    b_username="or enter the second letterboxd username directly",
)
async def lb_tastecheck(
    interaction: discord.Interaction,
    a_user: discord.Member | None = None,
    b_user: discord.Member | None = None,
    a_username: str | None = None,
    b_username: str | None = None,
):
    await interaction.response.defer()

    lb_a, label_a, err = _resolve_explicit_lb_target(a_user, a_username, "a")
    if err:
        await interaction.followup.send(err)
        return

    lb_b, label_b, err = _resolve_explicit_lb_target(b_user, b_username, "b")
    if err:
        await interaction.followup.send(err)
        return

    try:
        diary_a, diary_b = await asyncio.gather(
            lb_module.get_diary(lb_a),
            lb_module.get_diary(lb_b),
        )
    except lb_module.LetterboxdError as e:
        await interaction.followup.send(f"⚠️ couldn't fetch letterboxd diaries: {e}")
        return

    watchlist_a = []
    watchlist_b = []
    watchlist_note = None
    try:
        watchlist_a, watchlist_b = await asyncio.gather(
            lb_module.get_watchlist(lb_a),
            lb_module.get_watchlist(lb_b),
        )
    except lb_module.LetterboxdError:
        watchlist_note = "watchlist overlap skipped because at least one watchlist could not be fetched."

    payload = _tastecheck_payload(
        user_a=lb_a,
        label_a=label_a,
        diary_a=diary_a,
        watchlist_a=watchlist_a,
        user_b=lb_b,
        label_b=label_b,
        diary_b=diary_b,
        watchlist_b=watchlist_b,
    )
    embed = embeds.lb_tastecheck_embed(payload, watchlist_note=watchlist_note)
    await interaction.followup.send(embed=embed)

async def setup(bot: commands.Bot):
    global _bot
    _bot = bot
    bot.tree.add_command(lb_group)