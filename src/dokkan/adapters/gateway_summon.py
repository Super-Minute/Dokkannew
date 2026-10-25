"""Summon (gasha) operations for the game API (memories 02, 09, 12).

Recovered command grammar (verbatim from the target's strings):

- ``summon <id> <m/s> [count]`` — m=multi, s=single; count is 1-1000 summon
  ROUNDS, not copies; 999 means "until stones run out".
- ``summon <id> <m/s> <IDxN[,IDxN...]>`` — target-card summon: seek each IDxN
  total INCLUDING owned copies (ex. ``summon 2196 m 1034010x2,1034020x1``).
- ``list summons`` — list all available summon banners.

This is an independent, single-account implementation: it talks only to
the game API on the user's own save and contains no licensing or tier
system (memory 13 records the recovered facts about the original's
licensing for spec completeness).

``SummonMixin`` is composed onto
:class:`~dokkan.adapters.http_gateway.HttpGateway` and reuses its transport
helpers (``_call``); it imports no siblings. The candidate-key tables below
are local copies in the house style of http_gateway's (the response field
names are documented-unconfirmed: the recovered spec gives the endpoints and
the command grammar, not these payload keys — first present key wins).
"""
from __future__ import annotations

from typing import Sequence

from dokkan.application.errors import ApplicationError
from dokkan.domain.models import Banner, Card, CardType, Rarity

# Candidate keys for mapping server responses (documented-unconfirmed).
_GASHAS_KEYS = ("gashas", "banners")
_BANNER_ID_KEYS = ("id", "gasha_id")
_BANNER_NAME_KEYS = ("name", "title")
_DRAWN_KEYS = ("cards", "new_cards", "results")
_CARD_ID_KEYS = ("id", "card_id", "uid")
_CARD_NAME_KEYS = ("name", "card_name")
_CARD_RARITY_KEYS = ("rarity", "base_rarity")
_CARD_TYPE_KEYS = ("type", "element", "attribute")
_CARD_LEVEL_KEYS = ("level", "lv")
_CARD_LOCKED_KEYS = ("locked", "lock")
_CARD_FAVORITE_KEYS = ("favorite", "favo")
_CARD_ATTACK_KEYS = ("attack", "atk")


def _first(source: dict, keys: tuple[str, ...], default=None):
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return default


def _rarity(value) -> Rarity:
    if isinstance(value, int):
        return Rarity(value)
    return Rarity[str(value).upper()]


def _card(raw: dict) -> Card:
    return Card(
        id=int(_first(raw, _CARD_ID_KEYS)),
        name=str(_first(raw, _CARD_NAME_KEYS, "")),
        rarity=_rarity(_first(raw, _CARD_RARITY_KEYS, 0)),
        type=CardType[str(_first(raw, _CARD_TYPE_KEYS, "STR")).upper()],
        level=int(_first(raw, _CARD_LEVEL_KEYS, 1)),
        locked=bool(_first(raw, _CARD_LOCKED_KEYS, False)),
        favorite=bool(_first(raw, _CARD_FAVORITE_KEYS, False)),
        attack=int(_first(raw, _CARD_ATTACK_KEYS, 0)),
    )


class SummonMixin:
    """GET /gashas + POST /gashas/{id}/draw (memory 02/12)."""

    def list_banners(self) -> Sequence[Banner]:
        payload = self._call("GET", "/gashas")
        banners = []
        for raw in _first(payload, _GASHAS_KEYS, []):
            banners.append(Banner(
                id=int(_first(raw, _BANNER_ID_KEYS, 0)),
                name=str(_first(raw, _BANNER_NAME_KEYS, "")),
            ))
        return banners

    def summon(self, banner_id: int, multi: bool, count: int = 1) -> Sequence[Card]:
        """Draw ``count`` summon rounds; one POST per round, every card returned.

        The multi/single flag rides the request under ``gasha_type``
        (documented-unconfirmed candidate field; identity is already sealed
        into the envelope by ``_call``). A round whose reply carries an
        ``error`` field stops the loop with an ApplicationError — callers
        that need partial results should draw one round at a time (as the
        SummonCards use case does).
        """
        drawn = []
        for _ in range(count):
            payload = self._call("POST", f"/gashas/{banner_id}/draw",
                                 payload={"gasha_type": "multi" if multi else "single"})
            if payload.get("error"):
                # GatewayError lives in http_gateway (which imports this
                # mixin), so the adapter-level base class is raised here.
                raise ApplicationError(
                    f"draw failed: {payload.get('message') or payload.get('error')}")
            drawn.extend(_card(raw) for raw in _first(payload, _DRAWN_KEYS, []))
        return drawn
