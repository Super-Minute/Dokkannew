from __future__ import annotations

from dataclasses import dataclass

from dokkan.application.ports import GameGateway
from dokkan.domain.models import Event


@dataclass(frozen=True)
class ListEvents:
    gateway: GameGateway

    def execute(self) -> list[Event]:
        return list(self.gateway.list_events())
