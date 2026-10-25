"""Shops (Baba, per-shop items, treasure convert), dragonball sets, wishes,
sd packs and single-banner detail against a fake game server.

In-file copy of the FakeGameServer pattern from tests/test_daily.py: a
threaded http.server speaking the recovered envelope (data/sign form
fields, JSON responses sealed as ``{"sign": ...}``). Wire facts from
memories 02 and 12; the ShopsMixin comes via composition on HttpGateway.
"""
import json
import re
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from dokkan.adapters.envelope import Envelope, envelope_secrets
from dokkan.adapters.http_gateway import HttpGateway
from dokkan.adapters.save_store import SaveRecord

# The sealed identity fields the transport merges into every payload
# (http_gateway._identity); a "default empty" caller payload means the
# recorded request carries nothing beyond these.
_IDENTITY_KEYS = {"identifier", "access_token", "secret", "user_id"}


class FakeGameServer:
    """Minimal game API speaking the recovered envelope (test-local copy)."""

    def __init__(self, client: str = "global") -> None:
        self.client = client
        self.request_env = Envelope(*envelope_secrets(client, "request"))
        self.response_env = Envelope(*envelope_secrets(client, "response"))
        self.requests: list[tuple[str, str, dict, dict]] = []  # (method, path, query, payload)
        self.state = {
            "baba_response": {"baba": []},
            "wish_response": {"success": True},
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
                self._respond(200, server.route(split.path, payload))

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
        if path == "/baba":
            return dict(self.state["baba_response"])
        if path == "/shops/treasure/items/convert":
            return {"success": True}
        if path == "/wishes":
            return dict(self.state["wish_response"])
        if path == "/dragonball_sets":
            return {"dragonball_sets": [{"id": 1}, {"id": 2}]}
        if path == "/sd/packs":
            return {"packs": []}
        if path == "/shops/treasure/items":
            return {"items": [{"id": 5}]}
        match = re.fullmatch(r"/shops/([^/]+)/items/([^/]+)/buy", path)
        if match:
            return {"success": True, "shop": match.group(1), "item_id": int(match.group(2))}
        match = re.fullmatch(r"/dragonball_sets/([^/]+)", path)
        if match:
            return {"id": int(match.group(1))}
        match = re.fullmatch(r"/gashas/([^/]+)", path)
        if match:
            return {"id": int(match.group(1))}
        raise AssertionError(f"unexpected path {path}")

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self.thread.join(timeout=5)


class ShopsMixinTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeGameServer()
        self.gateway = HttpGateway(
            SaveRecord("kyle", "at", "s", "global", user_id=42),
            versions=None, base_url=self.server.url())

    def tearDown(self):
        self.server.close()

    def _calls(self):
        return [(method, path) for (method, path, _, _) in self.server.requests]

    def _last_payload(self):
        return self.server.requests[-1][3]

    # -- baba ----------------------------------------------------------------
    def test_baba_gets_the_baba(self):
        result = self.gateway.baba()
        self.assertEqual(self._calls(), [("GET", "/baba")])
        self.assertEqual(result, {"baba": []})

    # -- buy_item ------------------------------------------------------------
    def test_buy_item_posts_the_verbatim_shop_template(self):
        result = self.gateway.buy_item("baba", 7)
        self.assertEqual(self._calls(), [("POST", "/shops/baba/items/7/buy")])
        self.assertEqual(self._last_payload()["quantity"], 1)  # default quantity
        self.assertEqual(result["item_id"], 7)

    def test_buy_item_passes_quantity_and_any_shop(self):
        self.gateway.buy_item("exchange_point", 12, quantity=3)
        self.assertEqual(self._calls(), [("POST", "/shops/exchange_point/items/12/buy")])
        self.assertEqual(self._last_payload()["quantity"], 3)
        # the shop name rides only in the path, never validated client-side
        self.gateway.buy_item("zeni", 1, quantity=2)
        self.assertEqual(self._calls()[-1], ("POST", "/shops/zeni/items/1/buy"))

    # -- list_shop_items -----------------------------------------------------
    def test_list_shop_items_gets_the_shop_items_path(self):
        result = self.gateway.list_shop_items("treasure")
        self.assertEqual(self._calls(), [("GET", "/shops/treasure/items")])
        self.assertEqual(result, {"items": [{"id": 5}]})

    # -- convert_treasure ---------------------------------------------------
    def test_convert_treasure_passes_the_callers_payload_verbatim(self):
        self.gateway.convert_treasure({"item_ids": [1, 2], "target": "orb"})
        self.assertEqual(self._calls(), [("POST", "/shops/treasure/items/convert")])
        payload = self._last_payload()
        self.assertEqual(payload["item_ids"], [1, 2])
        self.assertEqual(payload["target"], "orb")

    def test_convert_treasure_defaults_to_an_empty_payload(self):
        self.gateway.convert_treasure()
        self.assertEqual(self._calls(), [("POST", "/shops/treasure/items/convert")])
        # nothing beyond the sealed identity fields rides along
        self.assertLessEqual(set(self._last_payload()), _IDENTITY_KEYS)

    # -- dragonball sets -----------------------------------------------------
    def test_dragonball_sets_lists_all_sets(self):
        result = self.gateway.dragonball_sets()
        self.assertEqual(self._calls(), [("GET", "/dragonball_sets")])
        self.assertEqual(result["dragonball_sets"], [{"id": 1}, {"id": 2}])

    def test_dragonball_set_gets_one_set_by_id(self):
        result = self.gateway.dragonball_set(3)
        self.assertEqual(self._calls(), [("GET", "/dragonball_sets/3")])
        self.assertEqual(result, {"id": 3})

    # -- wishes --------------------------------------------------------------
    def test_wish_posts_the_callers_fields(self):
        self.gateway.wish({"wish_id": 4, "dragonball_set_id": 2})
        self.assertEqual(self._calls(), [("POST", "/wishes")])
        payload = self._last_payload()
        self.assertEqual(payload["wish_id"], 4)
        self.assertEqual(payload["dragonball_set_id"], 2)

    def test_wish_defaults_to_an_empty_payload(self):
        self.gateway.wish()
        self.assertEqual(self._calls(), [("POST", "/wishes")])
        self.assertLessEqual(set(self._last_payload()), _IDENTITY_KEYS)

    # -- sd packs ------------------------------------------------------------
    def test_sd_packs_lists_packs(self):
        result = self.gateway.sd_packs()
        self.assertEqual(self._calls(), [("GET", "/sd/packs")])
        self.assertEqual(result, {"packs": []})

    # -- banner_detail -------------------------------------------------------
    def test_banner_detail_gets_one_banner_by_id(self):
        result = self.gateway.banner_detail(2196)
        self.assertEqual(self._calls(), [("GET", "/gashas/2196")])
        self.assertEqual(result, {"id": 2196})


if __name__ == "__main__":
    unittest.main()
