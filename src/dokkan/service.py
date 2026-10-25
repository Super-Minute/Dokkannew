"""DokkanService — the operations facade, the one entry point to the bot.

Every capability is a method here taking plain parameters and returning
plain results or display lines. Everything that speaks the game's wire
protocol — hosts, endpoint paths, the envelope, auth handshakes — lives
encapsulated behind this facade in the adapters ring. Extend the bot by
adding service methods and CLI verbs; there should be no need to touch
transport to add an operation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from dokkan.application.errors import ApplicationError
from dokkan.application.ports import GameGateway
from dokkan.application.settings import Settings
from dokkan.application.use_cases.collect_daily import CollectDaily
from dokkan.application.use_cases.farm_stage import FarmStage
from dokkan.application.use_cases.find_stage import FindStage
from dokkan.application.use_cases.list_events import ListEvents
from dokkan.application.use_cases.sell_cards import SellCards
from dokkan.application.use_cases.summon_cards import SummonCards

_LINK_LABELS = {"game_center": "Game Center", "link_code": "Link Code"}


@dataclass(frozen=True)
class DokkanService:
    """All bot operations behind one object (see the module docstring)."""

    gateway: GameGateway
    settings: Settings

    # -- account / connectivity ------------------------------------------------
    def ping(self) -> str:
        probe = getattr(self.gateway, "ping", None)
        if probe is None or not probe():
            raise ApplicationError("game server unreachable")
        return "ok"

    def info(self) -> str:
        account = self.gateway.fetch_account()
        return (f"{account.name}: stamina {account.stamina.current}/"
                f"{account.stamina.maximum}, {len(account.cards)} cards")

    def link_status(self) -> list[str]:
        """ACCOUNT LINK STATUS (memory 02/05) with the original's states."""
        status = self.gateway.link_status()
        lines = []
        for service in ("google", "apple", "facebook", "game_center", "link_code"):
            state = status.get(service, "unserved")
            text = {"linked": "LINKED", "unlinked": "UNLINKED"}.get(
                state, "- NOT AVAILABLE")
            lines.append(f"{_LINK_LABELS.get(service, service.capitalize())}: {text}")
        return lines

    # -- events / farming ------------------------------------------------------
    def events(self) -> list[str]:
        lines = []
        for event in ListEvents(self.gateway).execute():
            lines.append(f"{event.id} {event.name}")
            for stage in event.stages:
                lines.append(f"  {stage.id} {stage.name} "
                             f"({stage.difficulty.name}, {stage.stamina_cost} stamina)")
        return lines

    def farm(self, stage_id: str, runs: int) -> str:
        stage = FindStage(self.gateway).execute(stage_id)
        report = FarmStage(self.gateway, self.settings).execute(stage, runs)
        return (f"{report.runs} runs, {report.cleared} cleared, "
                f"{report.cards_sold} sold ({report.stopped_reason.value})")

    def sell_auto(self) -> str:
        sold = SellCards(self.gateway).execute(self.gateway.fetch_account())
        return f"sold {len(sold)} cards"

    # -- summons ----------------------------------------------------------------
    def summons(self) -> list[str]:
        return [f"{banner.id} {banner.name}" for banner in self.gateway.list_banners()]

    def summon(self, banner_id: int, multi: bool, count: int,
               targets: dict[int, int] | None = None) -> list[str]:
        report = SummonCards(self.gateway).execute(banner_id, multi, count, targets)
        line = f"{report.rounds} rounds, {report.drawn} drawn"
        if targets is not None:
            line += f", targets {'met' if report.targets_met else 'unmet'}"
        lines = [line]
        lines.extend(f"  {card_id}: need {still} more"
                     for card_id, still in report.progress if still)
        return lines

    # -- daily rewards / boards ---------------------------------------------------
    def daily(self) -> str:
        report = CollectDaily(self.gateway).execute()
        return (f"login bonus: {'claimed' if report.login_bonus else 'none left'}, "
                f"{report.gifts} gifts, {report.missions} missions")

    def board(self, board_id: int) -> str:
        result = self.gateway.run_board(board_id)
        return f"board {board_id}: {'cleared' if result.cleared else 'not cleared'}"

    # -- raw passthrough ---------------------------------------------------------
    def raw(self, method: str, path: str, payload: dict | None = None,
            params: dict | None = None) -> dict:
        """Direct passthrough to the transport for anything not wrapped.

        Every endpoint recovered in memories 02/12 is callable through this
        even without a typed method (pacing applies). Raises
        ApplicationError when the gateway has no transport (simulated).
        """
        call = getattr(self.gateway, "request", None)
        if call is None:
            raise ApplicationError(
                "this gateway has no raw transport (simulated gateway?)")
        return call(method, path, payload=payload, params=params)
