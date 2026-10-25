"""Collect every daily reward: login bonus, gifts, missions (memory 05).

The original's auto flow after login is, verbatim: daily login bonus →
gifts → missions ("AUTO-RUNNING GIFT", automissions etc.).
"""
from __future__ import annotations

from dataclasses import dataclass

from dokkan.application.ports import GameGateway


@dataclass(frozen=True)
class DailyReport:
    login_bonus: bool
    gifts: int
    missions: int


@dataclass(frozen=True)
class CollectDaily:
    gateway: GameGateway

    def execute(self) -> DailyReport:
        login_bonus = self.gateway.accept_login_bonus()
        gifts = self.gateway.accept_gifts()
        missions = self.gateway.accept_missions()
        return DailyReport(login_bonus=login_bonus, gifts=gifts, missions=missions)
