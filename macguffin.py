import json
import random
import sqlite3
from pathlib import Path

import db

RARITY_WEIGHTS = {"common": 65, "rare": 30, "iconic": 5}
RARITY_FALLBACK = {
    "iconic": ["rare", "common"],
    "rare": ["common", "iconic"],
    "common": ["rare", "iconic"],
}

CARDS: dict[str, dict] = {}

_ASSET_PATH = Path(__file__).resolve().parent / "assets" / "macguffins.json"


class MacGuffinError(Exception):
    """Base exception for MacGuffin feature failures."""


class MacGuffinPoolEmpty(MacGuffinError):
    """Raised when every MacGuffin has already been claimed."""


class MacGuffinAssetError(MacGuffinError):
    """Raised when the MacGuffin asset file is missing or invalid."""


def load_cards() -> dict[str, dict]:
    """Load and validate the MacGuffin asset file."""
    global CARDS

    try:
        with _ASSET_PATH.open("r", encoding="utf-8") as f:
            raw_cards = json.load(f)
    except FileNotFoundError as e:
        raise MacGuffinAssetError(f"macguffin asset file missing: {_ASSET_PATH}") from e
    except json.JSONDecodeError as e:
        raise MacGuffinAssetError(f"macguffin asset file is invalid json: {e}") from e

    if not isinstance(raw_cards, list):
        raise MacGuffinAssetError("macguffin asset file must contain a list")

    loaded: dict[str, dict] = {}
    required = {"id", "name", "emoji", "rarity", "source", "flavor"}
    for index, card in enumerate(raw_cards, start=1):
        if not isinstance(card, dict):
            raise MacGuffinAssetError(f"macguffin entry {index} must be an object")

        missing = required - set(card)
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise MacGuffinAssetError(
                f"macguffin entry {index} is missing: {missing_text}"
            )

        card_id = str(card["id"])
        rarity = str(card["rarity"]).lower()
        if rarity not in RARITY_WEIGHTS:
            raise MacGuffinAssetError(
                f"macguffin {card_id} has unknown rarity: {card['rarity']}"
            )
        if card_id in loaded:
            raise MacGuffinAssetError(f"duplicate macguffin id: {card_id}")

        normalized = dict(card)
        normalized["id"] = card_id
        normalized["rarity"] = rarity
        loaded[card_id] = normalized

    CARDS = loaded
    return CARDS


def _ensure_loaded() -> None:
    if not CARDS:
        load_cards()


def _available_cards(claimed_ids: set[str]) -> dict[str, list[dict]]:
    available = {rarity: [] for rarity in RARITY_WEIGHTS}
    for card in CARDS.values():
        if card["id"] not in claimed_ids:
            available[card["rarity"]].append(card)
    return available


def _roll_rarity(excluded: set[str] | None = None) -> str | None:
    excluded = excluded or set()
    rarities = [rarity for rarity in RARITY_WEIGHTS if rarity not in excluded]
    if not rarities:
        return None
    weights = [RARITY_WEIGHTS[rarity] for rarity in rarities]
    return random.choices(rarities, weights=weights, k=1)[0]


def _pick_card(claimed_ids: set[str]) -> dict:
    available = _available_cards(claimed_ids)
    if not any(available.values()):
        raise MacGuffinPoolEmpty("all macguffins have been claimed")

    excluded: set[str] = set()
    while len(excluded) < len(RARITY_WEIGHTS):
        rolled = _roll_rarity(excluded)
        if rolled is None:
            break
        for rarity in [rolled, *RARITY_FALLBACK[rolled]]:
            if rarity in excluded:
                continue
            if available[rarity]:
                return random.choice(available[rarity])
            excluded.add(rarity)

    raise MacGuffinPoolEmpty("all macguffins have been claimed")


def drop_macguffin(user_id: str, user_tag: str, via: str) -> dict:
    """Claim one random unowned MacGuffin for a user and return its card dict."""
    _ensure_loaded()

    # A concurrent claim can win the unique macguffin_id insert first, so retry
    # with a fresh claimed set a few times before surfacing the DB error.
    for _ in range(5):
        claimed_ids = db.get_claimed_macguffin_ids()
        card = _pick_card(claimed_ids)
        try:
            db.claim_macguffin(card["id"], user_id, user_tag, via)
            return dict(card)
        except sqlite3.IntegrityError:
            continue

    claimed_ids = db.get_claimed_macguffin_ids()
    card = _pick_card(claimed_ids)
    db.claim_macguffin(card["id"], user_id, user_tag, via)
    return dict(card)


def transfer(macguffin_id: str, new_owner_id: str, new_owner_tag: str) -> bool:
    """Transfer an already-claimed MacGuffin to a new owner."""
    return db.transfer_macguffin(macguffin_id, new_owner_id, new_owner_tag)
