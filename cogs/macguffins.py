import asyncio

import discord
from discord import app_commands
from discord.ext import commands

import db
import embeds
import logger
import macguffin as macguffin_module
import views
import achievements as achievement_module


_CLAIM_LOCKS: dict[str, asyncio.Lock] = {}


def _merge_inventory(records: list[dict]) -> list[dict]:
    if not macguffin_module.CARDS:
        macguffin_module.load_cards()
    cards: list[dict] = []
    for record in records:
        card = macguffin_module.CARDS.get(record.get("macguffin_id"))
        if not card:
            continue
        merged = dict(card)
        merged.update(record)
        cards.append(merged)
    return cards


def _find_matches(query: str, cards: list[dict]) -> list[dict]:
    needle = query.strip().lower()
    if not needle:
        return []
    return [
        card
        for card in cards
        if needle in card.get("name", "").lower()
        or needle in card.get("id", "").lower()
    ]


def _claim_lock(user_id: str) -> asyncio.Lock:
    lock = _CLAIM_LOCKS.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _CLAIM_LOCKS[user_id] = lock
    return lock


async def _announce_drop(
    interaction: discord.Interaction,
    card: dict,
    user: discord.Member,
) -> None:
    claimed = await asyncio.to_thread(db.get_claimed_macguffin_ids)
    drop_embed = embeds.macguffin_drop_embed(
        card,
        user.mention,
        len(claimed),
        len(macguffin_module.CARDS),
    )
    await interaction.channel.send(embed=drop_embed)


class MacGuffinCog(commands.Cog):
    """Collectible MacGuffin commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="claimguffin", description="claim your one free macguffin")
    async def claimguffin(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        user_id = str(interaction.user.id)
        async with _claim_lock(user_id):
            used = await asyncio.to_thread(db.has_used_free_claim, user_id)
            if used:
                await interaction.followup.send(
                    "you already claimed your free macguffin.", ephemeral=True
                )
                return

            try:
                card = await asyncio.to_thread(
                    macguffin_module.drop_macguffin,
                    user_id,
                    str(interaction.user),
                    "claim",
                )
                await asyncio.to_thread(db.record_free_claim_used, user_id)
                await _announce_drop(interaction, card, interaction.user)
                await achievement_module.award_for_user(
                    self.bot,
                    interaction.user,
                    source_type="macguffin_claim",
                    source_id=card.get("id"),
                )
                await interaction.followup.send("check the channel!", ephemeral=True)
            except macguffin_module.MacGuffinPoolEmpty:
                await interaction.followup.send(
                    "all macguffins have already been claimed.", ephemeral=True
                )
            except Exception as e:
                logger.log_exception("claimguffin", e)
                await interaction.followup.send(
                    "something went wrong claiming your macguffin.", ephemeral=True
                )

    @app_commands.command(name="giftguffin", description="gift one of your macguffins to another member")
    @app_commands.describe(
        user="the member to gift to",
        card="name of the macguffin to gift",
    )
    async def giftguffin(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        card: str,
    ):
        await interaction.response.defer(ephemeral=True)

        sender_id = str(interaction.user.id)
        recipient_id = str(user.id)
        if user.bot:
            await interaction.followup.send("bots don't need macguffins.", ephemeral=True)
            return
        if recipient_id == sender_id:
            await interaction.followup.send(
                "you already have that macguffin.", ephemeral=True
            )
            return

        records = await asyncio.to_thread(db.get_macguffin_inventory, sender_id)
        owned_cards = _merge_inventory(records)
        matches = _find_matches(card, owned_cards)

        if not matches:
            await interaction.followup.send(
                "i couldn't find a macguffin by that name in your collection.",
                ephemeral=True,
            )
            return

        if len(matches) > 1:
            options = "\n".join(
                f"{match.get('emoji', '')} **{match.get('name', 'unknown macguffin')}**"
                for match in matches[:10]
            )
            await interaction.followup.send(
                "that matched more than one macguffin. try a more specific name:\n"
                f"{options}",
                ephemeral=True,
            )
            return

        selected = matches[0]
        macguffin_id = selected["id"]
        still_owned = await asyncio.to_thread(
            db.user_owns_macguffin,
            sender_id,
            macguffin_id,
        )
        if not still_owned:
            await interaction.followup.send(
                "looks like that macguffin already moved.", ephemeral=True
            )
            return

        transferred = await asyncio.to_thread(
            macguffin_module.transfer,
            macguffin_id,
            recipient_id,
            str(user),
        )
        if not transferred:
            await interaction.followup.send(
                "i couldn't gift that macguffin.", ephemeral=True
            )
            return

        rarity = selected.get("rarity", "unknown")
        emoji = selected.get("emoji", "")
        name = selected.get("name", "unknown macguffin")
        await interaction.channel.send(
            f"{interaction.user.mention} gifted {user.mention} "
            f"the [{rarity}] {emoji} **{name}**"
        )
        achievement_module.record_event(
            sender_id,
            str(interaction.user),
            "macguffin_gift_sent",
            macguffin_id,
        )
        achievement_module.record_event(
            recipient_id,
            str(user),
            "macguffin_gift_received",
            macguffin_id,
        )
        await achievement_module.award_for_user(
            self.bot,
            interaction.user,
            source_type="macguffin_gift",
            source_id=macguffin_id,
        )
        await achievement_module.award_for_user(
            self.bot,
            user,
            source_type="macguffin_gift",
            source_id=macguffin_id,
        )
        await interaction.followup.send("gift sent.", ephemeral=True)

    @app_commands.command(name="myguffins", description="view your macguffin collection")
    async def myguffins(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        records = await asyncio.to_thread(
            db.get_macguffin_inventory,
            str(interaction.user.id),
        )
        cards = _merge_inventory(records)
        if not cards:
            await interaction.followup.send(
                "you don't have any macguffins yet.", ephemeral=True
            )
            return

        view = views.MacGuffinListView(
            user_id=str(interaction.user.id),
            user_tag=str(interaction.user),
            cards=cards,
            bot=self.bot,
        )
        embed = embeds.macguffin_list_embed(
            str(interaction.user),
            cards,
            view.page,
            view.total_pages,
        )
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(
        name="adminguffins",
        description="view or edit a member's macguffins (admin only)",
    )
    @app_commands.describe(
        action="what to do",
        user="the member whose collection to manage",
        card="card name or id for add/remove",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="view", value="view"),
        app_commands.Choice(name="add", value="add"),
        app_commands.Choice(name="remove", value="remove"),
        app_commands.Choice(name="random", value="random"),
    ])
    @app_commands.default_permissions(manage_guild=True)
    async def adminguffins(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        user: discord.Member,
        card: str | None = None,
    ):
        await interaction.response.defer(ephemeral=True)

        if not macguffin_module.CARDS:
            macguffin_module.load_cards()

        target_id = str(user.id)
        records = await asyncio.to_thread(db.get_macguffin_inventory, target_id)
        inventory = _merge_inventory(records)

        if action.value == "view":
            if not inventory:
                await interaction.followup.send(
                    f"**{user}** doesn't have any macguffins.", ephemeral=True
                )
                return

            lines = [
                f"{item.get('emoji', '')} **{item.get('name', 'unknown macguffin')}** "
                f"- {item.get('rarity', 'unknown')} (`{item.get('id')}`)"
                for item in inventory[:25]
            ]
            extra = ""
            if len(inventory) > 25:
                extra = f"\n...and {len(inventory) - 25} more."
            await interaction.followup.send(
                f"**{user}'s macguffins** ({len(inventory)})\n"
                + "\n".join(lines)
                + extra,
                ephemeral=True,
            )
            return

        if action.value == "random":
            try:
                selected = await asyncio.to_thread(
                    macguffin_module.drop_macguffin,
                    target_id,
                    str(user),
                    "admin",
                )
            except macguffin_module.MacGuffinPoolEmpty:
                await interaction.followup.send(
                    "all macguffins have already been claimed.", ephemeral=True
                )
                return
            except Exception as e:
                logger.log_exception("adminguffins_random", e)
                await interaction.followup.send(
                    "i couldn't assign a random macguffin.", ephemeral=True
                )
                return

            await interaction.followup.send(
                f"random pull assigned to **{user}**: "
                f"{selected.get('emoji', '')} **{selected.get('name')}** "
                f"({selected.get('rarity', 'unknown')}).",
                ephemeral=True,
            )
            await _announce_drop(interaction, selected, user)
            await achievement_module.award_for_user(
                self.bot,
                user,
                source_type="macguffin_admin",
                source_id=selected.get("id"),
            )
            return

        if not card:
            await interaction.followup.send(
                "include a card name or id for add/remove.", ephemeral=True
            )
            return

        if action.value == "remove":
            matches = _find_matches(card, inventory)
            if not matches:
                await interaction.followup.send(
                    f"couldn't find that macguffin in **{user}**'s collection.",
                    ephemeral=True,
                )
                return
            if len(matches) > 1:
                options = "\n".join(
                    f"{match.get('emoji', '')} **{match.get('name', 'unknown macguffin')}** "
                    f"(`{match.get('id')}`)"
                    for match in matches[:10]
                )
                await interaction.followup.send(
                    "that matched more than one macguffin. try a more specific name:\n"
                    f"{options}",
                    ephemeral=True,
                )
                return

            selected = matches[0]
            removed = await asyncio.to_thread(
                db.remove_macguffin,
                selected["id"],
                target_id,
            )
            if not removed:
                await interaction.followup.send(
                    "i couldn't remove that macguffin.", ephemeral=True
                )
                return
            await interaction.followup.send(
                f"removed {selected.get('emoji', '')} **{selected.get('name')}** "
                f"from **{user}**.",
                ephemeral=True,
            )
            return

        if action.value == "add":
            matches = _find_matches(
                card,
                list(macguffin_module.CARDS.values()),
            )
            if not matches:
                await interaction.followup.send(
                    "couldn't find a macguffin by that name or id.", ephemeral=True
                )
                return
            if len(matches) > 1:
                options = "\n".join(
                    f"{match.get('emoji', '')} **{match.get('name', 'unknown macguffin')}** "
                    f"(`{match.get('id')}`)"
                    for match in matches[:10]
                )
                await interaction.followup.send(
                    "that matched more than one macguffin. try a more specific name:\n"
                    f"{options}",
                    ephemeral=True,
                )
                return

            selected = matches[0]
            existing = await asyncio.to_thread(db.get_macguffin_record, selected["id"])
            if existing and existing.get("owner_id") == target_id:
                await interaction.followup.send(
                    f"**{user}** already has {selected.get('emoji', '')} "
                    f"**{selected.get('name')}**.",
                    ephemeral=True,
                )
                return

            if existing:
                transferred = await asyncio.to_thread(
                    macguffin_module.transfer,
                    selected["id"],
                    target_id,
                    str(user),
                )
                if not transferred:
                    await interaction.followup.send(
                        "i couldn't move that macguffin.", ephemeral=True
                    )
                    return
                await _announce_drop(interaction, selected, user)
                await achievement_module.award_for_user(
                    self.bot,
                    user,
                    source_type="macguffin_admin",
                    source_id=selected.get("id"),
                )
                await interaction.followup.send(
                    f"moved {selected.get('emoji', '')} **{selected.get('name')}** "
                    f"from **{existing.get('owner_tag', 'another member')}** to **{user}**.",
                    ephemeral=True,
                )
                return

            await asyncio.to_thread(
                db.claim_macguffin,
                selected["id"],
                target_id,
                str(user),
                "admin",
            )
            await _announce_drop(interaction, selected, user)
            await achievement_module.award_for_user(
                self.bot,
                user,
                source_type="macguffin_admin",
                source_id=selected.get("id"),
            )
            await interaction.followup.send(
                f"added {selected.get('emoji', '')} **{selected.get('name')}** "
                f"to **{user}**.",
                ephemeral=True,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(MacGuffinCog(bot))
