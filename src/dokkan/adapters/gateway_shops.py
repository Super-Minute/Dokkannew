"""Shops and summon extras: Baba, treasure/zeni/exchange-point shops,
dragonball sets, wishes, sd packs (memories 02, 12).

Recovered endpoints (verbatim from the target's strings): ``GET /baba``,
``POST /shops/{shop}/items/{item_id}/buy`` (baba buy), ``/shops/{shop}/items``
listings plus ``/shops/treasure/items/convert``, ``GET /dragonball_sets``
and ``/dragonball_sets/{id}``, ``POST /wishes`` (Shenron), ``GET /sd/packs``
and ``GET /gashas/{id}`` (single banner detail).

``ShopsMixin`` is composed onto
:class:`~dokkan.adapters.http_gateway.HttpGateway` and reuses its transport
helpers (``_call``); it imports no siblings. Every method returns the raw
response dict: the recovered spec gives the paths, not the response shapes,
so mapping onto domain models is left to the callers.

Wire details the recovered spec leaves unconfirmed are documented at each
method (documented-unconfirmed, minimal payloads).
"""
from __future__ import annotations


class ShopsMixin:
    """Baba, per-shop items, treasure convert, dragonball sets, wishes,
    sd packs and single-banner detail (memory 02/12)."""

    def baba(self) -> dict:
        """GET /baba — the Baba shop listing. The response shape is
        documented-unconfirmed; the raw dict is returned untouched."""
        return self._call("GET", "/baba")

    def buy_item(self, shop: str, item_id: int, quantity: int = 1) -> dict:
        """POST /shops/{shop}/items/{item_id}/buy — buy from any shop.

        Known shops are ``exchange_point``, ``treasure``, ``zeni`` and
        ``baba`` (memory 02), but the name is interpolated verbatim and
        never validated client-side. The buy payload is minimal: the
        quantity rides under ``quantity`` (documented-unconfirmed field
        name; the recovered spec gives only the path template).
        """
        return self._call("POST", f"/shops/{shop}/items/{item_id}/buy",
                          payload={"quantity": quantity})

    def list_shop_items(self, shop: str) -> dict:
        """GET /shops/{shop}/items — one shop's item listing. The response
        shape is documented-unconfirmed; the raw dict is returned."""
        return self._call("GET", f"/shops/{shop}/items")

    def convert_treasure(self, payload: dict | None = None) -> dict:
        """POST /shops/treasure/items/convert — convert treasure shop items.

        The caller's payload is passed through verbatim (the convert body
        is not in the recovered spec, so no fields are invented); the
        default is empty and only the sealed identity rides along.
        """
        return self._call("POST", "/shops/treasure/items/convert", payload=payload)

    def dragonball_sets(self) -> dict:
        """GET /dragonball_sets — all collected dragonball sets. The
        response shape is documented-unconfirmed; the raw dict is
        returned."""
        return self._call("GET", "/dragonball_sets")

    def dragonball_set(self, set_id: int) -> dict:
        """GET /dragonball_sets/{set_id} — one dragonball set by id. The
        response shape is documented-unconfirmed; the raw dict is
        returned."""
        return self._call("GET", f"/dragonball_sets/{set_id}")

    def wish(self, payload: dict | None = None) -> dict:
        """POST /wishes — make a Shenron wish.

        The wish selection fields are not in the recovered spec, so the
        caller supplies them and they are passed through verbatim; the
        default is empty and only the sealed identity rides along.
        """
        return self._call("POST", "/wishes", payload=payload)

    def sd_packs(self) -> dict:
        """GET /sd/packs — starter/step-up pack listing. The response
        shape is documented-unconfirmed; the raw dict is returned."""
        return self._call("GET", "/sd/packs")

    def banner_detail(self, banner_id: int) -> dict:
        """GET /gashas/{banner_id} — one summon banner's detail (the
        listing lives in SummonMixin). The response shape is
        documented-unconfirmed; the raw dict is returned untouched."""
        return self._call("GET", f"/gashas/{banner_id}")
