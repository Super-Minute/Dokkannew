"""Shared response-mapping helpers used by every gateway mixin.

Field names in the recovered spec are unconfirmed; the candidate-key tables
below are the single source of truth. Mixins must import from here rather
than keeping local copies, so a server-side rename is a one-line edit.
"""
from __future__ import annotations

from typing import Callable, TypeVar

from dokkan.adapters.http_gateway_errors import GatewayError  # see C2
from dokkan.domain.models import Card, CardType, Difficulty, Rarity, Stage, Stamina

T = TypeVar("T")

# User / account
_USER_KEYS = ("user",)
_NAME_KEYS = ("name", "account_name", "user_name")
_STAMINA_KEYS = ("act", "stamina")
_STAMINA_MAX_KEYS = ("act_max", "max_act", "stamina_max")
_STAMINA_ALL_KEYS = _STAMINA_KEYS + _STAMINA_MAX_KEYS
_CAPACITY_KEYS = ("card_capacity", "capacity")
# Cards
_CARDS_KEYS = ("cards",)
_CARD_ID_KEYS = ("id", "card_id", "uid")
_CARD_NAME_KEYS = ("name", "card_name")
_CARD_RARITY_KEYS = ("rarity", "base_rarity")
_CARD_TYPE_KEYS = ("type", "element", "attribute")
_CARD_LEVEL_KEYS = ("level", "lv")
_CARD_ATTACK_KEYS = ("attack", "atk")
_CARD_LOCKED_KEYS = ("locked", "lock")
_CARD_FAVORITE_KEYS = ("favorite", "favo")
# Events / stages
_EVENTS_KEYS = ("events",)
_EVENT_ID_KEYS = ("id", "event_id")
_EVENT_NAME_KEYS = ("name", "event_name")
_STAGE_KEYS = ("stages",)
_STAGE_ID_KEYS = ("id", "stage_id")
_STAGE_NAME_KEYS = ("name", "stage_name")
_STAGE_DIFFICULTY_KEYS = ("difficulty",)
_STAGE_STAMINA_KEYS = ("stamina_cost", "stamina", "act")

__all__ = [
    "_USER_KEYS", "_NAME_KEYS", "_STAMINA_KEYS", "_STAMINA_MAX_KEYS",
    "_STAMINA_ALL_KEYS", "_CAPACITY_KEYS", "_CARDS_KEYS", "_CARD_ID_KEYS",
    "_CARD_NAME_KEYS", "_CARD_RARITY_KEYS", "_CARD_TYPE_KEYS", "_CARD_LEVEL_KEYS",
    "_CARD_ATTACK_KEYS", "_CARD_LOCKED_KEYS", "_CARD_FAVORITE_KEYS",
    "_EVENTS_KEYS", "_EVENT_ID_KEYS", "_EVENT_NAME_KEYS", "_STAGE_KEYS",
    "_STAGE_ID_KEYS", "_STAGE_NAME_KEYS", "_STAGE_DIFFICULTY_KEYS",
    "_STAGE_STAMINA_KEYS",
    "first", "as_bool", "rarity", "card", "stage", "stamina",
    "safe_map", "ensure_ok",
]


def first(source: dict, keys: tuple[str, ...], default=None):
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return default


def as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y", "on")
    raise GatewayError(f"cannot interpret {type(value).__name__} {value!r} as boolean")


def rarity(value) -> Rarity:
    if isinstance(value, int):
        return Rarity(value)
    return Rarity[str(value).upper()]


def card(raw: dict) -> Card:
    return Card(
        id=int(first(raw, _CARD_ID_KEYS)),
        name=str(first(raw, _CARD_NAME_KEYS, "")),
        rarity=rarity(first(raw, _CARD_RARITY_KEYS, 0)),
        type=CardType[str(first(raw, _CARD_TYPE_KEYS, "STR")).upper()],
        level=int(first(raw, _CARD_LEVEL_KEYS, 1)),
        locked=bool(first(raw, _CARD_LOCKED_KEYS, False)),
        favorite=bool(first(raw, _CARD_FAVORITE_KEYS, False)),
        attack=int(first(raw, _CARD_ATTACK_KEYS, 0)),
    )


def stage(raw: dict) -> Stage:
    return Stage(
        id=str(first(raw, _STAGE_ID_KEYS, "")),
        name=str(first(raw, _STAGE_NAME_KEYS, "")),
        difficulty=Difficulty(int(first(raw, _STAGE_DIFFICULTY_KEYS, 0))),
        stamina_cost=int(first(raw, _STAGE_STAMINA_KEYS, 0)),
    )


def stamina(user: dict) -> Stamina:
    current = int(first(user, _STAMINA_KEYS, 0))
    return Stamina(current, int(first(user, _STAMINA_MAX_KEYS, current)))


def safe_map(fn: Callable[[dict], T], raw, *, what: str) -> T:
    """Apply ``fn`` to ``raw``, converting domain/schema errors to GatewayError."""
    try:
        return fn(raw)
    except (KeyError, ValueError, TypeError) as exc:
        fields = sorted(raw) if isinstance(raw, dict) else type(raw).__name__
        raise GatewayError(f"cannot map {what}: {exc} (fields={fields})") from exc


def ensure_ok(payload: dict, what: str) -> None:
    """Raise if a 2xx payload signals failure via ``error`` or ``success=False``."""
    if payload.get("error"):
        raise GatewayError(
            f"{what} failed: {payload.get('message') or payload.get('error')}")
    success = payload.get("success")
    if success is not None and not as_bool(success):
        raise GatewayError(f"{what} failed: success={success!r}")