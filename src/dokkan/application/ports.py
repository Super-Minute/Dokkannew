"""Interfaces the application needs from the outside world (implemented by adapters)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from dokkan.domain.models import Account, Banner, Card, Event, Stage, Stamina, Team


class RefillSource(str, Enum):
    MEAT = "meat"
    STONE = "stone"


@dataclass(frozen=True)
class StageResult:
    cleared: bool


class GameGateway(Protocol):
    """Everything the use cases may ask of the game. Adapters decide how."""

    def fetch_account(self) -> Account: ...

    def list_events(self) -> Sequence[Event]: ...

    def run_stage(self, stage: Stage, team: Team) -> StageResult: ...

    def sell_cards(self, card_ids: Sequence[int]) -> None: ...

    def refill_stamina(self, source: RefillSource) -> Stamina:
        """Raise RefillUnavailable when the source cannot be used."""
        ...

    def list_banners(self) -> Sequence[Banner]:
        """List summon banners (GET /gashas; memory 02/09)."""
        ...

    def summon(self, banner_id: int, multi: bool, count: int = 1) -> Sequence[Card]:
        """Draw ``count`` summon rounds on one banner; returns the cards drawn.

        ``count`` is summon ROUNDS, not copies (memory 09: "count is 1-1000
        summon rounds, not copies"); ``multi`` selects the multi pull.
        """
        ...

    def accept_gifts(self) -> int:
        """Accept every pending gift; returns how many were accepted."""
        ...

    def accept_missions(self) -> int:
        """Accept every claimable mission reward; returns how many."""
        ...

    def accept_login_bonus(self) -> bool:
        """Claim the daily login bonus; False when nothing is left to claim."""
        ...

    def run_board(self, board_id: int) -> StageResult:
        """Play one Greatest Warrior (sugoroku) board: start then finish."""
        ...

    def link_status(self) -> Mapping[str, str]:
        """Link state per service: google/apple/facebook/game_center/link_code
        → linked | unlinked | unserved (memory 02/05, GET /user/links)."""
        ...


class CollectionLoader(Protocol):
    """Reads a card collection from a local file; no game connection involved."""

    def __call__(self, path: Path) -> Sequence[Card]:
        """Raise CollectionError when the file cannot be read or is malformed."""
        ...
