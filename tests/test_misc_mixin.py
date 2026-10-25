"""Misc/account endpoints against a fake game server.

In-file copy of the FakeGameServer pattern from tests/test_http_gateway.py
(via tests/test_daily.py): a threaded http.server speaking the recovered
envelope (data/sign form fields, JSON responses sealed as ``{"sign": ...}``),
plus a real HttpGateway with the mixin composed on. Wire facts from
memories 02 and 12.
"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from dokkan.adapters.envelope import Envelope, envelope_secrets
from dokkan.adapters.http_gateway import HttpGateway
from dokkan.adapters.save_store import SaveRecord


class FakeGameServer:
    """Minimal game API speaking the recovered envelope (test-local copy)."""

    # Every path this suite is allowed to touch (all verbatim, memories 02/12).
    KNOWN_PATHS = {
        "/user", "/user/mydata", "/user_areas", "/items", "/system", "/status",
        "/config", "/resources/home", "/teams", "/team_save",
        "/item_reverse_resolutions/awakening_items", "/giru_nav/unacquired",
        "/iap_rails/itunes_products", "/events/eventkagi_events",
        "/eventkagi_items", "/cooperation_campaigns", "/joint_campaigns",
        "/joint_campaigns/accept", "/missions/mission_board_campaigns",
        "/announcements/notify",
    }

    def __init__(self, client: str = "global") -> None:
        self.client = client
        self.request_env = Envelope(*envelope_secrets(client, "request"))
        self.response_env = Envelope(*envelope_secrets(client, "response"))
        self.requests: list[tuple[str, str, dict, dict]] = []  # (method, path, query, payload)
        self.state = {"responses": {}}  # per-path canned response bodies

        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _respond(self, code: int, payload: dict | None = None) -> None:
                body = json.dumps({"sign": server._seal(payload)}).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
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
        if path in self.KNOWN_PATHS:
            return dict(self.state["responses"].get(path, {"ok": True}))
        raise AssertionError(f"unexpected path {path}")

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self.thread.join(timeout=5)


class MiscMixinTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeGameServer()
        self.gateway = HttpGateway(
            SaveRecord("kyle", "at", "s", "global", user_id=42),
            versions=None, base_url=self.server.url())

    def tearDown(self):
        self.server.close()

    def _sole_call(self) -> tuple[str, str, dict, dict]:
        self.assertEqual(len(self.server.requests), 1)
        return self.server.requests[0]

    # -- plain wrappers --------------------------------------------------------
    def test_plain_endpoints_route_with_method_and_path(self):
        cases = [
            (self.gateway.user, "GET", "/user"),
            (self.gateway.mydata, "GET", "/user/mydata"),
            (self.gateway.user_areas, "GET", "/user_areas"),
            (self.gateway.items, "GET", "/items"),
            (self.gateway.system, "GET", "/system"),
            (self.gateway.status, "GET", "/status"),
            (self.gateway.config, "GET", "/config"),
            (self.gateway.teams, "GET", "/teams"),
            (self.gateway.item_reverse_resolutions,
             "GET", "/item_reverse_resolutions/awakening_items"),
            (self.gateway.giru_nav_unacquired, "GET", "/giru_nav/unacquired"),
            (self.gateway.eventkagi_events, "GET", "/events/eventkagi_events"),
            (self.gateway.eventkagi_items, "GET", "/eventkagi_items"),
            (self.gateway.cooperation_campaigns, "GET", "/cooperation_campaigns"),
            (self.gateway.mission_board_campaigns,
             "GET", "/missions/mission_board_campaigns"),
        ]
        for call, method, path in cases:
            with self.subTest(path=path):
                self.server.requests.clear()
                self.server.state["responses"][path] = {"echo": path}
                self.assertEqual(call(), {"echo": path})
                self.assertEqual(self._sole_call()[:2], (method, path))

    # -- announce --------------------------------------------------------------
    def test_announce_posts_notify(self):
        self.assertEqual(self.gateway.announce(), {"ok": True})
        method, path, query, payload = self._sole_call()
        self.assertEqual((method, path), ("POST", "/announcements/notify"))

    # -- teams -----------------------------------------------------------------
    def test_save_team_posts_caller_supplied_fields(self):
        team = {"team": 1, "deck_num": 2, "card_ids": [11, 12],
                "team_cards": [], "members": [], "sub_leader": 12}
        self.gateway.save_team(team)
        method, path, query, payload = self._sole_call()
        self.assertEqual((method, path), ("POST", "/team_save"))
        # the team fields ride the sealed body alongside the identity fields
        for key, value in team.items():
            self.assertIn(key, payload)
            self.assertEqual(payload[key], value)

    # -- resources/home --------------------------------------------------------
    def test_resources_home_coerces_flags_to_true_false_strings(self):
        self.gateway.resources_home(user=True, gifts=False,
                                    random_login_bonuses=True, banners=0)
        method, path, query, payload = self._sole_call()
        self.assertEqual((method, path), ("GET", "/resources/home"))
        self.assertEqual(query, {
            "user": "true", "gifts": "false",
            "random_login_bonuses": "true", "banners": "false",
        })

    def test_resources_home_with_no_flags_sends_no_extra_params(self):
        self.gateway.resources_home()
        method, path, query, payload = self._sole_call()
        self.assertEqual((method, path), ("GET", "/resources/home"))
        self.assertEqual(query, {})

    # -- iap rails -------------------------------------------------------------
    def test_itunes_products_without_country_sends_no_param(self):
        self.gateway.itunes_products()
        method, path, query, payload = self._sole_call()
        self.assertEqual((method, path), ("GET", "/iap_rails/itunes_products"))
        self.assertNotIn("platform_country", query)

    def test_itunes_products_with_country_sends_param(self):
        self.gateway.itunes_products("US")
        method, path, query, payload = self._sole_call()
        self.assertEqual((method, path), ("GET", "/iap_rails/itunes_products"))
        self.assertEqual(query, {"platform_country": "US"})

    # -- joint campaigns -------------------------------------------------------
    def test_joint_campaigns_get_by_default(self):
        self.assertEqual(self.gateway.joint_campaigns(), {"ok": True})
        self.assertEqual(self._sole_call()[:2], ("GET", "/joint_campaigns"))

    def test_joint_campaigns_accept_routes_to_post(self):
        self.assertEqual(self.gateway.joint_campaigns(accept=True), {"ok": True})
        self.assertEqual(self._sole_call()[:2],
                        ("POST", "/joint_campaigns/accept"))


if __name__ == "__main__":
    unittest.main()
