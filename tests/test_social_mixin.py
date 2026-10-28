"""Social + battle mixin (friends/supporters, missions beyond accept,
apologies, stage dropout, origin/z/kobetu battles, rankings) against a
fake game server.

In-file copy of the FakeGameServer pattern from tests/test_daily.py: a
threaded http.server speaking the recovered envelope (data/sign form
fields, JSON responses sealed as ``{"sign": ...}``). Wire facts from
memories 02 and 12.
"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from dokkan.adapters.envelope import Envelope, envelope_secrets
from dokkan.adapters.gateway_social import KOBETU_COMMANDS
from dokkan.adapters.http_gateway import HttpGateway
from dokkan.adapters.save_store import SaveRecord


class FakeGameServer:
    """Minimal game API speaking the recovered envelope (test-local copy)."""

    def __init__(self, client: str = "global") -> None:
        self.client = client
        self.request_env = Envelope(*envelope_secrets(client, "request"))
        self.response_env = Envelope(*envelope_secrets(client, "response"))
        self.requests: list[tuple[str, str, dict, dict]] = []  # (method, path, query, payload)

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
        if path == "/friendships":
            return {"friends": []}
        if path == "/messages":
            return {"messages": []}
        if path == "/members/":
            return {"members": []}
        if path == "/supporters":
            return {"supporters": []}
        if path == "/support_leaders":
            return {"support_leaders": []}
        if path == "/missions":
            return {"missions": []}
        if path == "/missions/finish":
            return {"success": True}
        if path == "/missions/put_forward":
            return {"success": True}
        if path == "/apologies/accept":
            return {"success": True}
        if path.startswith("/stages/") and path.endswith("/dropout"):
            return {"success": True}
        if path == "/origin_battles/briefing":
            return {"briefing": {}}
        if path.startswith("/origin_battles/"):
            return {"success": True}
        if path.startswith("/z_battles/") and path.endswith("/briefing"):
            return {"briefing": {}}
        if path.startswith("/z_battles/"):
            return {"success": True}
        if path.endswith("/resume") and path.startswith("/kobetu_battles/"):
            return {"room_state": "opaque-protobuf-bytes-placeholder"}
        if path.endswith("/transit_round") and path.startswith("/kobetu_battles/"):
            return {"success": True}
        if path.startswith("/kobetu_battles/"):
            return {"success": True}
        if path == "/rankings":
            return {"rankings": []}
        raise AssertionError(f"unexpected path {path}")

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self.thread.join(timeout=5)


class SocialBattleMixinTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeGameServer()
        self.gateway = HttpGateway(
            SaveRecord("kyle", "at", "s", "global", user_id=42),
            versions=None, base_url=self.server.url())

    def tearDown(self):
        self.server.close()

    def _last(self):
        """(method, path, query, payload) of the most recent request."""
        return self.server.requests[-1]

    # -- social ---------------------------------------------------------------
    def test_friends(self):
        self.assertEqual(self.gateway.friends(), {"friends": []})
        method, path, query, _ = self._last()
        self.assertEqual((method, path, query), ("GET", "/friendships", {}))

    def test_messages(self):
        self.assertEqual(self.gateway.messages(), {"messages": []})
        method, path, query, _ = self._last()
        self.assertEqual((method, path, query), ("GET", "/messages", {}))

    def test_members_keeps_the_verbatim_trailing_slash(self):
        self.assertEqual(self.gateway.members(), {"members": []})
        method, path, query, _ = self._last()
        self.assertEqual((method, path, query), ("GET", "/members/", {}))

    def test_supporters_without_difficulty_sends_no_params(self):
        self.gateway.supporters()
        method, path, query, _ = self._last()
        self.assertEqual((method, path), ("GET", "/supporters"))
        self.assertEqual(query, {})

    def test_supporters_difficulty_param_shape(self):
        self.gateway.supporters(difficulty=3)
        method, path, query, _ = self._last()
        self.assertEqual((method, path), ("GET", "/supporters"))
        # the verbatim ?difficulty= pattern (memory 12)
        self.assertEqual(query, {"difficulty": "3"})

    def test_support_leaders(self):
        self.gateway.support_leaders()
        method, path, query, _ = self._last()
        self.assertEqual((method, path, query), ("GET", "/support_leaders", {}))

    # -- missions -------------------------------------------------------------
    def test_list_missions_without_categories(self):
        self.gateway.list_missions()
        method, path, query, _ = self._last()
        self.assertEqual((method, path), ("GET", "/missions"))
        self.assertEqual(query, {})

    def test_list_missions_category_ids_use_the_verbatim_array_key(self):
        self.gateway.list_missions([1, 2])
        method, path, query, _ = self._last()
        self.assertEqual((method, path), ("GET", "/missions"))
        # memory 12's verbatim key shape: the Rails-style array-suffixed
        # 'mission_category_ids[]', carrying one value per id. The shared
        # _call transport urlencodes its params dict in a single pass, so
        # the id list reaches the wire as one bracketed value; true per-id
        # repetition would need doseq encoding in _call.
        self.assertEqual(list(query), ["mission_category_ids[]"])
        self.assertEqual(query["mission_category_ids[]"], "[1, 2]")

    def test_finish_mission_posts_the_id(self):
        self.gateway.finish_mission(7)
        method, path, query, payload = self._last()
        self.assertEqual((method, path), ("POST", "/missions/finish"))
        self.assertEqual(payload["mission_id"], 7)

    def test_put_forward_mission_posts_the_id(self):
        self.gateway.put_forward_mission(8)
        method, path, query, payload = self._last()
        self.assertEqual((method, path), ("POST", "/missions/put_forward"))
        self.assertEqual(payload["mission_id"], 8)

    # -- apologies -------------------------------------------------------------
    def test_accept_apology(self):
        self.assertEqual(self.gateway.accept_apology(), {"success": True})
        method, path, query, _ = self._last()
        self.assertEqual((method, path, query), ("POST", "/apologies/accept", {}))

    # -- stage dropout -----------------------------------------------------------
    def test_dropout_interpolates_the_stage_id(self):
        self.gateway.dropout(123)
        method, path, query, _ = self._last()
        self.assertEqual((method, path, query), ("POST", "/stages/123/dropout", {}))

    # -- origin battles ----------------------------------------------------------
    def test_origin_battle_briefing_selects_the_origin_battle_id_key(self):
        self.gateway.origin_battle_briefing(origin_battle_id=9)
        method, path, query, _ = self._last()
        self.assertEqual((method, path), ("GET", "/origin_battles/briefing"))
        self.assertEqual(query, {"origin_battle_id": "9"})

    def test_origin_battle_briefing_selects_the_stage_id_key(self):
        self.gateway.origin_battle_briefing(stage_id=77)
        method, path, query, _ = self._last()
        self.assertEqual((method, path), ("GET", "/origin_battles/briefing"))
        self.assertEqual(query, {"stage_id": "77"})

    def test_origin_battle_briefing_with_both_ids_sends_both_keys(self):
        self.gateway.origin_battle_briefing(origin_battle_id=9, stage_id=77)
        method, path, query, _ = self._last()
        self.assertEqual((method, path), ("GET", "/origin_battles/briefing"))
        self.assertEqual(query, {"origin_battle_id": "9", "stage_id": "77"})

    def test_origin_battle_briefing_with_no_ids_sends_no_params(self):
        self.gateway.origin_battle_briefing()
        method, path, query, _ = self._last()
        self.assertEqual((method, path), ("GET", "/origin_battles/briefing"))
        self.assertEqual(query, {})

    def test_origin_battle_ops_are_posts_on_the_interpolated_path(self):
        for op in ("start", "finish", "retire"):
            self.gateway.origin_battle(op, 5)
            method, path, _, _ = self._last()
            self.assertEqual((method, path), ("POST", f"/origin_battles/5/{op}"))

    def test_origin_battle_passes_the_payload_through(self):
        self.gateway.origin_battle("finish", 5, payload={"elapsed_time": 42})
        _, _, _, payload = self._last()
        self.assertEqual(payload["elapsed_time"], 42)

    # -- z battles ----------------------------------------------------------------
    def test_z_battle_briefing_is_a_get_with_params_passed_through(self):
        self.gateway.z_battle("briefing", 78, params={
            "difficulty": 1, "force_update": "true", "team_num": 6})
        method, path, query, _ = self._last()
        self.assertEqual((method, path), ("GET", "/z_battles/78/briefing"))
        self.assertEqual(query, {"difficulty": "1", "force_update": "true", "team_num": "6"})

    def test_z_battle_start_and_finish_are_posts(self):
        for op in ("start", "finish"):
            self.gateway.z_battle(op, 78)
            method, path, _, _ = self._last()
            self.assertEqual((method, path), ("POST", f"/z_battles/78/{op}"))

    # -- kobetu battles --------------------------------------------------------------
    def test_kobetu_commands_use_the_verbatim_command_strings(self):
        for command in KOBETU_COMMANDS:
            self.gateway.kobetu_command(9, command)
            method, path, _, _ = self._last()
            self.assertEqual((method, path),
                             ("POST", f"/kobetu_battles/9/commands/{command}"))

    def test_kobetu_command_passes_the_payload_through(self):
        self.gateway.kobetu_command(9, "take_energy_ball", payload={"ball": 3})
        _, _, _, payload = self._last()
        self.assertEqual(payload["ball"], 3)

    def test_kobetu_resume_is_a_bare_get(self):
        result = self.gateway.kobetu_resume(9)
        method, path, _, _ = self._last()
        self.assertEqual((method, path), ("GET", "/kobetu_battles/9/resume"))
        # The nested battle-state field is opaque protobuf (memory 12
        # caveat) — only assert the call shape, not the field's meaning.
        self.assertIn("room_state", result)

    def test_kobetu_transit_round_is_a_bare_post(self):
        self.gateway.kobetu_transit_round(9)
        method, path, _, _ = self._last()
        self.assertEqual((method, path), ("POST", "/kobetu_battles/9/transit_round"))

    # -- rankings ----------------------------------------------------------------
    def test_rankings(self):
        self.assertEqual(self.gateway.rankings(), {"rankings": []})
        method, path, query, _ = self._last()
        self.assertEqual((method, path, query), ("GET", "/rankings", {}))


if __name__ == "__main__":
    unittest.main()
