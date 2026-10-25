"""An offline, in-memory game used for demos and the CLI smoke path.

It is the only GameGateway shipped here. A gateway that talks to a real service
belongs in this ring too, written and authorised by whoever operates that service.
"""
from __future__ import annotations

from dokkan.application.errors import RefillUnavailable
from dokkan.application.ports import RefillSource, StageResult
from dokkan.domain.models import (
    Account, Banner, Card, CardType, Difficulty, Event, Rarity, Stage, Stamina, Team,
)


class SimulatedGateway:
    def __init__(self, stamina: int = 30, meat: int = 2, stones: int = 5) -> None:
        self._maximum = 50
        self._stamina = stamina
        self._items = {RefillSource.MEAT: meat, RefillSource.STONE: stones}
        self._next_id = 100
        self._cards = [
            Card(1, "Hero", Rarity.LR, CardType.STR, level=80, attack=9000),
            Card(2, "Recruit", Rarity.N, CardType.AGL, attack=100),
            Card(3, "Soldier", Rarity.SR, CardType.TEQ, level=10, attack=1500),
        ]
        self._events = [
            Event(1, "Training", (
                Stage("1001", "Easy Field", Difficulty.EASY, 6),
                Stage("1002", "Z-Hard Field", Difficulty.Z_HARD, 20, max_rarity=Rarity.SR),
            )),
        ]
        self._banners = [Banner(1, "Simulated Legends"), Banner(2, "Simulated Ticket")]

    def fetch_account(self) -> Account:
        return Account("sim", Stamina(self._stamina, self._maximum), tuple(self._cards), card_capacity=6)

    def ping(self) -> bool:
        return True

    def list_events(self) -> list[Event]:
        return list(self._events)

    def run_stage(self, stage: Stage, team: Team) -> StageResult:
        self._stamina -= stage.stamina_cost
        drop = Card(self._next_id, f"Drop{self._next_id}", Rarity.N, CardType.PHY)
        self._next_id += 1
        self._cards.append(drop)
        return StageResult(cleared=True)

    def sell_cards(self, card_ids) -> None:
        sold = set(card_ids)
        self._cards = [c for c in self._cards if c.id not in sold]

    def refill_stamina(self, source: RefillSource) -> Stamina:
        if self._items[source] <= 0:
            raise RefillUnavailable(source.value)
        self._items[source] -= 1
        self._stamina = self._maximum
        return Stamina(self._stamina, self._maximum)

    def list_banners(self) -> list[Banner]:
        return list(self._banners)

    def summon(self, banner_id: int, multi: bool, count: int = 1) -> list[Card]:
        drawn = []
        for _ in range(count):
            for _ in range(7 if multi else 1):
                card = Card(self._next_id, f"Pull{self._next_id}", Rarity.N, CardType.PHY)
                self._next_id += 1
                self._cards.append(card)
                drawn.append(card)
        return drawn

    def accept_gifts(self) -> int:
        return 3

    def accept_missions(self) -> int:
        return 2

    def accept_login_bonus(self) -> bool:
        return True

    def run_board(self, board_id: int) -> StageResult:
        return StageResult(cleared=True)

    def link_status(self) -> dict[str, str]:
        return {"google": "linked", "apple": "unlinked", "facebook": "unlinked",
                "game_center": "unserved", "link_code": "linked"}
