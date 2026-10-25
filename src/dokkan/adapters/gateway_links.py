"""Account link status (memory 02/05: ``GET /user/links``).

Recovered from the strings: ``GET /user/links`` reports ``external_links``
per service — google, apple, facebook, game_center, link_code — each
linked / unlinked / unserved (the original displays "LINKED", "UNLINKED",
"- NOT AVAILABLE").

``LinksMixin`` is composed onto
:class:`~dokkan.adapters.http_gateway.HttpGateway` and reuses its
``_call`` transport; it imports no siblings. The per-service value shape
inside ``external_links`` is not recovered — candidate handling below
(documented-unconfirmed).
"""
from __future__ import annotations

from typing import Mapping

_SERVICES = ("google", "apple", "facebook", "game_center", "link_code")


def _state(value) -> str:
    if isinstance(value, dict):
        for key in ("status", "state"):
            if isinstance(value.get(key), str):
                return value[key]
        if isinstance(value.get("linked"), bool):
            return "linked" if value["linked"] else "unlinked"
    if isinstance(value, bool):
        return "linked" if value else "unlinked"
    if isinstance(value, str):
        return value
    return "unserved"


class LinksMixin:
    """GET /user/links (memory 02/05)."""

    def link_status(self) -> Mapping[str, str]:
        payload = self._call("GET", "/user/links")
        raw = payload.get("external_links")
        if not isinstance(raw, dict):
            raw = payload.get("links") if isinstance(payload.get("links"), dict) else {}
        return {service: _state(raw.get(service)) for service in _SERVICES}
