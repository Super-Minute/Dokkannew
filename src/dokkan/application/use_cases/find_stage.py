from __future__ import annotations

from dataclasses import dataclass

from dokkan.application.errors import ApplicationError
from dokkan.application.ports import GameGateway
from dokkan.domain.models import Stage


class StageNotFound(ApplicationError):
    pass


@dataclass(frozen=True)
class FindStage:
    gateway: GameGateway

    def execute(self, stage_id: str) -> Stage:
        for event in self.gateway.list_events():
            for stage in event.stages:
                if stage.id == stage_id:
                    return stage
        raise StageNotFound(stage_id)
