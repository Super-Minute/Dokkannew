from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dokkan.application.errors import ConfigError
from dokkan.application.settings import (
    DelayRange, Settings, StaminaSettings, _default_client_delays,
)

_CLIENTS = {"global": "global", "glb": "global", "japan": "japan", "jp": "japan"}


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"'{key}' must be an object")
    return value


def _flag(section: dict[str, Any], key: str, default: bool, where: str) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"'{where}.{key}' must be true or false")
    return value


def _delay_range(section: dict[str, Any], where: str) -> DelayRange:
    try:
        lo, hi = float(section["min"]), float(section["max"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"'{where}' must have numeric min and max") from exc
    if not lo <= hi:
        raise ConfigError(f"'{where}.min must be <= max")
    return DelayRange(lo, hi)


def _client_delays(raw: dict[str, Any]) -> dict[str, DelayRange]:
    delays = _default_client_delays()
    section = _section(raw, "client_delays")
    for key, value in section.items():
        client = _CLIENTS.get(str(key).lower())
        if client is None:
            raise ConfigError(f"unknown client in 'client_delays': {key!r}")
        if not isinstance(value, dict):
            raise ConfigError(f"'client_delays.{key}' must be an object")
        delays[client] = _delay_range(value, f"client_delays.{key}")
    return delays


def load_settings(path: Path) -> Settings:
    """Read the recognised keys of a JSON config; a missing file yields defaults."""
    if not path.exists():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config root must be an object")

    defaults = Settings()
    stamina = _section(raw, "stamina")
    return Settings(
        stamina=StaminaSettings(
            prefer_meat=_flag(stamina, "prefer_meat", defaults.stamina.prefer_meat, "stamina"),
            use_stone=_flag(stamina, "use_stone", defaults.stamina.use_stone, "stamina"),
        ),
        auto_sell=_flag(_section(raw, "auto_sell"), "enabled", defaults.auto_sell, "auto_sell"),
        team_build=_flag(_section(raw, "game"), "team_build", defaults.team_build, "game"),
        client_delays=_client_delays(raw),
    )
