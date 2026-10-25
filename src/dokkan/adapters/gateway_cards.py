"""Card management: inventory, exchange, awakening, training, potential
(memories 02, 06, 12).

Recovered endpoints (verbatim from the target's strings): ``GET /cards``
and ``GET /cards/{uid}``; ``POST /cards/exchange`` (Baba exchange carrying
``card_ids`` plus optional ``item_cards``, whose response may carry
``gain_exchange_point``/``exchange_point`` — memory 06); the awakening steps
``/awake``, ``/awaken2``, ``/awaken3`` plus ``/bulk_optimal_awake`` and
``/awakening_items`` with ``POST /awakening_items/sell``; training via
``/training``, ``/bulk_train``, ``/training_fields`` and ``/training_items``;
and the potential routes ``/hidden_potential``, ``/hidden_potential_batch``,
``/potentials/release`` and ``/potential_items``.

``CardsMixin`` is composed onto
:class:`~dokkan.adapters.http_gateway.HttpGateway` and reuses its transport
helpers (``_call``, which seals identity and the client version into the
envelope and raises :class:`~dokkan.adapters.http_gateway.GatewayError`);
it imports no siblings, so the candidate-key table is a local copy in the
house style of gateway_summon's (first present key wins).

Wire details the recovered spec leaves unconfirmed are marked inline and
collected here (documented-unconfirmed): the step-to-path awakening
mapping, the payload keys of every write endpoint beyond ``card_ids`` /
``item_cards`` / ``item_ids`` / ``card_id`` (identity is already sealed into
the envelope by ``_call``, so payloads stay minimal), and the response
shapes returned as raw dicts.
"""
from __future__ import annotations

from typing import Sequence

from dokkan.domain.models import Card, CardType, Rarity

# Candidate keys for mapping server responses (documented-unconfirmed, same
# table as gateway_summon / http_gateway: first present key wins).
_CARDS_KEYS = ("cards",)
_CARD_ID_KEYS = ("id", "card_id", "uid")
_CARD_NAME_KEYS = ("name", "card_name")
_CARD_RARITY_KEYS = ("rarity", "base_rarity")
_CARD_TYPE_KEYS = ("type", "element", "attribute")
_CARD_LEVEL_KEYS = ("level", "lv")
_CARD_LOCKED_KEYS = ("locked", "lock")
_CARD_FAVORITE_KEYS = ("favorite", "favo")
_CARD_ATTACK_KEYS = ("attack", "atk")

# Awakening step -> path (documented-unconfirmed: the three /awakenN paths
# are recovered verbatim, which step each serves is not).
_AWAKEN_PATHS = {1: "/awake", 2: "/awaken2", 3: "/awaken3"}


def _first(source: dict, keys: tuple[str, ...], default=None):
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return default


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


class CardsMixin:
    """Inventory, exchange, awakening, training and potential (memory 02/12)."""

    # -- inventory -----------------------------------------------------------
    def list_cards(self) -> Sequence[Card]:
        """GET /cards; response data under ``cards`` (memory 02/12), mapped
        onto :class:`~dokkan.domain.models.Card` with the shared candidate
        key table."""
        payload = self._call("GET", "/cards")
        return [_card(raw) for raw in _first(payload, _CARDS_KEYS, []) or []]

    def get_card(self, uid: int) -> dict:
        """GET /cards/{uid}; the raw response dict (shape unconfirmed in the
        recovered spec, so it is passed through untouched)."""
        return self._call("GET", f"/cards/{uid}")

    def exchange_cards(self, card_ids, item_cards=None) -> dict:
        """POST /cards/exchange (Baba exchange, memory 02).

        The payload carries ``card_ids`` plus ``item_cards`` when given
        (both field names recovered from the target's locals); the response
        may carry ``gain_exchange_point``/``exchange_point`` (memory 06) and
        is returned raw.
        """
        # Payload key names documented-unconfirmed beyond the recovered
        # card_ids/item_cards locals; identity is already sealed by _call.
        payload = {"card_ids": list(card_ids)}
        if item_cards is not None:
            payload["item_cards"] = list(item_cards)
        return self._call("POST", "/cards/exchange", payload=payload)

    # -- awakening -----------------------------------------------------------
    def awaken(self, card_id, step: int = 1) -> dict:
        """POST the awakening step's path: step 1 -> /awake, 2 -> /awaken2,
        3 -> /awaken3 (memory 02; the step-to-path mapping itself is
        documented-unconfirmed — only the three paths are recovered). Any
        other step raises ValueError before touching the wire."""
        try:
            path = _AWAKEN_PATHS[step]
        except (KeyError, TypeError):
            raise ValueError(
                f"awakening step must be 1, 2 or 3, got {step!r}") from None
        # Request field name beyond card_id is documented-unconfirmed.
        return self._call("POST", path, payload={"card_id": card_id})

    def bulk_optimal_awake(self, card_ids=None) -> dict:
        """POST /bulk_optimal_awake (memory 12); ``card_ids`` ride the
        payload when given (documented-unconfirmed key)."""
        payload = {"card_ids": list(card_ids)} if card_ids is not None else None
        return self._call("POST", "/bulk_optimal_awake", payload=payload)

    def awakening_items(self) -> dict:
        """GET /awakening_items (memory 02); raw response dict."""
        return self._call("GET", "/awakening_items")

    def sell_awakening_items(self, item_ids) -> dict:
        """POST /awakening_items/sell (memory 02).

        The id list rides the payload under ``item_ids``
        (documented-unconfirmed key name).
        """
        return self._call("POST", "/awakening_items/sell",
                          payload={"item_ids": list(item_ids)})

    # -- training ------------------------------------------------------------
    def train(self, card_id, **fields) -> dict:
        """POST /training (memory 02) with ``card_id`` plus any extra request
        fields the caller supplies (field names beyond card_id are
        documented-unconfirmed)."""
        payload = {"card_id": card_id, **fields}
        return self._call("POST", "/training", payload=payload)

    def bulk_train(self, payload: dict) -> dict:
        """POST /bulk_train (memory 02) with the caller's payload verbatim
        (identity/version are added by the transport, not here)."""
        return self._call("POST", "/bulk_train", payload=payload)

    def training_fields(self) -> dict:
        """GET /training_fields (memory 02); raw response dict."""
        return self._call("GET", "/training_fields")

    def training_items(self) -> dict:
        """GET /training_items (memory 02); raw response dict."""
        return self._call("GET", "/training_items")

    # -- hidden potential ----------------------------------------------------
    def hidden_potential(self, card_id) -> dict:
        """POST /hidden_potential (memory 02) for one card; raw response
        dict. Request field name beyond card_id is documented-unconfirmed."""
        return self._call("POST", "/hidden_potential", payload={"card_id": card_id})

    def hidden_potential_batch(self, card_ids) -> dict:
        """POST /hidden_potential_batch (memory 02); ``card_ids`` ride the
        payload (documented-unconfirmed key name)."""
        return self._call("POST", "/hidden_potential_batch",
                          payload={"card_ids": list(card_ids)})

    def release_potential(self, card_id) -> dict:
        """POST /potentials/release (memory 02); raw response dict.
        Request field name beyond card_id is documented-unconfirmed."""
        return self._call("POST", "/potentials/release", payload={"card_id": card_id})

    def potential_items(self) -> dict:
        """GET /potential_items (memory 02); raw response dict."""
        return self._call("GET", "/potential_items")
