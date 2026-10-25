"""Cards mixin (inventory, exchange, awakening, training, potential)
against a fake game server.

In-file copy of the FakeGameServer pattern from tests/test_daily.py: a
threaded http.server speaking the recovered envelope (data/sign form
fields, JSON responses sealed as ``{"sign": ...}``). The gateway is a real
:class:`~dokkan.adapters.http_gateway.HttpGateway` so the mixin arrives via
composition. Wire facts from memories 02, 06 and 12.
"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from dokkan.adapters.envelope import Envelope, envelope_secrets
from dokkan.adapters.http_gateway import HttpGateway
from dokkan.adapters.save_store import SaveRecord
from dokkan.domain.models import Card, CardType, Rarity


def _fields(payload: dict, keys: tuple[str, ...]) -> dict:
    """The candidate keys of a recorded request payload (identity stripped)."""
    return {k: v for k, v in payload.items() if k in keys}


class FakeGameServer:
    """Minimal game API speaking the recovered envelope (test-local copy)."""

    def __init__(self, client: str = "global") -> None:
        self.client = client
        self.request_env = Envelope(*envelope_secrets(client, "request"))
        self.response_env = Envelope(*envelope_secrets(client, "response"))
        self.requests: list[tuple[str, str, dict, dict]] = []  # (method, path, query, payload)
        self.state = {
            "cards": [],
        }

        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _respond(self, code: int, payload: dict | None = None) -> None:
                body = b"pong" if payload is None else json.dumps({"sign": server._seal(payload)}).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json" if payload is not None else "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle(self, method: str) -> None:
                split = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(split.query).items()}
                clean_query = {k: v for k, v in query.items() if k not in ("data", "sign")}
                if method == "POST":
                    length = int(self.headers.get("Content-Length", 0))
                    fields = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
                else:
                    fields = query
                payload = server.request_env.open(fields["data"])
                server.requests.append((method, split.path, clean_query, payload))
                response = server.route(split.path, payload)
                self._respond(200, response)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self.thread.start()

    # -- test-side helpers ---------------------------------------------------
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def _seal(self, payload: dict) -> str:
        return self.response_env.seal(payload)[0]

    def route(self, path: str, payload: dict) -> dict:
        # /cards/exchange and /cards/{uid} share the /cards/ prefix: the
        # literal route wins, like the real router's longest match.
        if path == "/cards":
            return {"cards": self.state["cards"]}
        if path == "/cards/exchange":
            # memory 02/06: response may carry gain_exchange_point/exchange_point
            return {"gain_exchange_point": 10, "exchange_point": 120}
        if path.startswith("/cards/"):
            # raw single-card fetch; shape unconfirmed, echoed verbatim
            return {"uid": int(path[len("/cards/"):]), "name": "Card"}
        if path in ("/awake", "/awaken2", "/awaken3"):
            return {"success": True}
        if path == "/bulk_optimal_awake":
            return {"success": True}
        if path == "/awakening_items":
            return {"awakening_items": []}
        if path == "/awakening_items/sell":
            return {"success": True}
        if path == "/training":
            return {"success": True}
        if path == "/bulk_train":
            return {"success": True}
        if path == "/training_fields":
            return {"training_fields": []}
        if path == "/training_items":
            return {"training_items": []}
        if path == "/hidden_potential":
            return {"success": True}
        if path == "/hidden_potential_batch":
            return {"success": True}
        if path == "/potentials/release":
            return {"success": True}
        if path == "/potential_items":
            return {"potential_items": []}
        raise AssertionError(f"unexpected path {path}")

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self.thread.join(timeout=5)


class CardsMixinTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeGameServer()
        self.gateway = HttpGateway(
            SaveRecord("kyle", "at", "s", "global", user_id=42),
            versions=None, base_url=self.server.url())

    def tearDown(self):
        self.server.close()

    def _last(self):
        """The most recent request as (method, path, payload)."""
        method, path, _, payload = self.server.requests[-1]
        return method, path, payload

    # -- inventory -----------------------------------------------------------
    def test_list_cards_maps_response_to_card_objects(self):
        # same per-card candidate keys as gateway_summon: first present wins
        self.server.state["cards"] = [
            {"id": 1001, "name": "Goku", "rarity": 5, "type": "STR", "level": 120,
             "locked": True, "favorite": False, "attack": 15000},
            {"card_id": 1002, "card_name": "Vegeta", "base_rarity": "UR",
             "element": "AGL", "lv": 90, "lock": False, "favo": True, "atk": 12000},
        ]
        cards = self.gateway.list_cards()
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0], Card(id=1001, name="Goku", rarity=Rarity.LR,
                                        type=CardType.STR, level=120, locked=True,
                                        favorite=False, attack=15000))
        self.assertEqual(cards[1], Card(id=1002, name="Vegeta", rarity=Rarity.UR,
                                        type=CardType.AGL, level=90, locked=False,
                                        favorite=True, attack=12000))
        method, path, _ = self._last()
        self.assertEqual((method, path), ("GET", "/cards"))

    def test_list_cards_with_empty_inventory(self):
        self.assertEqual(self.gateway.list_cards(), [])
        self.assertEqual(self._last()[:2], ("GET", "/cards"))

    def test_get_card_fetches_raw_dict_for_uid(self):
        raw = self.gateway.get_card(1234)
        # raw dict returned; the server echoes the interpolated uid
        self.assertEqual(raw, {"uid": 1234, "name": "Card"})
        method, path, _ = self._last()
        self.assertEqual((method, path), ("GET", "/cards/1234"))

    # -- exchange ------------------------------------------------------------
    def test_exchange_cards_posts_card_ids(self):
        raw = self.gateway.exchange_cards([1, 2, 3])
        # response returned raw, including the memory-02/06 exchange points
        self.assertEqual(raw, {"gain_exchange_point": 10, "exchange_point": 120})
        method, path, payload = self._last()
        self.assertEqual((method, path), ("POST", "/cards/exchange"))
        self.assertEqual(_fields(payload, ("card_ids", "item_cards")),
                         {"card_ids": [1, 2, 3]})

    def test_exchange_cards_includes_item_cards_when_given(self):
        self.gateway.exchange_cards([7], item_cards=[8, 9])
        method, path, payload = self._last()
        self.assertEqual((method, path), ("POST", "/cards/exchange"))
        self.assertEqual(_fields(payload, ("card_ids", "item_cards")),
                         {"card_ids": [7], "item_cards": [8, 9]})

    # -- awakening -----------------------------------------------------------
    def test_awaken_routes_step_to_path(self):
        for step, expected_path in ((1, "/awake"), (2, "/awaken2"), (3, "/awaken3")):
            with self.subTest(step=step):
                self.server.requests.clear()
                raw = self.gateway.awaken(55, step=step)
                self.assertEqual(raw, {"success": True})
                method, path, payload = self._last()
                self.assertEqual((method, path), ("POST", expected_path))
                self.assertEqual(_fields(payload, ("card_id",)), {"card_id": 55})

    def test_awaken_defaults_to_step_one(self):
        self.gateway.awaken(55)
        self.assertEqual(self._last()[:2], ("POST", "/awake"))

    def test_awaken_rejects_unknown_step(self):
        self.assertEqual(len(self.server.requests), 0)
        with self.assertRaises(ValueError):
            self.gateway.awaken(55, step=4)
        self.assertEqual(len(self.server.requests), 0)  # no wire call was made

    def test_bulk_optimal_awake_posts_card_ids(self):
        raw = self.gateway.bulk_optimal_awake([4, 5])
        self.assertEqual(raw, {"success": True})
        method, path, payload = self._last()
        self.assertEqual((method, path), ("POST", "/bulk_optimal_awake"))
        self.assertEqual(_fields(payload, ("card_ids",)), {"card_ids": [4, 5]})

    def test_bulk_optimal_awake_without_card_ids(self):
        self.gateway.bulk_optimal_awake()
        method, path, payload = self._last()
        self.assertEqual((method, path), ("POST", "/bulk_optimal_awake"))
        self.assertEqual(_fields(payload, ("card_ids",)), {})

    def test_awakening_items_gets_list(self):
        raw = self.gateway.awakening_items()
        self.assertEqual(raw, {"awakening_items": []})
        self.assertEqual(self._last()[:2], ("GET", "/awakening_items"))

    def test_sell_awakening_items_posts_item_ids(self):
        raw = self.gateway.sell_awakening_items([11, 12])
        self.assertEqual(raw, {"success": True})
        method, path, payload = self._last()
        self.assertEqual((method, path), ("POST", "/awakening_items/sell"))
        self.assertEqual(_fields(payload, ("item_ids",)), {"item_ids": [11, 12]})

    # -- training ------------------------------------------------------------
    def test_train_posts_card_id_plus_fields(self):
        raw = self.gateway.train(77, training_item_id=3, field=1)
        self.assertEqual(raw, {"success": True})
        method, path, payload = self._last()
        self.assertEqual((method, path), ("POST", "/training"))
        self.assertEqual(_fields(payload, ("card_id", "training_item_id", "field")),
                         {"card_id": 77, "training_item_id": 3, "field": 1})

    def test_bulk_train_posts_caller_payload_verbatim(self):
        caller = {"card_ids": [1, 2], "field": 2, "custom": "x"}
        raw = self.gateway.bulk_train(caller)
        self.assertEqual(raw, {"success": True})
        method, path, payload = self._last()
        self.assertEqual((method, path), ("POST", "/bulk_train"))
        self.assertEqual(_fields(payload, tuple(caller)), caller)

    def test_training_fields_gets_list(self):
        raw = self.gateway.training_fields()
        self.assertEqual(raw, {"training_fields": []})
        self.assertEqual(self._last()[:2], ("GET", "/training_fields"))

    def test_training_items_gets_list(self):
        raw = self.gateway.training_items()
        self.assertEqual(raw, {"training_items": []})
        self.assertEqual(self._last()[:2], ("GET", "/training_items"))

    # -- hidden potential ----------------------------------------------------
    def test_hidden_potential_posts_card_id(self):
        raw = self.gateway.hidden_potential(88)
        self.assertEqual(raw, {"success": True})
        method, path, payload = self._last()
        self.assertEqual((method, path), ("POST", "/hidden_potential"))
        self.assertEqual(_fields(payload, ("card_id",)), {"card_id": 88})

    def test_hidden_potential_batch_posts_card_ids(self):
        raw = self.gateway.hidden_potential_batch([88, 89])
        self.assertEqual(raw, {"success": True})
        method, path, payload = self._last()
        self.assertEqual((method, path), ("POST", "/hidden_potential_batch"))
        self.assertEqual(_fields(payload, ("card_ids",)), {"card_ids": [88, 89]})

    def test_release_potential_posts_card_id(self):
        raw = self.gateway.release_potential(88)
        self.assertEqual(raw, {"success": True})
        method, path, payload = self._last()
        self.assertEqual((method, path), ("POST", "/potentials/release"))
        self.assertEqual(_fields(payload, ("card_id",)), {"card_id": 88})

    def test_potential_items_gets_list(self):
        raw = self.gateway.potential_items()
        self.assertEqual(raw, {"potential_items": []})
        self.assertEqual(self._last()[:2], ("GET", "/potential_items"))


if __name__ == "__main__":
    unittest.main()
