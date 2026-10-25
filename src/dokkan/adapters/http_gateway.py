"""A GameGateway that talks to the real game API (memories 02, 06, 08).

Transport is stdlib urllib. Requests carry the account's identity and the
current client version inside the encrypted envelope; responses are JSON
with plaintext fields or a ``{"sign": "<b64>"}`` body, unwrapped with the
response-direction secrets. A ``new_client_version_exists`` reply forces one
version refresh and retry.

Hardening applied on top of the recovered spec:

* 4xx/5xx bodies are decoded so a version-gate rejection delivered with a
  non-2xx status still reaches the retry loop, and so error payloads surface
  as structured messages rather than a bare HTTP code.
* ``new_client_version_exists`` short-circuits before any other status signal.
* Transient transport failures (URLError / OSError / 5xx) are retried for
  idempotent calls only; ``/start``, ``/finish``, ``/cards/sell`` and
  ``/user/recover_act_*`` opt out because a mid-flight timeout cannot be
  safely replayed.
* Mapping helpers wrap KeyError/ValueError/TypeError in ``GatewayError`` with
  the offending record shape attached.
* ``payload`` may not override identity fields; GET requests may not carry a
  payload.
* Boolean-valued response fields are parsed through ``_as_bool`` so the string
  ``"false"`` is not silently truthy.

Wire details the recovered spec leaves unconfirmed are collected here as
documented constants: request payload field names, and the candidate keys
used when mapping responses onto domain models.

Note on the wire: ``/resources/login`` is GET and the sealed envelope
(including ``identifier``/``access_token``/``secret``) is placed in the query
string, matching the target. This exposes identity to any intermediary that
logs URLs; the behaviour is preserved here because it is spec-verified, but
should not be treated as a template for new endpoints.
"""
from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol, Sequence

from dokkan.adapters.envelope import Envelope, envelope_secrets
from dokkan.adapters.gateway_boards import BoardsMixin
from dokkan.adapters.gateway_cards import CardsMixin
from dokkan.adapters.gateway_daily import DailyMixin
from dokkan.adapters.gateway_links import LinksMixin
from dokkan.adapters.gateway_misc import MiscMixin
from dokkan.adapters.gateway_shops import ShopsMixin
from dokkan.adapters.gateway_social import SocialBattleMixin
from dokkan.adapters.gateway_summon import SummonMixin
from dokkan.adapters.save_store import SaveRecord
from dokkan.application.errors import ApplicationError, RefillUnavailable
from dokkan.application.ports import RefillSource, StageResult
from dokkan.domain.models import (
    Account, Card, CardType, Difficulty, Event, Rarity, Stage, Stamina, Team,
)

logger = logging.getLogger(__name__)

DEFAULT_HOSTS = {
    "global": "https://ishin-global.aktsk.com",
    "japan": "https://ishin-production.aktsk.jp",
}
SELL_BATCH_SIZE = 99  # verified batches of 99 (memory 02/09)

# Identity fields the gateway always supplies; callers may not override them.
_IDENTITY_KEYS = frozenset({"identifier", "access_token", "secret", "user_id"})

# Candidate keys for mapping server responses (field names unconfirmed in the
# recovered spec; first present key wins).
_USER_KEYS = ("user",)
_NAME_KEYS = ("name", "account_name", "user_name")
_STAMINA_KEYS = ("act", "stamina")
_STAMINA_MAX_KEYS = ("act_max", "max_act", "stamina_max")
_STAMINA_ALL_KEYS = _STAMINA_KEYS + _STAMINA_MAX_KEYS
_CAPACITY_KEYS = ("card_capacity", "capacity")
_CARDS_KEYS = ("cards",)
_CARD_ID_KEYS = ("id", "card_id", "uid")
_CARD_NAME_KEYS = ("name", "card_name")
_CARD_RARITY_KEYS = ("rarity", "base_rarity")
_CARD_TYPE_KEYS = ("type", "element", "attribute")
_CARD_LEVEL_KEYS = ("level", "lv")
_CARD_ATTACK_KEYS = ("attack", "atk")
_CARD_LOCKED_KEYS = ("locked", "lock")
_CARD_FAVORITE_KEYS = ("favorite", "favo")
_EVENTS_KEYS = ("events",)
_EVENT_ID_KEYS = ("id", "event_id")
_EVENT_NAME_KEYS = ("name", "event_name")
_STAGE_KEYS = ("stages",)
_STAGE_ID_KEYS = ("id", "stage_id")
_STAGE_NAME_KEYS = ("name", "stage_name")
_STAGE_DIFFICULTY_KEYS = ("difficulty",)
_STAGE_STAMINA_KEYS = ("stamina_cost", "stamina", "act")


class VersionSource(Protocol):
    """Current client version code plus a refresh on server rejection."""

    @property
    def code(self) -> str: ...

    def refresh(self) -> None: ...

    def learn(self, code: str) -> None:
        """Cache a client version the server handed us (optional)."""
        ...


class GatewayError(ApplicationError):
    """The game API could not be reached or returned an unusable reply."""


class TransientTransportError(GatewayError):
    """A network-level failure that may succeed on retry."""


def _first(source: dict, keys: tuple[str, ...], default=None):
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return default


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y", "on")
    raise GatewayError(
        f"cannot interpret {type(value).__name__} {value!r} as boolean")


def _rarity(value) -> Rarity:
    if isinstance(value, int):
        return Rarity(value)
    return Rarity[str(value).upper()]


def _card(raw: dict) -> Card:
    return Card(
        id=int(_first(raw, _CARD_ID_KEYS)),
        name=str(_first(raw, _CARD_NAME_KEYS, "")),
        rarity=_rarity(_first(raw, _CARD_RARITY_KEYS, 0)),
        type=CardType[str(_first(raw, _CARD_TYPE_KEYS, "STR")).upper()],
        level=int(_first(raw, _CARD_LEVEL_KEYS, 1)),
        locked=bool(_first(raw, _CARD_LOCKED_KEYS, False)),
        favorite=bool(_first(raw, _CARD_FAVORITE_KEYS, False)),
        attack=int(_first(raw, _CARD_ATTACK_KEYS, 0)),
    )


def _stage(raw: dict) -> Stage:
    return Stage(
        id=str(_first(raw, _STAGE_ID_KEYS, "")),
        name=str(_first(raw, _STAGE_NAME_KEYS, "")),
        difficulty=Difficulty(int(_first(raw, _STAGE_DIFFICULTY_KEYS, 0))),
        stamina_cost=int(_first(raw, _STAGE_STAMINA_KEYS, 0)),
    )


def _stamina(user: dict) -> Stamina:
    current = int(_first(user, _STAMINA_KEYS, 0))
    return Stamina(current, int(_first(user, _STAMINA_MAX_KEYS, current)))


def _safe_map(fn, raw, *, what: str):
    try:
        return fn(raw)
    except (KeyError, ValueError, TypeError) as exc:
        fields = sorted(raw) if isinstance(raw, dict) else type(raw).__name__
        raise GatewayError(
            f"cannot map {what}: {exc} (fields={fields})") from exc


def _ensure_ok(payload: dict, what: str) -> None:
    if payload.get("error"):
        raise GatewayError(
            f"{what} failed: {payload.get('message') or payload.get('error')}")
    success = payload.get("success")
    if success is not None and not _as_bool(success):
        raise GatewayError(f"{what} failed: success={success!r}")


class HttpGateway(SummonMixin, DailyMixin, BoardsMixin, LinksMixin,
                 CardsMixin, ShopsMixin, SocialBattleMixin, MiscMixin):
    """GameGateway implementation over the recovered HTTP envelope.

    Feature groups (summon, daily rewards) live in their own mixin modules
    and are composed here; they share this class's ``_call`` transport. Mixins
    must not redefine ``_call`` or ``_read`` — the invariant is asserted in
    the test suite.
    """

    def __init__(self, save: SaveRecord, versions: VersionSource | None = None,
                 base_url: str | None = None, timeout: float = 30.0, *,
                 sleep=time.sleep, network_retries: int = 2,
                 network_backoff: float = 0.5) -> None:
        if base_url is None and save.client not in DEFAULT_HOSTS:
            raise GatewayError(
                f"unknown client {save.client!r}; "
                f"known: {sorted(DEFAULT_HOSTS)}")
        self._save = save
        self._versions = versions
        self._base = (base_url or DEFAULT_HOSTS[save.client]).rstrip("/")
        self._timeout = timeout
        self._sleep = sleep
        self._network_retries = max(0, network_retries)
        self._network_backoff = max(0.0, network_backoff)
        self._out = Envelope(*envelope_secrets(save.client, "request"))
        self._in = Envelope(*envelope_secrets(save.client, "response"))

    # -- transport ----------------------------------------------------------
    def ping(self) -> bool:
        """GET /ping connectivity probe (memory 02)."""
        try:
            with urllib.request.urlopen(
                urllib.request.Request(f"{self._base}/ping", method="GET"),
                timeout=self._timeout,
            ) as response:
                return response.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def _build_request(self, method: str, path: str,
                       payload: dict | None, params: dict | None):
        collisions = _IDENTITY_KEYS & set(payload or ())
        if collisions:
            raise GatewayError(
                f"payload cannot override identity fields: {sorted(collisions)}")
        body = dict(payload or {})
        body.update(self._identity())
        data, sign = self._out.seal(body)
        fields = {"data": data, "sign": sign}
        if method == "GET":
            if payload:
                raise GatewayError("GET requests cannot carry a payload")
            # NOTE: the sealed envelope is placed in the query string to match
            # the target; see the module docstring for the exposure caveat.
            query = urllib.parse.urlencode(
                {**dict(params or {}), **fields}, doseq=True)
            return urllib.request.Request(f"{self._base}{path}?{query}",
                                          method="GET")
        query = urllib.parse.urlencode(params or {}, doseq=True)
        url = f"{self._base}{path}" + (f"?{query}" if query else "")
        return urllib.request.Request(
            url,
            data=urllib.parse.urlencode(fields).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

    def _call(self, method: str, path: str, payload: dict | None = None,
              params: dict | None = None, *,
              idempotent: bool = True) -> dict:
        version_retried = False
        network_attempts = 0
        while True:
            request = self._build_request(method, path, payload, params)
            try:
                response_payload = self._read(request)
            except TransientTransportError as exc:
                if not idempotent or network_attempts >= self._network_retries:
                    raise GatewayError(str(exc)) from exc
                network_attempts += 1
                backoff = self._network_backoff * (2 ** (network_attempts - 1))
                backoff += random.uniform(0, self._network_backoff)
                logger.warning(
                    "transient transport error (attempt %d/%d), retrying in "
                    "%.2fs: %s", network_attempts, self._network_retries,
                    backoff, exc)
                self._sleep(backoff)
                continue

            if not response_payload.get("new_client_version_exists"):
                return response_payload

            if version_retried or self._versions is None:
                raise GatewayError(
                    f"server demands a newer client version: "
                    f"{response_payload.get('client_version')!r}")
            version_retried = True
            # "updating from game server" (memory 12): a rejection that carries
            # the authoritative client_version teaches it to the cache, so the
            # retry works even with no remote feed configured.
            server_code = response_payload.get("client_version")
            if isinstance(server_code, str) and server_code:
                learner = getattr(self._versions, "learn", None)
                if callable(learner):
                    learner(server_code)
                else:
                    logger.warning(
                        "VersionSource lacks learn(); dropping server version "
                        "%r", server_code)
            logger.info("refreshing client version after server rejection")
            self._versions.refresh()

    def request(self, method: str, path: str, payload: dict | None = None,
                params: dict | None = None, *,
                idempotent: bool = True) -> dict:
        """Generic passthrough — the escape hatch for endpoints without a
        typed wrapper. Mark ``idempotent=False`` for non-safe verbs so
        transient transport errors are not replayed."""
        return self._call(method.upper(), path, payload=payload,
                          params=params, idempotent=idempotent)

    def _read(self, request) -> dict:
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read() if hasattr(exc, "read") else b""
            # A version-gate rejection can arrive with a 4xx/5xx status; feed
            # it back to _call so the retry loop sees new_client_version_exists.
            inspected = self._try_decode_body(body)
            if isinstance(inspected, dict) and inspected.get("new_client_version_exists"):
                return inspected
            detail = self._body_snippet(body)
            message = f"{request.full_url} returned HTTP {exc.code}: {detail}"
            if exc.code >= 500:
                raise TransientTransportError(message) from exc
            raise GatewayError(message) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise TransientTransportError(
                f"cannot reach {self._base}: {exc}") from exc
        return self._decode_payload(raw, request.full_url)

    def _decode_payload(self, raw: bytes, url: str) -> dict:
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise GatewayError(f"non-JSON response from {url}") from exc
        if not isinstance(parsed, dict):
            raise GatewayError(f"unexpected response shape from {url}")
        sign = parsed.get("sign")
        if not isinstance(sign, str):
            return parsed
        try:
            decrypted = self._in.open(sign)
        except ValueError as exc:
            raise GatewayError(
                f"cannot decrypt response from {url}: {exc}") from exc
        if not isinstance(decrypted, dict):
            raise GatewayError(
                f"decrypted response from {url} is not an object")
        # Plaintext-first (memory 04/06): sibling fields win over the
        # decrypted envelope on key conflicts.
        merged = dict(decrypted)
        merged.update({k: v for k, v in parsed.items() if k != "sign"})
        return merged

    def _try_decode_body(self, raw: bytes) -> dict | None:
        """Best-effort decode of an error body; used only for inspection."""
        if not raw:
            return None
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(parsed, dict):
            return None
        sign = parsed.get("sign")
        if isinstance(sign, str):
            try:
                decrypted = self._in.open(sign)
            except ValueError:
                return None
            if not isinstance(decrypted, dict):
                return None
            merged = dict(decrypted)
            merged.update({k: v for k, v in parsed.items() if k != "sign"})
            return merged
        return parsed

    @staticmethod
    def _body_snippet(raw: bytes, limit: int = 200) -> str:
        if not raw:
            return "<empty body>"
        text = raw.decode("utf-8", errors="replace")
        return text[:limit] + ("..." if len(text) > limit else "")

    def _identity(self) -> dict:
        payload = {
            "identifier": self._save.identifier,
            "access_token": self._save.access_token,
            "secret": self._save.secret,
        }
        if self._save.user_id is not None:
            payload["user_id"] = self._save.user_id
        if self._versions is not None:
            code = self._versions.code
            if code:
                payload["client_version"] = code
        return payload

    # -- GameGateway ---------------------------------------------------------
    def fetch_account(self) -> Account:
        # The verbatim /resources/login flag combo recovered from the target
        # (memory 12): one call returning user + cards (+ missions, unused here).
        payload = self._call("GET", "/resources/login", params={
            "act_items": "true", "support_items": "true", "treasure_items": "true",
            "cards": "true", "user": "true", "missions": "true",
        })
        user = _first(payload, _USER_KEYS)
        if not isinstance(user, dict):
            logger.debug("/resources/login lacked a 'user' object; "
                         "using whole payload")
            user = payload
        raw_cards = _first(payload, _CARDS_KEYS)
        if raw_cards is None:
            logger.warning("/resources/login response missing cards; fields=%s",
                           sorted(payload))
            raw_cards = []
        if not isinstance(raw_cards, list):
            raise GatewayError(
                f"/resources/login 'cards' is {type(raw_cards).__name__}, "
                "not a list")
        capacity = _first(user, _CAPACITY_KEYS)
        try:
            capacity_payload = self._call("GET", "/user/capacity/card")
            capacity = _first(capacity_payload, _CAPACITY_KEYS, capacity)
        except GatewayError as exc:
            logger.debug("capacity endpoint unavailable; using login value: %s",
                         exc)
        return Account(
            name=str(_first(user, _NAME_KEYS, self._save.identifier)),
            stamina=_stamina(user),
            cards=tuple(_safe_map(_card, raw, what="card") for raw in raw_cards),
            card_capacity=capacity,
        )

    def list_events(self) -> Sequence[Event]:
        payload = self._call("GET", "/events")
        raw_events = _first(payload, _EVENTS_KEYS, [])
        if not isinstance(raw_events, list):
            raise GatewayError(
                f"/events 'events' is {type(raw_events).__name__}, not a list")
        events = []
        for raw in raw_events:
            if not isinstance(raw, dict):
                raise GatewayError(
                    f"/events entry is {type(raw).__name__}, not an object")
            raw_stages = _first(raw, _STAGE_KEYS, [])
            stages = tuple(_safe_map(_stage, s, what="stage") for s in raw_stages)
            event_id = _first(raw, _EVENT_ID_KEYS)
            if event_id is None:
                logger.warning("event missing id field; fields=%s", sorted(raw))
                event_id = 0
            events.append(Event(
                int(event_id),
                str(_first(raw, _EVENT_NAME_KEYS, "")),
                stages,
            ))
        return events

    def run_stage(self, stage: Stage, team: Team) -> StageResult:
        # /stages/{id}/briefing?difficulty=&force_update=&team_num= (memory 12)
        briefing = self._call("GET", f"/stages/{stage.id}/briefing", params={
            "difficulty": int(stage.difficulty),
            "force_update": "true",
            "team_num": len(team.card_ids),
        })
        _ensure_ok(briefing, "stage briefing")
        started_at = time.time()
        start_reply = self._call(
            "POST", f"/stages/{stage.id}/start",
            payload={"card_ids": list(team.card_ids)},
            idempotent=False,
        )
        _ensure_ok(start_reply, "stage start")
        finished_at = time.time()
        finished = self._call(
            "POST", f"/stages/{stage.id}/finish",
            payload={
                "elapsed_time": int(finished_at - started_at),
                "start_time": int(started_at),
                "finish_time": int(finished_at),
                "stones": 0,
            },
            idempotent=False,
        )
        _ensure_ok(finished, "stage finish")
        # is_cleared is the battle-state field recovered verbatim (memory 12).
        if "is_cleared" in finished:
            return StageResult(cleared=_as_bool(finished["is_cleared"]))
        if "success" in finished:
            return StageResult(cleared=_as_bool(finished["success"]))
        raise GatewayError(
            "finish response has neither 'is_cleared' nor 'success'; "
            f"fields={sorted(finished)}")

    def sell_cards(self, card_ids: Sequence[int]) -> None:
        ids = list(card_ids)
        for offset in range(0, len(ids), SELL_BATCH_SIZE):
            batch = ids[offset:offset + SELL_BATCH_SIZE]
            payload = self._call(
                "POST", "/cards/sell", payload={"cards": batch},
                idempotent=False,
            )
            # Abort on the first failure so a broken account does not keep
            # selling subsequent batches.
            _ensure_ok(payload, f"sell batch at offset {offset}")

    def refill_stamina(self, source: RefillSource) -> Stamina:
        path = ("/user/recover_act_with_items" if source is RefillSource.MEAT
                else "/user/recover_act_with_stone")
        payload = self._call("POST", path, idempotent=False)
        if payload.get("error"):
            raise RefillUnavailable(source.value)
        user = _first(payload, _USER_KEYS, payload)
        if not isinstance(user, dict) or not any(
                k in user for k in _STAMINA_ALL_KEYS):
            raise GatewayError(
                "refill response has no stamina fields; "
                f"keys={sorted(user) if isinstance(user, dict) else type(user).__name__}")
        return _stamina(user)