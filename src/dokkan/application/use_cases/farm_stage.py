from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from dokkan.application.errors import InvalidRuns, NoTeamAvailable, RefillUnavailable
from dokkan.application.ports import GameGateway, RefillSource
from dokkan.application.settings import Settings
from dokkan.application.use_cases.sell_cards import SellCards
from dokkan.domain.models import Account, Stage, Stamina, Team
from dokkan.domain.policies import build_team


class StopReason(str, Enum):
    COMPLETED = "completed"
    OUT_OF_STAMINA = "out of stamina"
    STAGE_FAILED = "stage not cleared"


@dataclass(frozen=True)
class FarmReport:
    runs: int
    cleared: int
    cards_sold: int
    stopped_reason: StopReason


@dataclass(frozen=True)
class FarmStage:
    gateway: GameGateway
    settings: Settings

    def execute(self, stage: Stage, runs: int, team: Team | None = None) -> FarmReport:
        if runs < 0:
            raise InvalidRuns(f"runs must be >= 0, got {runs}")
        done = cleared = sold = 0
        reason = StopReason.COMPLETED
        account = self.gateway.fetch_account()
        for _ in range(runs):
            run_team = self._team_for(account, stage, team)  # fail before spending anything
            if not self._ensure_stamina(account.stamina, stage):
                reason = StopReason.OUT_OF_STAMINA
                break
            if self.settings.auto_sell and account.box_is_full:
                sold += len(SellCards(self.gateway).execute(account, keep=run_team.card_ids))
            result = self.gateway.run_stage(stage, run_team)
            done += 1
            cleared += result.cleared
            if not result.cleared:
                reason = StopReason.STAGE_FAILED
                break
            account = self.gateway.fetch_account()
        return FarmReport(done, cleared, sold, reason)

    def _team_for(self, account: Account, stage: Stage, team: Team | None) -> Team:
        if team is not None:
            return team
        if not self.settings.team_build:
            raise NoTeamAvailable("no team given and team_build is disabled")
        return build_team(account.cards, stage)

    def _ensure_stamina(self, stamina: Stamina, stage: Stage) -> bool:
        """Refill from the preferred sources until the stage is affordable."""
        for source in self._refill_order():
            while not stamina.can_afford(stage.stamina_cost):
                try:
                    refilled = self.gateway.refill_stamina(source)
                except RefillUnavailable:
                    break
                if refilled.current <= stamina.current:
                    break  # a refill that restores nothing would loop forever
                stamina = refilled
        return stamina.can_afford(stage.stamina_cost)

    def _refill_order(self) -> list[RefillSource]:
        meat_first = [RefillSource.MEAT, RefillSource.STONE]
        order = meat_first if self.settings.stamina.prefer_meat else meat_first[::-1]
        return [s for s in order if s is not RefillSource.STONE or self.settings.stamina.use_stone]
