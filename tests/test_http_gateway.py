"""HttpGateway against a fake envelope-speaking game server (memory 02/04/06).

The fake server implements the wire contract the recovered spec describes:
POST bodies (and GET queries) carry ``data``/``sign`` form fields, responses
are JSON — plaintext fields or ``{"sign": "<b64>"}``.
"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from dokkan.adapters.envelope import Envelope, envelope_secrets
from dokkan.adapters.http_gateway import DEFAULT_HOSTS, HttpGateway
from dokkan.adapters.save_store import SaveRecord
from dokkan.application.errors import RefillUnavailable
from dokkan.domain.models import Difficulty, Rarity, Stage, Team


def header(headers: dict, name: str):
    """Case-insensitive header lookup (RFC 7230 \u00a73.2); urllib re-titles
    header names before they hit the wire, so tests must not assume the
    exact case the gateway code set."""
    name = name.lower()
    return next((v for k, v in headers.items() if k.lower() == name), None)

USER_PAYLOAD = {
    "user": {"name": "Testy", "act": 40, "act_max": 50, "card_capacity": 100}
}
CARDS_PAYLOAD = {
    "cards": [
        {"id": 1, "name": "Hero", "rarity": 5, "type": "STR", "level": 80, "attack": 9000},
        {"id": 2, "name": "Recruit", "rarity": 0, "type": "AGL", "level": 1, "attack": 100},
    ]
}


class FakeGameServer:
    """Minimal game API speaking the recovered envelope."""

    def __init__(self, client: str = "global") -> None:
        self.client = client
        self.request_env = Envelope(*envelope_secrets(client, "request"))
        self.response_env = Envelope(*envelope_secrets(client, "response"))
        self.requests: list[tuple[str, str, dict, dict]] = []  # (method, path, query, payload)
        self.raw_bodies: list[bytes] = []
        self.request_headers: list[dict] = []
        self.version_rejections = 0  # respond N times with new_client_version_exists
        self.state = {"user": dict(USER_PAYLOAD["user"]), "cards": [dict(c) for c in CARDS_PAYLOAD["cards"]]}

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
                server.request_headers.append(dict(self.headers.items()))
                if split.path == "/ping":
                    server.requests.append((method, split.path, clean_query, {}))
                    self._respond(200)
                    return
                if method == "POST":
                    length = int(self.headers.get("Content-Length", 0))
                    server.raw_bodies.append(self.rfile.read(length))
                    fields = {k: v[0] for k, v in parse_qs(server.raw_bodies[-1].decode()).items()}
                else:
                    fields = query
                payload = server.request_env.open(fields["data"])
                server.requests.append((method, split.path, clean_query, payload))
                if server.version_rejections > 0:
                    server.version_rejections -= 1
                    self._respond(200, {"new_client_version_exists": True, "client_version": "9.9.9"})
                    return
                self._respond(200, server.route(split.path, payload))

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self.thread.start()

    # -- test-side helpers -------------------------------------------------
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def _seal(self, payload: dict) -> str:
        return self.response_env.seal(payload)[0]

    def route(self, path: str, payload: dict) -> dict:
        if path == "/user":
            return {"user": self.state["user"]}
        if path == "/cards":
            return {"cards": self.state["cards"]}
        if path.startswith("/resources/login"):
            return {"user": self.state["user"], "cards": self.state["cards"], "missions": []}
        if path == "/user/capacity/card":
            return {"capacity": 100}
        if path == "/events":
            return {"events": [{
                "id": 1, "name": "Training",
                "stages": [
                    {"id": "1001", "name": "Easy Field", "difficulty": 0, "stamina_cost": 6},
                    {"id": "1002", "name": "Z-Hard Field", "difficulty": 3, "stamina_cost": 20},
                ]}]}
        if path == "/cards/sell":
            sold = set(payload.get("cards", []))
            self.state["cards"] = [c for c in self.state["cards"] if c["id"] not in sold]
            return {"success": True, "requested_ids": sorted(sold), "sold_ids": sorted(sold),
                    "failed_ids": [], "skipped_ids": []}
        if path.startswith("/stages/"):
            return {"success": True, "is_cleared": True, "user": self.state["user"]}
        if path.startswith("/user/recover_act_with_"):
            self.state["user"]["act"] = self.state["user"]["act_max"]
            return {"success": True, "user": self.state["user"]}
        raise AssertionError(f"unexpected path {path}")

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self.thread.join(timeout=5)


class StubVersions:
    def __init__(self, code: str | None = "6.6.0-abc") -> None:
        self.code = code
        self.refreshes = 0
        self.learned: list[str] = []

    def refresh(self) -> None:
        self.refreshes += 1

    def learn(self, code: str) -> None:
        self.learned.append(code)
        self.code = code


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeGameServer()
        self.versions = StubVersions()
        self.gateway = HttpGateway(
            SaveRecord("kyle", "at", "s", "global", user_id=42),
            versions=self.versions, base_url=self.server.url())

    def tearDown(self):
        self.server.close()

    def test_default_hosts_match_the_recovered_constants(self):
        self.assertEqual(DEFAULT_HOSTS, {
            "global": "https://ishin-global.aktsk.com",
            "japan": "https://ishin-production.aktsk.jp",
        })

    def test_ping_returns_true_on_200(self):
        self.assertTrue(self.gateway.ping())

    def test_ping_sends_x_client_version_header(self):
        # memory 02/12 update: /ping and every call carry the version code as
        # the X-ClientVersion header (confirmed verbatim string in the real
        # client's libcocos2dcpp.so, and against the live ishin-global API —
        # a bare GET /ping with no header returns 400
        # client_version/format_invalid; adding this header returns 200 with
        # a real ping_info body). Not a query param, not in the envelope.
        self.gateway.ping()
        self.assertEqual(
            header(self.server.request_headers[-1], "X-ClientVersion"),
            "6.6.0-abc")

    def test_post_body_is_form_encoded_data_and_sign(self):
        self.gateway.sell_cards([1])
        raw = self.server.raw_bodies[-1].decode()
        self.assertIn("/cards/sell", self.server.requests[-1][1])
        fields = {k: v[0] for k, v in parse_qs(raw).items()}
        self.assertEqual(set(fields), {"data", "sign"})
        self.assertTrue(self.server.request_env.verify(fields["data"], fields["sign"]))

    def test_payload_carries_identity_and_calls_carry_the_version_header(self):
        self.gateway.fetch_account()
        paths = [r[1].split("?")[0] for r in self.server.requests]
        self.assertIn("/resources/login", paths)
        self.assertIn("/user/capacity/card", paths)
        for payload in (p for (_, _, _, p) in self.server.requests):
            self.assertEqual(payload.get("identifier"), "kyle")
            self.assertEqual(payload.get("access_token"), "at")
            self.assertEqual(payload.get("secret"), "s")
        for headers in self.server.request_headers:
            self.assertEqual(header(headers, "X-ClientVersion"), "6.6.0-abc")

    def test_fetch_account_uses_the_verbatim_resources_login_combo(self):
        self.gateway.fetch_account()
        login = next(r for r in self.server.requests if r[1].startswith("/resources/login"))
        self.assertEqual(login[0], "GET")
        self.assertEqual(login[2], {
            "act_items": "true", "support_items": "true", "treasure_items": "true",
            "cards": "true", "user": "true", "missions": "true"})

    def test_fetch_account_maps_user_and_cards(self):
        account = self.gateway.fetch_account()
        self.assertEqual(account.name, "Testy")
        self.assertEqual((account.stamina.current, account.stamina.maximum), (40, 50))
        self.assertEqual(account.card_capacity, 100)
        self.assertEqual([c.id for c in account.cards], [1, 2])
        self.assertEqual(account.cards[0].rarity, Rarity.LR)


class GatewayOperationTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeGameServer()
        self.versions = StubVersions()
        self.gateway = HttpGateway(
            SaveRecord("kyle", "at", "s", "global", user_id=42),
            versions=self.versions, base_url=self.server.url())

    def tearDown(self):
        self.server.close()

    def _requests(self, prefix: str):
        return [r for r in self.server.requests if r[1].split("?")[0].startswith(prefix)]

    def test_list_events_maps_events_and_stages(self):
        events = self.gateway.list_events()
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0].id, events[0].name), (1, "Training"))
        self.assertEqual([s.id for s in events[0].stages], ["1001", "1002"])
        self.assertEqual(events[0].stages[1].stamina_cost, 20)
        self.assertEqual(len(self._requests("/events")), 1)
        self.assertEqual(self._requests("/events")[0][0], "GET")

    def test_run_stage_does_briefing_start_finish(self):
        stage = Stage("1001", "Easy Field", Difficulty.EASY, 6)
        result = self.gateway.run_stage(stage, Team((1, 2)))
        self.assertTrue(result.cleared)
        self.assertEqual([r[1].split("?")[0] for r in self._requests("/stages/1001")],
                         ["/stages/1001/briefing", "/stages/1001/start", "/stages/1001/finish"])
        briefing, start, finish = self._requests("/stages/1001")
        self.assertEqual(briefing[0], "GET")
        self.assertEqual(briefing[2], {"difficulty": "0", "force_update": "true", "team_num": "2"})
        self.assertEqual(start[0], "POST")
        self.assertEqual(start[3].get("card_ids"), [1, 2])
        self.assertEqual(finish[0], "POST")
        for key in ("elapsed_time", "start_time", "finish_time"):
            self.assertIn(key, finish[3])

    def test_sell_cards_batches_of_99(self):
        ids = list(range(1, 251))  # 250 cards -> 3 batches
        self.server.state["cards"] = [{"id": i, "name": f"c{i}", "rarity": 0, "type": "AGL"}
                                      for i in ids]
        self.gateway.sell_cards(ids)
        sells = self._requests("/cards/sell")
        self.assertEqual(len(sells), 3)
        self.assertEqual([len(s[3]["cards"]) for s in sells], [99, 99, 52])
        self.assertEqual(len(self.server.state["cards"]), 0)

    def test_refill_stamina_maps_source_to_endpoint(self):
        from dokkan.application.ports import RefillSource
        stamina = self.gateway.refill_stamina(RefillSource.STONE)
        self.assertEqual((stamina.current, stamina.maximum), (50, 50))
        self.assertEqual(len(self._requests("/user/recover_act_with_stone")), 1)
        self.gateway.refill_stamina(RefillSource.MEAT)
        self.assertEqual(len(self._requests("/user/recover_act_with_items")), 1)

    def test_refill_error_is_refill_unavailable(self):
        from dokkan.application.ports import RefillSource
        def refuse(path, payload):
            if path.startswith("/user/recover_act_with_"):
                return {"error": "no_items"}
            raise AssertionError(path)
        self.server.route = refuse
        with self.assertRaises(RefillUnavailable):
            self.gateway.refill_stamina(RefillSource.MEAT)


class VersionRetryTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeGameServer()
        self.versions = StubVersions()
        self.gateway = HttpGateway(
            SaveRecord("kyle", "at", "s", "global", user_id=42),
            versions=self.versions, base_url=self.server.url())

    def tearDown(self):
        self.server.close()

    def test_version_mismatch_refreshes_and_retries_once(self):
        self.server.version_rejections = 1
        self.gateway.fetch_account()
        self.assertEqual(self.versions.refreshes, 1)
        logins = [r for r in self.server.requests if r[1].startswith("/resources/login")]
        self.assertEqual(len(logins), 2)  # rejected once, retried once

    def test_persistent_version_mismatch_raises_after_one_retry(self):
        from dokkan.adapters.http_gateway import GatewayError
        self.server.version_rejections = 5
        with self.assertRaises(GatewayError):
            self.gateway.fetch_account()
        self.assertEqual(self.versions.refreshes, 1)
        logins = [r for r in self.server.requests if r[1].startswith("/resources/login")]
        self.assertEqual(len(logins), 2)

    def test_no_version_source_and_mismatch_raises(self):
        from dokkan.adapters.http_gateway import GatewayError
        self.server.version_rejections = 5
        gateway = HttpGateway(SaveRecord("kyle", "at", "s", "global"),
                              base_url=self.server.url())
        with self.assertRaises(GatewayError):
            gateway.fetch_account()

    def test_rejection_teaches_the_server_version_and_retries(self):
        # memory 12: "updating from game server" — a rejection carrying the
        # authoritative client_version is learned and used by the retry,
        # with no remote feed configured at all.
        self.server.version_rejections = 1
        self.gateway.fetch_account()
        self.assertEqual(self.versions.learned, ["9.9.9"])
        self.assertEqual(self.versions.code, "9.9.9")
        self.assertEqual(
            header(self.server.request_headers[-1], "X-ClientVersion"),
            "9.9.9")


if __name__ == "__main__":
    unittest.main()
