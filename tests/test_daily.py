"""Daily rewards (gifts, missions, login bonus) against a fake game server.

In-file copy of the FakeGameServer pattern from tests/test_http_gateway.py:
a threaded http.server speaking the recovered envelope (data/sign form
fields, JSON responses sealed as ``{"sign": ...}``), plus a plain in-memory
gateway for the use case. Wire facts from memories 02, 05 and 12.
"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from dokkan.adapters.envelope import Envelope, envelope_secrets
from dokkan.adapters.http_gateway import HttpGateway
from dokkan.adapters.save_store import SaveRecord
from dokkan.application.use_cases.collect_daily import CollectDaily, DailyReport


def _id_fields(payload: dict, keys: tuple[str, ...]) -> dict:
    """The candidate id keys of a recorded accept payload (identity stripped)."""
    return {k: v for k, v in payload.items() if k in keys}


class _HttpError(Exception):
    """Lets route() answer a request with an arbitrary HTTP status."""

    def __init__(self, code: int) -> None:
        self.code = code


class FakeGameServer:
    """Minimal game API speaking the recovered envelope (test-local copy)."""

    def __init__(self, client: str = "global") -> None:
        self.client = client
        self.request_env = Envelope(*envelope_secrets(client, "request"))
        self.response_env = Envelope(*envelope_secrets(client, "response"))
        self.requests: list[tuple[str, str, dict, dict]] = []  # (method, path, query, payload)
        self.fail_accept_ids: set[int] = set()  # gift/mission ids answered with HTTP 500
        self.fail_random_bonuses = False  # /random_login_bonuses/... answered with HTTP 500
        self.state = {
            "gifts": [],
            "missions": [],
            "login_bonus_response": {"success": True},
            "home_response": {},
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
                try:
                    response = server.route(split.path, payload)
                except _HttpError as exc:
                    self._respond(exc.code)
                    return
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

    @staticmethod
    def _id_of(raw: dict):
        return raw.get("id", raw.get("gift_id", raw.get("mission_id")))

    def route(self, path: str, payload: dict) -> dict:
        if path == "/gifts":
            return {"gifts": self.state["gifts"]}
        if path == "/gifts/accept":
            gift_id = payload.get("id", payload.get("gift_id"))
            if gift_id in self.fail_accept_ids:
                raise _HttpError(500)
            self.state["gifts"] = [g for g in self.state["gifts"] if self._id_of(g) != gift_id]
            return {"success": True}
        if path == "/missions":
            return {"missions": self.state["missions"]}
        if path == "/missions/accept":
            mission_id = payload.get("id", payload.get("mission_id"))
            if mission_id in self.fail_accept_ids:
                raise _HttpError(500)
            self.state["missions"] = [m for m in self.state["missions"] if self._id_of(m) != mission_id]
            return {"success": True}
        if path == "/login_bonuses/accept":
            return dict(self.state["login_bonus_response"])
        if path.startswith("/resources/home"):
            return dict(self.state["home_response"])
        if path.startswith("/random_login_bonuses/"):
            if self.fail_random_bonuses:
                raise _HttpError(500)
            return {"success": True}
        raise AssertionError(f"unexpected path {path}")

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self.thread.join(timeout=5)


class DailyGatewayTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeGameServer()
        self.gateway = HttpGateway(
            SaveRecord("kyle", "at", "s", "global", user_id=42),
            versions=None, base_url=self.server.url())

    def tearDown(self):
        self.server.close()

    def _calls(self):
        return [(method, path) for (method, path, _, _) in self.server.requests]

    def _accepts(self, path):
        return [payload for (_, p, _, payload) in self.server.requests if p == path]

    # -- gifts ---------------------------------------------------------------
    def test_accept_gifts_lists_then_accepts_each_gift(self):
        self.server.state["gifts"] = [{"id": 1}, {"gift_id": 2}, {"id": 3}]
        self.assertEqual(self.gateway.accept_gifts(), 3)
        self.assertEqual(self._calls(), [
            ("GET", "/gifts"),
            ("POST", "/gifts/accept"),
            ("POST", "/gifts/accept"),
            ("POST", "/gifts/accept"),
        ])
        accepts = self._accepts("/gifts/accept")
        # the id is posted under the same candidate key it was found with
        # (the transport also merges the sealed identity fields)
        self.assertEqual(_id_fields(accepts[0], ("id", "gift_id")), {"id": 1})
        self.assertEqual(_id_fields(accepts[1], ("id", "gift_id")), {"gift_id": 2})
        self.assertEqual(_id_fields(accepts[2], ("id", "gift_id")), {"id": 3})
        self.assertEqual(self.server.state["gifts"], [])

    def test_accept_gifts_survives_one_failing_accept(self):
        self.server.state["gifts"] = [{"id": 1}, {"id": 2}, {"id": 3}]
        self.server.fail_accept_ids = {2}
        self.assertEqual(self.gateway.accept_gifts(), 2)
        self.assertEqual(len(self._accepts("/gifts/accept")), 3)  # every gift was attempted
        self.assertEqual(self.server.state["gifts"], [{"id": 2}])  # only the failed one stays

    def test_accept_gifts_with_no_pending_gifts(self):
        self.assertEqual(self.gateway.accept_gifts(), 0)
        self.assertEqual(self._calls(), [("GET", "/gifts")])

    # -- missions ------------------------------------------------------------
    def test_accept_missions_lists_then_accepts_each_mission(self):
        self.server.state["missions"] = [{"mission_id": 10}, {"id": 11}]
        self.assertEqual(self.gateway.accept_missions(), 2)
        self.assertEqual(self._calls(), [
            ("GET", "/missions"),
            ("POST", "/missions/accept"),
            ("POST", "/missions/accept"),
        ])
        accepts = self._accepts("/missions/accept")
        self.assertEqual(_id_fields(accepts[0], ("id", "mission_id")), {"mission_id": 10})
        self.assertEqual(_id_fields(accepts[1], ("id", "mission_id")), {"id": 11})
        self.assertEqual(self.server.state["missions"], [])

    def test_accept_missions_survives_one_failing_accept(self):
        self.server.state["missions"] = [{"id": 10}, {"id": 11}, {"id": 12}]
        self.server.fail_accept_ids = {11}
        self.assertEqual(self.gateway.accept_missions(), 2)
        self.assertEqual(self.server.state["missions"], [{"id": 11}])

    # -- login bonus ---------------------------------------------------------
    def test_accept_login_bonus_true_on_success(self):
        self.server.state["login_bonus_response"] = {"success": True}
        self.assertTrue(self.gateway.accept_login_bonus())
        self.assertIn(("POST", "/login_bonuses/accept"), self._calls())

    def test_accept_login_bonus_false_on_error_response(self):
        self.server.state["login_bonus_response"] = {"error": "already_received"}
        self.assertFalse(self.gateway.accept_login_bonus())

    def test_random_bonus_id_from_accept_response_skips_home_poll(self):
        self.server.state["login_bonus_response"] = {"success": True, "random_login_bonus_id": 9}
        self.assertTrue(self.gateway.accept_login_bonus())
        self.assertIn(("POST", "/random_login_bonuses/9/accept"), self._calls())
        self.assertNotIn("GET", [m for (m, p) in self._calls() if p.startswith("/resources/home")])

    def test_random_bonus_polls_home_with_the_verbatim_flag_combo(self):
        self.server.state["home_response"] = {"random_login_bonus_id": 5}
        self.assertTrue(self.gateway.accept_login_bonus())
        home = next(r for r in self.server.requests if r[1].startswith("/resources/home"))
        self.assertEqual(home[0], "GET")
        self.assertEqual(home[2], {
            "apologies": "false", "banners": "false", "bonus_schedules": "false",
            "budokai": "false", "dragonball_sets": "false", "gifts": "false",
            "login_bonuses": "false", "random_login_bonuses": "true",
            "rmbattles": "false", "user": "false", "user_subscription": "false",
        })
        self.assertIn(("POST", "/random_login_bonuses/5/accept"), self._calls())

    def test_random_bonus_best_effort_never_raises(self):
        self.server.state["home_response"] = {"random_login_bonus_id": 77}
        self.server.fail_random_bonuses = True
        self.assertTrue(self.gateway.accept_login_bonus())  # must not raise
        self.assertIn(("POST", "/random_login_bonuses/77/accept"), self._calls())

    def test_no_random_bonus_id_means_no_random_accept_call(self):
        # the home poll happens (the original polls it), but carries no id
        self.assertTrue(self.gateway.accept_login_bonus())
        self.assertEqual(self._calls(), [
            ("POST", "/login_bonuses/accept"), ("GET", "/resources/home")])


class RecordingGateway:
    """In-memory fake gateway recording call order."""

    def __init__(self, login_bonus: bool = True, gifts: int = 0, missions: int = 0) -> None:
        self.login_bonus = login_bonus
        self.gifts = gifts
        self.missions = missions
        self.calls: list[str] = []

    def accept_login_bonus(self) -> bool:
        self.calls.append("login_bonus")
        return self.login_bonus

    def accept_gifts(self) -> int:
        self.calls.append("gifts")
        return self.gifts

    def accept_missions(self) -> int:
        self.calls.append("missions")
        return self.missions


class CollectDailyTests(unittest.TestCase):
    def test_collects_in_the_original_auto_flow_order(self):
        # memory 05: daily login bonus -> gifts -> missions after login
        gateway = RecordingGateway(login_bonus=True, gifts=3, missions=5)
        report = CollectDaily(gateway).execute()
        self.assertEqual(report, DailyReport(login_bonus=True, gifts=3, missions=5))
        self.assertEqual(gateway.calls, ["login_bonus", "gifts", "missions"])

    def test_report_reflects_a_failed_login_bonus(self):
        gateway = RecordingGateway(login_bonus=False, gifts=0, missions=2)
        report = CollectDaily(gateway).execute()
        self.assertEqual(report, DailyReport(login_bonus=False, gifts=0, missions=2))
        self.assertEqual(gateway.calls, ["login_bonus", "gifts", "missions"])


if __name__ == "__main__":
    unittest.main()
