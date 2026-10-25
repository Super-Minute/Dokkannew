"""The ``version.json`` client-version cache (memory 08).

Keys are plain strings, addressable per client (``version_code_global`` /
``version_code_japan`` …). Remote payloads arrive as JSON objects or plain
``key=value`` lines. Writes are a locked read-merge-replace so concurrent
terminals cannot clobber each other's keys, and the legacy one-time
``db_version.txt`` import is honoured on first load.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dokkan.application.errors import ApplicationError

_CLIENT_SUFFIX = {"global": "global", "glb": "global", "japan": "japan", "jp": "japan"}
LEGACY_FILE = "db_version.txt"


class VersionFetchError(ApplicationError):
    """The remote version feed could not be fetched or parsed."""


def fetch_version_payload(url: str, timeout: float = 15.0) -> dict[str, str]:
    """GET ``version_code_url`` and parse it (memory 03/08: JSON or key=value)."""
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, method="GET"), timeout=timeout,
        ) as response:
            if response.status != 200:
                raise VersionFetchError(f"{url} returned HTTP {response.status}")
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        exc.close()
        raise VersionFetchError(f"{url} returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, UnicodeDecodeError) as exc:
        raise VersionFetchError(f"cannot fetch {url}: {exc}") from exc
    try:
        return parse_version_payload(text)
    except ValueError as exc:
        raise VersionFetchError(f"{url}: {exc}") from exc


@dataclass(frozen=True)
class ClientVersions:
    version_code: str
    asset_version: str | None
    database_version: str | None


def parse_version_payload(text: str) -> dict[str, str]:
    """Parse a version feed that is either a JSON object or key=value lines."""
    text = text.strip()
    if not text:
        raise ValueError("empty version payload")
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict) and parsed and all(
        isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()
    ):
        return dict(parsed)
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            raise ValueError(f"not a key=value line: {line!r}")
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    if not values:
        raise ValueError("no version values found")
    return values


class VersionStore:
    """Locked, atomic read/merge/replace store for one ``version.json`` file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, str]:
        """Return the cached values, importing the legacy file once if needed."""
        self._import_legacy()
        return self._read()

    def get(self, client: str) -> ClientVersions | None:
        suffix = _CLIENT_SUFFIX.get(client.lower())
        if suffix is None:
            raise ValueError(f"unknown client: {client!r}")
        values = self.load()
        code = values.get(f"version_code_{suffix}")
        if code is None:
            return None
        return ClientVersions(
            code,
            values.get(f"asset_version_{suffix}"),
            values.get(f"database_version_{suffix}"),
        )

    def merge(self, values: Mapping[str, str]) -> None:
        """Merge ``values`` into the cache under an exclusive lock."""
        with self._locked():
            merged = self._read()
            merged.update(values)
            merged["timestamp"] = str(time.time())
            self._write(merged)

    def _read(self) -> dict[str, str]:
        try:
            parsed = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(parsed, dict):
            return {}
        return {k: v for k, v in parsed.items() if isinstance(k, str) and isinstance(v, str)}

    def _write(self, values: Mapping[str, str]) -> None:
        temp = self._path.with_suffix(".json.tmp")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(values, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self._path)

    def _locked(self):
        lock_path = self._path.with_suffix(".json.lock")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "w")
        return _Flock(handle)

    def _import_legacy(self) -> None:
        legacy = self._path.parent / LEGACY_FILE
        if not legacy.exists() or self._path.exists():
            return
        try:
            code = legacy.read_text(encoding="utf-8").strip()
        except OSError:
            return
        if code:
            self.merge({"version_code_global": code})
        try:
            legacy.unlink()
        except OSError:
            pass


class FileVersionSource:
    """VersionSource served from a :class:`VersionStore` file.

    ``code`` re-reads the cache on every access, so a refresh performed by
    another process is picked up by the in-flight retry. With ``url`` set
    (``version_code_url``, memory 03/08), :meth:`refresh` fetches the remote
    feed over HTTP and merges it into the cache under the same lock, so the
    gateway's version-rejection retry picks up fresh codes.
    """

    def __init__(self, store: VersionStore, client: str,
                 url: str | None = None, timeout: float = 15.0) -> None:
        self._store = store
        self._client = client
        self._url = url
        self._timeout = timeout

    @property
    def code(self) -> str | None:
        versions = self._store.get(self._client)
        return versions.version_code if versions else None

    def refresh(self) -> None:
        if self._url is not None:
            self._store.merge(fetch_version_payload(self._url, self._timeout))
        else:
            self._store.load()  # re-read; the cache file is the source of truth

    def learn(self, code: str) -> None:
        """Cache a client version the game server handed us.

        A version-rejection reply carries the authoritative ``client_version``
        (memory 12: "Transient version error, updating from game server...");
        learning it into the cache lets the retry succeed with no remote feed
        configured at all.
        """
        suffix = _CLIENT_SUFFIX.get(self._client.lower(), self._client)
        self._store.merge({f"version_code_{suffix}": code})


class _Flock:
    def __init__(self, handle) -> None:
        self._handle = handle

    def __enter__(self):
        fcntl.flock(self._handle, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc) -> None:
        fcntl.flock(self._handle, fcntl.LOCK_UN)
        self._handle.close()
