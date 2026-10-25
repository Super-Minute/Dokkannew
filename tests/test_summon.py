"""Summon slice: SummonMixin over a fake envelope server + SummonCards loops.

The fake server is the house pattern from tests/test_http_gateway.py
(threaded http.server speaking the recovered envelope) with the gasha
routes added. Use-case loop logic is tested against an in-memory fake
gateway (no HTTP) plus one end-to-end pass through the real HttpGateway.
"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from dokkan.adapters.envelope import Envelope, envelope_secrets
from dokkan.adapters.http_gateway import HttpGateway
from dokkan.adapters.save_store import SaveRecord
from dokkan.application.errors import ApplicationError, InvalidRuns
from dokkan.application.use_cases.summon_cards import (
    InvalidTargets,
    SummonCards,
    parse_targets,
)
from dokkan.domain.models import Account, Banner, Card, CardType, Rarity, Stamina

USER_PAYLOAD = {
    "user": {"name": "Testy", "act": 40, "act_max": 50, "card_capacity": 100}
}
CARDS_PAYLOAD = {
    "cards": [
        {"id": 1, "name": "Hero", "rarity": 5, "type": "STR", "level": 80, "attack": 9000},
        {"id": 2, "name": "Recruit", "rarity": 0, "type": "AGL", "level": 1, "attack": 100},
    ]
}
GASHAS_PAYLOAD = {
    "gashas": [
        {"id": 2196, "name": "Legendary Warriors"},
        {"gasha_id": 5, "title": "Friend Summon"},
    ]
}
MULTI_DRAW = {
    "cards": [
        {"id": 1034010, "name": "Goku", "rarity": 5, "type": "STR", "level": 120,
         "attack": 15000, "locked": False, "favorite": True},
        {"id": 1034020, "name": "Vegeta", "rarity": 4, "type": "AGL", "level": 100,
         "attack": 12000},
    ]
}
# Exercises the documented-unconfirmed fallback candidate keys.
ALTERNATE_DRAW = {
    "results": [
        {"card_id": 1001230, "card_name": "Piccolo", "base_rarity": 3,
         "element": "INT", "lv": 10, "atk": 500},
    ]
}


class FakeGameServer:
    """Minimal game API speaking the recovered envelope (house pattern)."""

    def __init__(self, client: str = "global") -> None:
        self.client = client
        self.request_env = Envelope(*envelope_secrets(client, "request"))
        self.response_env = Envelope(*envelope_secrets(client, "response"))
        self.requests: list[tuple[str, str, dict, dict]] = []  # (method, path, query, payload)
        self.raw_bodies: list[bytes] = []
        self.version_rejections = 0
        self.state = {"user": dict(USER_PAYLOAD["user"]),
                      "cards": [dict(c) for c in CARDS_PAYLOAD["cards"]]}
        self.draw_rounds: list[dict] = []  # scripted /gashas/{id}/draw responses

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
        if path == "/resources/login":
            return {"user": self.state["user"], "cards": self.state["cards"], "missions": []}
        if path == "/user/capacity/card":
            return {"capacity": 100}
        if path == "/gashas":
            return {"gashas": [dict(b) for b in GASHAS_PAYLOAD["gashas"]]}
        if path.startswith("/gashas/") and path.endswith("/draw"):
            if self.draw_rounds:
                return self.draw_rounds.pop(0)
            return {"cards": [dict(c) for c in MULTI_DRAW["cards"]]}
        raise AssertionError(f"unexpected path {path}")

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self.thread.join(timeout=5)


class StubVersions:
    def __init__(self, code: str = "6.6.0-abc") -> None:
        self.code = code
        self.refreshes = 0

    def refresh(self) -> None:
        self.refreshes += 1


class SummonMixinTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeGameServer()
        self.versions = StubVersions()
        self.gateway = HttpGateway(
            SaveRecord("kyle", "at", "s", "global", user_id=42),
            versions=self.versions, base_url=self.server.url())

    def tearDown(self):
        self.server.close()

    def _requests(self, path: str):
        return [r for r in self.server.requests if r[1] == path]

    def test_list_banners_parses_gashas_payload(self):
        banners = self.gateway.list_banners()
        self.assertEqual(banners, [Banner(2196, "Legendary Warriors"),
                                   Banner(5, "Friend Summon")])
        self.assertEqual(len(self._requests("/gashas")), 1)
        self.assertEqual(self._requests("/gashas")[0][0], "GET")

    def test_summon_multi_count_2_issues_exactly_two_posts(self):
        cards = self.gateway.summon(2196, True, 2)
        draws = self._requests("/gashas/2196/draw")
        self.assertEqual(len(draws), 2)
        self.assertTrue(all(r[0] == "POST" for r in draws))
        self.assertEqual([c.id for c in cards],
                         [1034010, 1034020, 1034010, 1034020])
        goku = cards[0]
        self.assertEqual(goku.name, "Goku")
        self.assertEqual(goku.rarity, Rarity.LR)
        self.assertEqual(goku.type, CardType.STR)
        self.assertEqual(goku.level, 120)
        self.assertEqual(goku.attack, 15000)
        self.assertFalse(goku.locked)
        self.assertTrue(goku.favorite)
        self.assertEqual(cards[1].rarity, Rarity.UR)
        self.assertFalse(cards[1].locked)
        self.assertFalse(cards[1].favorite)

    def test_summon_defaults_to_one_round_and_carries_the_flag(self):
        cards = self.gateway.summon(2196, True)
        self.assertEqual(len(self._requests("/gashas/2196/draw")), 1)
        self.assertEqual(len(cards), 2)
        payload = self._requests("/gashas/2196/draw")[0][3]
        self.assertEqual(payload.get("gasha_type"), "multi")
        # identity is still sealed into the envelope by _call
        self.assertEqual(payload.get("identifier"), "kyle")
        self.assertEqual(payload.get("access_token"), "at")
        self.gateway.summon(2196, False)
        self.assertEqual(self._requests("/gashas/2196/draw")[1][3].get("gasha_type"),
                         "single")

    def test_summon_maps_alternate_candidate_keys(self):
        self.server.draw_rounds = [ALTERNATE_DRAW]
        cards = self.gateway.summon(2196, False, 1)
        self.assertEqual(len(cards), 1)
        piccolo = cards[0]
        self.assertEqual((piccolo.id, piccolo.name), (1001230, "Piccolo"))
        self.assertEqual(piccolo.rarity, Rarity.SSR)
        self.assertEqual(piccolo.type, CardType.INT)
        self.assertEqual(piccolo.level, 10)
        self.assertEqual(piccolo.attack, 500)

    def test_summon_error_payload_raises(self):
        self.server.draw_rounds = [
            {"error": "out_of_stones", "message": "not enough dragon stones"}]
        with self.assertRaises(ApplicationError):
            self.gateway.summon(2196, True, 1)


class FakeSummonGateway:
    """In-memory GameGateway: scripted rounds, no HTTP."""

    def __init__(self, owned=(), rounds=(), on_empty: str = "refuse") -> None:
        self.owned_ids = list(owned)      # card ids owned before summoning
        self.rounds = list(rounds)        # per round: card-id list or Exception
        self.on_empty = on_empty          # "refuse" (raise) or "filler" cards
        self.fetches = 0
        self.calls: list[tuple[int, bool, int]] = []

    def fetch_account(self) -> Account:
        self.fetches += 1
        return Account(
            name="Testy", stamina=Stamina(10, 10),
            cards=tuple(Card(id=i, name=f"owned {i}", rarity=Rarity.UR,
                             type=CardType.STR) for i in self.owned_ids))

    def summon(self, banner_id: int, multi: bool, count: int = 1):
        self.calls.append((banner_id, multi, count))
        drawn = []
        for _ in range(count):
            if self.rounds:
                step = self.rounds.pop(0)
            elif self.on_empty == "filler":
                step = [900001]
            else:
                raise ApplicationError("game refused the draw")
            if isinstance(step, BaseException):
                raise step
            drawn += [Card(id=i, name=f"pulled {i}", rarity=Rarity.UR,
                           type=CardType.STR) for i in step]
        return drawn


class ParseTargetsTests(unittest.TestCase):
    def test_happy_path_with_default_and_explicit_counts(self):
        self.assertEqual(parse_targets("1034010x2,1034020x1"),
                         {1034010: 2, 1034020: 1})
        self.assertEqual(parse_targets("1001x2,1002"), {1001: 2, 1002: 1})
        self.assertEqual(parse_targets(" 1001 x 3 , 1002 "), {1001: 3, 1002: 1})

    def test_empty_list_is_invalid(self):
        for text in ("", "   "):
            with self.assertRaises(InvalidTargets):
                parse_targets(text)

    def test_malformed_items_are_invalid(self):
        for text in ("foo", "1001x", "x2", "1001xfoo", "1001x0", "1001x-1",
                     "1001,,1002", "1001x2x3", "-5"):
            with self.assertRaises(InvalidTargets):
                parse_targets(text)

    def test_repeated_ids_accumulate(self):
        self.assertEqual(parse_targets("1001x2,1001"), {1001: 3})


class SummonCardsTests(unittest.TestCase):
    def test_no_targets_runs_exactly_count_rounds(self):
        gw = FakeSummonGateway(rounds=[[1, 2], [3], [4, 5, 6]])
        report = SummonCards(gw).execute(2196, True, 3)
        self.assertEqual((report.rounds, report.drawn, report.targets_met,
                          report.progress), (3, 6, True, ()))
        self.assertEqual(gw.calls, [(2196, True, 1), (2196, True, 1), (2196, True, 1)])
        self.assertEqual(gw.fetches, 0)  # no targets -> no account fetch

    def test_count_999_is_capped_at_1000_rounds(self):
        gw = FakeSummonGateway(rounds=[[1]], on_empty="filler")
        report = SummonCards(gw).execute(2196, True, 999)
        self.assertEqual(report.rounds, 1000)
        self.assertEqual(report.drawn, 1000)

    def test_negative_count_is_rejected(self):
        with self.assertRaises(InvalidRuns):
            SummonCards(FakeSummonGateway()).execute(2196, True, -1)

    def test_targets_subtract_owned_copies_then_stop_when_met(self):
        gw = FakeSummonGateway(owned=[1001, 1001, 1002], rounds=[[1001], [999]])
        report = SummonCards(gw).execute(2196, True, 999,
                                         targets={1001: 3, 1002: 1})
        # owned: 2 of the 3 wanted 1001s and the single 1002 -> need just one 1001
        self.assertEqual(report.rounds, 1)
        self.assertEqual(report.drawn, 1)
        self.assertTrue(report.targets_met)
        self.assertEqual(report.progress, ((1001, 0), (1002, 0)))
        self.assertEqual(gw.fetches, 1)  # fetched once, before the first round
        self.assertEqual(len(gw.calls), 1)

    def test_fully_pre_owned_targets_skip_summoning(self):
        gw = FakeSummonGateway(owned=[1001], rounds=[[999]])
        report = SummonCards(gw).execute(2196, True, 5, targets={1001: 1})
        self.assertEqual((report.rounds, report.drawn, report.targets_met),
                         (0, 0, True))
        self.assertEqual(report.progress, ((1001, 0),))
        self.assertEqual(gw.calls, [])

    def test_partial_failure_returns_partial_report(self):
        gw = FakeSummonGateway(rounds=[[1001], ApplicationError("out of stones")])
        report = SummonCards(gw).execute(2196, True, 999, targets={1001: 3})
        self.assertEqual((report.rounds, report.drawn), (1, 1))
        self.assertFalse(report.targets_met)
        self.assertEqual(report.progress, ((1001, 2),))

    def test_no_targets_partial_failure_returns_partial_report(self):
        gw = FakeSummonGateway(
            rounds=[[1, 2], [3], ApplicationError("out of stones"), [4]])
        report = SummonCards(gw).execute(2196, True, 5)
        self.assertEqual((report.rounds, report.drawn, report.targets_met),
                         (2, 3, True))


class SummonEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeGameServer()
        self.versions = StubVersions()
        self.gateway = HttpGateway(
            SaveRecord("kyle", "at", "s", "global", user_id=42),
            versions=self.versions, base_url=self.server.url())

    def tearDown(self):
        self.server.close()

    def test_target_summon_through_the_real_gateway(self):
        # account owns one copy of card 1 (server state) -> needs one more
        self.server.draw_rounds = [
            {"cards": [{"id": 1, "name": "Hero", "rarity": 5, "type": "STR"}]}]
        report = SummonCards(self.gateway).execute(2196, True, 999, targets={1: 2})
        self.assertEqual(report.rounds, 1)
        self.assertEqual(report.drawn, 1)
        self.assertTrue(report.targets_met)
        self.assertEqual(report.progress, ((1, 0),))
        draws = [r for r in self.server.requests if r[1] == "/gashas/2196/draw"]
        self.assertEqual(len(draws), 1)
        logins = [r for r in self.server.requests
                  if r[1].startswith("/resources/login")]
        self.assertEqual(len(logins), 1)  # fetched once before the first round

    def test_partial_failure_through_the_real_gateway(self):
        self.server.draw_rounds = [
            {"cards": [{"id": 1034010, "name": "Goku", "rarity": 5, "type": "STR"}]},
            {"error": "out_of_stones", "message": "not enough dragon stones"},
            {"cards": [{"id": 1034010, "name": "Goku", "rarity": 5, "type": "STR"}]},
        ]
        report = SummonCards(self.gateway).execute(2196, True, 5)
        self.assertEqual((report.rounds, report.drawn), (1, 1))
        self.assertEqual(len([r for r in self.server.requests
                              if r[1] == "/gashas/2196/draw"]), 2)


if __name__ == "__main__":
    unittest.main()
