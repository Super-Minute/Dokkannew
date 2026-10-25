from __future__ import annotations

from dokkan.application.errors import RefillUnavailable
from dokkan.application.ports import RefillSource, StageResult
from dokkan.domain.models import Account, Card, Event, Stage, Stamina, Team


class FakeGateway:
    """Test double implementing GameGateway with scripted state and call recording."""

    def __init__(self, stamina=50, cards=(), capacity=None, refills=None, maximum=100, clears=True):
        self.stamina, self.maximum = stamina, maximum
        self.cards, self.capacity = list(cards), capacity
        self.clears = clears
        self.refills = dict(refills or {})  # source -> amount restored; absent = unavailable
        self.runs: list[tuple[Stage, Team]] = []
        self.sold: list[int] = []
        self.refill_calls: list[RefillSource] = []

    def fetch_account(self) -> Account:
        return Account("t", Stamina(self.stamina, self.maximum), tuple(self.cards), self.capacity)

    def list_events(self) -> list[Event]:
        return [Event(1, "e")]

    def run_stage(self, stage: Stage, team: Team) -> StageResult:
        assert self.stamina >= stage.stamina_cost, "ran stage without enough stamina"
        self.stamina -= stage.stamina_cost
        self.runs.append((stage, team))
        return StageResult(cleared=self.clears)

    def sell_cards(self, card_ids) -> None:
        self.sold.extend(card_ids)
        self.cards = [c for c in self.cards if c.id not in set(card_ids)]

    def refill_stamina(self, source: RefillSource) -> Stamina:
        self.refill_calls.append(source)
        if source not in self.refills:
            raise RefillUnavailable(source.value)
        self.stamina += self.refills.pop(source)
        return Stamina(self.stamina, self.maximum)
