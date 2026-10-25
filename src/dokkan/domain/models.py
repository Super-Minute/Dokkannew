from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum

from .errors import InsufficientStamina, InvalidTeam, InvalidValue

MAX_TEAM_SIZE = 6


class Rarity(IntEnum):
    N = 0
    R = 1
    SR = 2
    SSR = 3
    UR = 4
    LR = 5


class CardType(str, Enum):
    AGL = "AGL"
    TEQ = "TEQ"
    INT = "INT"
    STR = "STR"
    PHY = "PHY"


class Difficulty(IntEnum):
    EASY = 0
    NORMAL = 1
    HARD = 2
    Z_HARD = 3


@dataclass(frozen=True)
class Card:
    id: int
    name: str
    rarity: Rarity
    type: CardType
    level: int = 1
    locked: bool = False
    favorite: bool = False
    attack: int = 0

    def __post_init__(self) -> None:
        if not self.name:
            raise InvalidValue("card name is empty")
        if self.level < 1 or self.attack < 0:
            raise InvalidValue("card level must be >= 1 and attack >= 0")


@dataclass(frozen=True)
class Stage:
    id: str
    name: str
    difficulty: Difficulty
    stamina_cost: int
    allowed_types: frozenset[CardType] | None = None  # None = no restriction
    max_rarity: Rarity | None = None

    def __post_init__(self) -> None:
        if self.stamina_cost < 0:
            raise InvalidValue("stage stamina_cost is negative")


@dataclass(frozen=True)
class Event:
    id: int
    name: str
    stages: tuple[Stage, ...] = ()


@dataclass(frozen=True)
class Banner:
    """A summon banner (gasha) as listed by the game."""
    id: int
    name: str


@dataclass(frozen=True)
class Stamina:
    current: int
    maximum: int

    def __post_init__(self) -> None:
        if self.current < 0 or self.maximum < 0:
            raise InvalidValue("stamina values must be non-negative")

    def can_afford(self, cost: int) -> bool:
        return self.current >= cost

    def after_spending(self, cost: int) -> "Stamina":
        if cost < 0:
            raise InvalidValue("cannot spend a negative amount")
        if not self.can_afford(cost):
            raise InsufficientStamina(f"need {cost}, have {self.current}")
        return Stamina(self.current - cost, self.maximum)


@dataclass(frozen=True)
class Team:
    card_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.card_ids:
            raise InvalidTeam("team is empty")
        if len(self.card_ids) > MAX_TEAM_SIZE:
            raise InvalidTeam(f"team exceeds {MAX_TEAM_SIZE} cards")
        if len(set(self.card_ids)) != len(self.card_ids):
            raise InvalidTeam("duplicate card in team")


@dataclass(frozen=True)
class Account:
    """One player's state as last fetched from the game."""

    name: str
    stamina: Stamina
    cards: tuple[Card, ...] = field(default_factory=tuple)
    card_capacity: int | None = None  # None = capacity unknown

    @property
    def box_is_full(self) -> bool:
        return self.card_capacity is not None and len(self.cards) >= self.card_capacity
