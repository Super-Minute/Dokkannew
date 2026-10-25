"""Offline analysis of a card collection: nothing here talks to a game."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Sequence

from dokkan.domain.models import MAX_TEAM_SIZE, Card, CardType, Rarity, Stage, Team, Difficulty
from dokkan.domain.policies import build_team, cards_to_sell


@dataclass(frozen=True)
class BoxSummary:
    total: int
    by_rarity: dict[Rarity, int]
    by_type: dict[CardType, int]
    sellable: int


def summarize_box(cards: Sequence[Card]) -> BoxSummary:
    rarity = Counter(c.rarity for c in cards)
    types = Counter(c.type for c in cards)
    return BoxSummary(
        total=len(cards),
        by_rarity={r: rarity[r] for r in sorted(rarity, reverse=True)},
        by_type={t: types[t] for t in sorted(types, key=lambda t: t.value)},
        sellable=len(cards_to_sell(cards)),
    )


def plan_sale(cards: Sequence[Card]) -> list[Card]:
    """The cards `sell auto` would sell, without selling anything."""
    return cards_to_sell(cards)


def plan_team(
    cards: Sequence[Card],
    allowed_types: frozenset[CardType] | None = None,
    max_rarity: Rarity | None = None,
    size: int = MAX_TEAM_SIZE,
) -> Team:
    stage = Stage("offline", "offline", Difficulty.NORMAL, 0, allowed_types, max_rarity)
    return build_team(cards, stage, size)
