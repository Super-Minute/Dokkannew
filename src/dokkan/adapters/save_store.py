"""Loading a single-account save record (memory 01 field list).

A save is what the signin flow produced in the target: ``identifier``,
``access_token``, ``secret``, ``user_id`` and the ``client`` it belongs to,
plus whatever extra per-account state travelled with it. Only the fields the
envelope and gateway need are typed; everything else is preserved verbatim.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dokkan.application.errors import CollectionError

_CLIENTS = {"glb": "global", "global": "global", "japan": "japan", "jp": "japan"}
REQUIRED = ("identifier", "access_token", "secret")


@dataclass(frozen=True)
class SaveRecord:
    identifier: str
    access_token: str
    secret: str
    client: str  # normalised: "global" | "japan"
    user_id: int | None = None
    extras: dict[str, Any] = field(default_factory=dict)


def load_save(path: Path) -> SaveRecord:
    """Read one save file; raise CollectionError when it is unusable."""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CollectionError(f"cannot read save file {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise CollectionError(f"save file {path} is not a JSON object")
    missing = [key for key in REQUIRED if not isinstance(parsed.get(key), str)]
    if missing:
        raise CollectionError(f"save file {path} is missing: {', '.join(missing)}")
    raw_client = parsed.get("client", "global")
    client = _CLIENTS.get(str(raw_client).lower())
    if client is None:
        raise CollectionError(f"save file {path} has unknown client: {raw_client!r}")
    known = {*REQUIRED, "client", "user_id"}
    user_id = parsed.get("user_id")
    if user_id is not None and not isinstance(user_id, int):
        raise CollectionError(f"save file {path} has a non-integer user_id")
    return SaveRecord(
        identifier=parsed["identifier"],
        access_token=parsed["access_token"],
        secret=parsed["secret"],
        client=client,
        user_id=user_id,
        extras={k: v for k, v in parsed.items() if k not in known},
    )


def write_save(path: Path, save: SaveRecord) -> None:
    """Persist a save record; round-trips through :func:`load_save`.

    The typed fields win over any stray extras (``load_save`` keeps the
    extras disjoint from them anyway).
    """
    record = dict(save.extras)
    record.update({
        "identifier": save.identifier,
        "access_token": save.access_token,
        "secret": save.secret,
        "client": save.client,
    })
    if save.user_id is not None:
        record["user_id"] = save.user_id
    try:
        path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    except OSError as exc:
        raise CollectionError(f"cannot write save file {path}: {exc}") from exc
