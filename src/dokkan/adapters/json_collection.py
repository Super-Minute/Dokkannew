"""Load a card collection from a local JSON file (a list of card objects)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dokkan.application.errors import CollectionError
from dokkan.domain.errors import DomainError
from dokkan.domain.models import Card, CardType, Rarity


def _card(raw: Any, index: int) -> Card:
    where = f"card #{index}"
    if not isinstance(raw, dict):
        raise CollectionError(f"{where} must be an object")
    try:
        return Card(
            id=_int(raw["id"], f"{where}.id"),
            name=str(raw["name"]),
            rarity=Rarity[str(raw["rarity"]).upper()],
            type=CardType(str(raw["type"]).upper()),
            level=_int(raw.get("level", 1), f"{where}.level"),
            locked=_flag(raw.get("locked", False), f"{where}.locked"),
            favorite=_flag(raw.get("favorite", False), f"{where}.favorite"),
            attack=_int(raw.get("attack", 0), f"{where}.attack"),
        )
    except KeyError as exc:
        raise CollectionError(f"{where}: missing or unknown value {exc}") from exc
    except ValueError as exc:
        raise CollectionError(f"{where}: {exc}") from exc
    except DomainError as exc:
        raise CollectionError(f"{where}: {exc}") from exc


def _int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CollectionError(f"{where} must be an integer")
    return value


def _flag(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise CollectionError(f"{where} must be true or false")
    return value


def load_collection(path: Path) -> list[Card]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectionError(f"cannot read {path}: {exc}") from exc
    if not isinstance(raw, list):
        raise CollectionError("collection root must be a list of cards")
    cards = [_card(item, i) for i, item in enumerate(raw)]
    if len({c.id for c in cards}) != len(cards):
        raise CollectionError("duplicate card id in collection")
    return cards
