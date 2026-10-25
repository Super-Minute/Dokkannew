from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class StaminaSettings:
    prefer_meat: bool = True
    use_stone: bool = True


@dataclass(frozen=True)
class DelayRange:
    """A randomized delay window in seconds (bot_config min/max pairs)."""

    min: float = 20.0
    max: float = 21.0


def _default_client_delays() -> dict[str, DelayRange]:
    # The original bot's shipped defaults: 20–21 s per client (memory 10).
    return {"global": DelayRange(20.0, 21.0), "japan": DelayRange(20.0, 21.0)}


@dataclass(frozen=True)
class Settings:
    stamina: StaminaSettings = field(default_factory=StaminaSettings)
    auto_sell: bool = True
    team_build: bool = True
    client_delays: Mapping[str, DelayRange] = field(default_factory=_default_client_delays)
