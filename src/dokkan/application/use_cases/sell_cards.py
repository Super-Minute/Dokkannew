from __future__ import annotations

from dataclasses import dataclass
from typing import Collection

from dokkan.application.ports import GameGateway
from dokkan.domain.models import Account
from dokkan.domain.policies import cards_to_sell


@dataclass(frozen=True)
class SellCards:
    gateway: GameGateway

    def execute(self, account: Account, keep: Collection[int] = ()) -> list[int]:
        """Sell every sellable card except those in `keep`; returns the ids sold."""
        ids = [c.id for c in cards_to_sell(account.cards) if c.id not in keep]
        if ids:
            self.gateway.sell_cards(ids)
        return ids
