"""Daily reward collection for the game API (memories 02, 05, 12).

Recovered endpoints and response field names (verbatim from the target's
strings): ``GET /gifts`` (response carries its data under a ``gifts`` key)
with ``POST /gifts/accept``; ``GET /missions`` (``missions`` key,
``mission_ids``) with ``POST /missions/accept``; ``POST /login_bonuses/accept``
and the ``random_login_bonuses`` polling flag on ``/resources/home``. The
original's auto flow after login is: daily login bonus → gifts → missions.

``DailyMixin`` is composed onto
:class:`~dokkan.adapters.http_gateway.HttpGateway` and reuses its transport
(``_call``, which raises :class:`~dokkan.adapters.http_gateway.GatewayError`,
an :class:`~dokkan.application.errors.ApplicationError`); sibling modules are
not imported, so the candidate-key helpers are replicated here.

Wire details the recovered spec leaves unconfirmed are collected as
documented constants below (candidate keys; first present key wins):
- the per-item id key in list responses and the matching accept payload key;
- the random-login-bonus id keys and the whole random-bonus flow.
"""
from __future__ import annotations

from dokkan.application.errors import ApplicationError

# Candidate keys for mapping server responses (field names unconfirmed in the
# recovered spec; first present key wins — same pattern as http_gateway).
_GIFTS_KEYS = ("gifts",)
_GIFT_ID_KEYS = ("id", "gift_id")
_MISSIONS_KEYS = ("missions",)
_MISSION_ID_KEYS = ("id", "mission_id")
_RANDOM_BONUS_ID_KEYS = ("random_login_bonus_id", "id")

# The verbatim /resources/home flag combo recovered from the target
# (all_strings.txt line 282128): everything false except random_login_bonuses.
_RANDOM_BONUS_HOME_FLAGS = {
    "apologies": "false", "banners": "false", "bonus_schedules": "false",
    "budokai": "false", "dragonball_sets": "false", "gifts": "false",
    "login_bonuses": "false", "random_login_bonuses": "true",
    "rmbattles": "false", "user": "false", "user_subscription": "false",
}


def _first(source: dict, keys: tuple[str, ...], default=None):
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return default


def _id_and_key(source: dict, keys: tuple[str, ...]):
    """Return ``(key, value)`` for the first candidate id key present."""
    for key in keys:
        if key in source and source[key] is not None:
            return key, source[key]
    return None


class DailyMixin:
    """Gifts, missions and login bonuses (memory 02/12)."""

    def accept_gifts(self) -> int:
        """GET /gifts then POST /gifts/accept per gift; returns how many stuck.

        A transport failure on one accept (GatewayError, an ApplicationError)
        is caught and the remaining gifts are still attempted, so one bad gift
        cannot lose the count of the others — the original's locals
        (``gift_ids``, "Gifts Accepted...") accept per-id, and stopping at the
        first failure would drop already-accepted gifts from the count.
        """
        payload = self._call("GET", "/gifts")
        accepted = 0
        for raw in _first(payload, _GIFTS_KEYS, []) or []:
            if not isinstance(raw, dict):
                continue  # tolerate malformed rows, as the original does
            found = _id_and_key(raw, _GIFT_ID_KEYS)
            if found is None:
                continue
            key, gift_id = found
            try:
                self._call("POST", "/gifts/accept", payload={key: gift_id},
                           idempotent=False)
            except ApplicationError:
                continue
            accepted += 1
        return accepted

    def accept_missions(self) -> int:
        """GET /missions then POST /missions/accept per mission; same per-id
        catch-and-continue policy as :meth:`accept_gifts` (the original's
        ``mission_ids`` local drives one accept call per mission)."""
        payload = self._call("GET", "/missions")
        accepted = 0
        for raw in _first(payload, _MISSIONS_KEYS, []) or []:
            if not isinstance(raw, dict):
                continue
            found = _id_and_key(raw, _MISSION_ID_KEYS)
            if found is None:
                continue
            key, mission_id = found
            try:
                self._call("POST", "/missions/accept", payload={key: mission_id},
                           idempotent=False)
            except ApplicationError:
                continue
            accepted += 1
        return accepted

    def accept_login_bonus(self) -> bool:
        """POST /login_bonuses/accept; True on success, False on an in-body
        error (e.g. already claimed). The random login bonus is attempted
        afterwards, best-effort, and can never fail this call."""
        payload = self._call("POST", "/login_bonuses/accept")
        success = not payload.get("error")
        self._accept_random_login_bonus(payload)
        return bool(success)

    def _accept_random_login_bonus(self, accept_payload: dict) -> None:
        """Documented-unconfirmed best-effort random login bonus (memory 12).

        The original polls ``/resources/home?...&random_login_bonuses=true``
        and keeps ``random_login_bonus``/``random_login_bonus_id`` locals with
        a ``/random_login_bonuses/`` path; the exact trigger and payload are
        unconfirmed, so every failure here is swallowed — a random-bonus
        problem must never fail the plain login bonus.
        """
        try:
            bonus_id = _first(accept_payload, _RANDOM_BONUS_ID_KEYS)
            if bonus_id is None:
                home = self._call("GET", "/resources/home",
                                  params=_RANDOM_BONUS_HOME_FLAGS)
                bonus_id = _first(home, _RANDOM_BONUS_ID_KEYS)
            if bonus_id is None:
                return
            self._call("POST", f"/random_login_bonuses/{bonus_id}/accept")
        except ApplicationError:
            pass
