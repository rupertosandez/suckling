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
SETS: dict[str, dict] = {}
GENERAL_SET_LABEL = "General"

_ASSET_PATH = Path(__file__).resolve().parent / "assets" / "macguffins.json"
_SET_ASSET_PATH = Path(__file__).resolve().parent / "assets" / "macguffin_sets.json"


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


def load_sets() -> dict[str, dict]:
    """Load and validate the MacGuffin set asset file."""
    global SETS

    if not CARDS:
        load_cards()

    try:
        with _SET_ASSET_PATH.open("r", encoding="utf-8") as f:
            raw_sets = json.load(f)
    except FileNotFoundError as e:
        raise MacGuffinAssetError(f"macguffin set asset file missing: {_SET_ASSET_PATH}") from e
    except json.JSONDecodeError as e:
        raise MacGuffinAssetError(f"macguffin set asset file is invalid json: {e}") from e

    if not isinstance(raw_sets, list):
        raise MacGuffinAssetError("macguffin set asset file must contain a list")

    loaded: dict[str, dict] = {}
    achievement_ids: set[str] = set()
    required = {
        "id",
        "label",
        "achievement_id",
        "achievement_name",
        "description",
        "hint",
        "emoji",
        "macguffin_ids",
    }
    for index, item_set in enumerate(raw_sets, start=1):
        if not isinstance(item_set, dict):
            raise MacGuffinAssetError(f"macguffin set entry {index} must be an object")

        missing = required - set(item_set)
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise MacGuffinAssetError(
                f"macguffin set entry {index} is missing: {missing_text}"
            )

        set_id = str(item_set["id"])
        if set_id in loaded:
            raise MacGuffinAssetError(f"duplicate macguffin set id: {set_id}")

        achievement_id = str(item_set["achievement_id"])
        if achievement_id in achievement_ids:
            raise MacGuffinAssetError(
                f"duplicate macguffin set achievement id: {achievement_id}"
            )
        achievement_ids.add(achievement_id)

        macguffin_ids = item_set["macguffin_ids"]
        if not isinstance(macguffin_ids, list) or not macguffin_ids:
            raise MacGuffinAssetError(
                f"macguffin set {set_id} must include at least one macguffin"
            )

        normalized_ids = [str(macguffin_id) for macguffin_id in macguffin_ids]
        duplicate_ids = sorted(
            macguffin_id
            for macguffin_id in set(normalized_ids)
            if normalized_ids.count(macguffin_id) > 1
        )
        if duplicate_ids:
            duplicate_text = ", ".join(duplicate_ids)
            raise MacGuffinAssetError(
                f"macguffin set {set_id} has duplicate macguffins: {duplicate_text}"
            )

        unknown_ids = sorted(macguffin_id for macguffin_id in normalized_ids if macguffin_id not in CARDS)
        if unknown_ids:
            unknown_text = ", ".join(unknown_ids)
            raise MacGuffinAssetError(
                f"macguffin set {set_id} references unknown macguffins: {unknown_text}"
            )

        normalized = dict(item_set)
        normalized["id"] = set_id
        normalized["achievement_id"] = achievement_id
        normalized["macguffin_ids"] = normalized_ids
        loaded[set_id] = normalized

    SETS = loaded
    return SETS


def _ensure_loaded() -> None:
    if not CARDS:
        load_cards()


def _ensure_sets_loaded() -> None:
    if not SETS:
        load_sets()


def all_sets() -> list[dict]:
    """Return validated MacGuffin set definitions in catalog order."""
    _ensure_sets_loaded()
    return list(SETS.values())


def sets_for_card(macguffin_id: str) -> list[dict]:
    """Return every set containing a MacGuffin ID."""
    _ensure_sets_loaded()
    return [
        item_set
        for item_set in SETS.values()
        if macguffin_id in item_set.get("macguffin_ids", [])
    ]


def set_labels_for_card(macguffin_id: str) -> list[str]:
    """Return short set labels for a MacGuffin, or General when unset."""
    labels = [
        str(item_set.get("label") or item_set["id"])
        for item_set in sets_for_card(macguffin_id)
    ]
    return labels or [GENERAL_SET_LABEL]


def _available_cards(claimed_ids: set[str]) -> dict[str, list[dict]]:
    available = {rarity: [] for rarity in RARITY_WEIGHTS}
    for card in CARDS.values():
        if card["id"] not in claimed_ids:
            available[card["rarity"]].append(card)
    return available


def _roll_rarity(
    excluded: set[str] | None = None,
    weights: dict[str, int] | None = None,
) -> str | None:
    excluded = excluded or set()
    weights = weights or RARITY_WEIGHTS
    rarities = [rarity for rarity in RARITY_WEIGHTS if rarity not in excluded]
    if not rarities:
        return None
    rarity_weights = [weights.get(rarity, RARITY_WEIGHTS[rarity]) for rarity in rarities]
    return random.choices(rarities, weights=rarity_weights, k=1)[0]


def _pick_card(
    claimed_ids: set[str],
    weights: dict[str, int] | None = None,
) -> dict:
    available = _available_cards(claimed_ids)
    if not any(available.values()):
        raise MacGuffinPoolEmpty("all macguffins have been claimed")

    excluded: set[str] = set()
    while len(excluded) < len(RARITY_WEIGHTS):
        rolled = _roll_rarity(excluded, weights=weights)
        if rolled is None:
            break
        for rarity in [rolled, *RARITY_FALLBACK[rolled]]:
            if rarity in excluded:
                continue
            if available[rarity]:
                return random.choice(available[rarity])
            excluded.add(rarity)

    raise MacGuffinPoolEmpty("all macguffins have been claimed")


def drop_macguffin(
    user_id: str,
    user_tag: str,
    via: str,
    rarity_weights: dict[str, int] | None = None,
) -> dict:
    """Claim one random unowned MacGuffin for a user and return its card dict."""
    _ensure_loaded()

    # A concurrent claim can win the unique macguffin_id insert first, so retry
    # with a fresh claimed set a few times before surfacing the DB error.
    for _ in range(5):
        claimed_ids = db.get_claimed_macguffin_ids()
        card = _pick_card(claimed_ids, weights=rarity_weights)
        try:
            db.claim_macguffin(card["id"], user_id, user_tag, via)
            return dict(card)
        except sqlite3.IntegrityError:
            continue

    claimed_ids = db.get_claimed_macguffin_ids()
    card = _pick_card(claimed_ids, weights=rarity_weights)
    db.claim_macguffin(card["id"], user_id, user_tag, via)
    return dict(card)


def transfer(macguffin_id: str, new_owner_id: str, new_owner_tag: str) -> bool:
    """Transfer an already-claimed MacGuffin to a new owner."""
    return db.transfer_macguffin(macguffin_id, new_owner_id, new_owner_tag)
