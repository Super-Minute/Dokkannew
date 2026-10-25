"""Pure decision rules: which cards to sell, which team fits a stage."""
from __future__ import annotations

from typing import Iterable

from .errors import InvalidTeam
from .models import MAX_TEAM_SIZE, Card, Rarity, Stage, Team

SELLABLE_RARITIES = frozenset({Rarity.N, Rarity.R, Rarity.SR})


def is_sellable(card: Card) -> bool:
    return card.rarity in SELLABLE_RARITIES and not card.locked and not card.favorite


def cards_to_sell(cards: Iterable[Card]) -> list[Card]:
    return [c for c in cards if is_sellable(c)]


def eligible_for_stage(card: Card, stage: Stage) -> bool:
    if stage.allowed_types is not None and card.type not in stage.allowed_types:
        return False
    if stage.max_rarity is not None and card.rarity > stage.max_rarity:
        return False
    return True


def build_team(cards: Iterable[Card], stage: Stage, size: int = MAX_TEAM_SIZE) -> Team:
    """Pick the strongest eligible cards for the stage's restrictions.

    Ties on attack, rarity and level go to the lowest card id (the id is negated
    because the sort is descending).
    """
    if not 1 <= size <= MAX_TEAM_SIZE:
        raise InvalidTeam(f"team size must be 1..{MAX_TEAM_SIZE}, got {size}")
    pool = sorted(
        (c for c in cards if eligible_for_stage(c, stage)),
        key=lambda c: (c.attack, c.rarity, c.level, -c.id),
        reverse=True,
    )
    if not pool:
        raise InvalidTeam(f"no card satisfies the restrictions of stage {stage.id}")
    return Team(tuple(c.id for c in pool[:size]))
