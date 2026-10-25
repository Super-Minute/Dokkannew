"""Client-delay pacing between game API calls (memory 10, bot_config).

The original bot spaces its calls 20–21 s per client to respect the game
service's rate limits. The cleanroom bot honors the same configured pacing
whenever it talks to the real API; the simulated gateway is offline and stays
unpaced.
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fakes import FakeGateway

from dokkan.adapters.json_settings import load_settings
from dokkan.adapters.pacing import PacedGateway
from dokkan.application.errors import ConfigError, RefillUnavailable
from dokkan.application.ports import RefillSource
from dokkan.application.settings import DelayRange, Settings
from dokkan.domain.models import Stage


class PacedGatewayTests(unittest.TestCase):
    def _paced(self, lo=1.0, hi=2.0):
        self.sleeps = []
        self.inner = FakeGateway()
        self.paced = PacedGateway(self.inner, DelayRange(lo, hi),
                                  sleep=self.sleeps.append, rng=lambda a, b: (a + b) / 2)
        return self.paced

    def test_first_call_is_not_delayed(self):
        self._paced().fetch_account()
        self.assertEqual(self.sleeps, [])

    def test_sleeps_once_between_consecutive_calls(self):
        self._paced(1.0, 2.0)
        self.paced.fetch_account()
        self.paced.fetch_account()
        self.assertEqual(self.sleeps, [1.5])

    def test_every_gateway_call_is_paced(self):
        self._paced(0.0, 0.0)
        self.paced.fetch_account()
        stage = Stage("1", "s", difficulty=1, stamina_cost=10)
        from dokkan.domain.models import Team
        self.paced.list_events()
        self.paced.run_stage(stage, Team((1, 2, 3, 4, 5, 6)))
        self.paced.sell_cards([1, 2])
        with self.assertRaises(RefillUnavailable):
            self.paced.refill_stamina(RefillSource.MEAT)
        self.assertEqual(self.sleeps, [0.0] * 4)  # one gap before each call after the first
        self.assertEqual(len(self.inner.runs), 1)
        self.assertEqual(self.inner.sold, [1, 2])

    def test_failed_call_still_paces_the_next_one(self):
        self._paced(1.0, 1.0)
        with self.assertRaises(RefillUnavailable):
            self.paced.refill_stamina(RefillSource.MEAT)
        self.paced.fetch_account()
        self.assertEqual(self.sleeps, [1.0])

    def test_ping_probe_is_not_paced(self):
        self._paced(5.0, 5.0)
        self.inner.ping = lambda: True
        self.assertTrue(self.paced.ping())
        self.assertEqual(self.sleeps, [])


class ClientDelaySettingsTests(unittest.TestCase):
    def test_defaults_are_the_original_bots_20_to_21_seconds(self):
        delays = Settings().client_delays
        self.assertEqual(delays["global"], DelayRange(20.0, 21.0))
        self.assertEqual(delays["japan"], DelayRange(20.0, 21.0))

    def test_parses_per_client_delays_with_aliases(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "c.json"
            cfg.write_text(json.dumps({"client_delays": {
                "global": {"min": 3, "max": 4}, "jp": {"min": 7, "max": 9}}}))
            delays = load_settings(cfg).client_delays
        self.assertEqual(delays["global"], DelayRange(3.0, 4.0))
        self.assertEqual(delays["japan"], DelayRange(7.0, 9.0))

    def test_partial_section_keeps_defaults_for_other_clients(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "c.json"
            cfg.write_text(json.dumps({"client_delays": {"japan": {"min": 1, "max": 1}}}))
            delays = load_settings(cfg).client_delays
        self.assertEqual(delays["japan"], DelayRange(1.0, 1.0))
        self.assertEqual(delays["global"], DelayRange(20.0, 21.0))

    def test_rejects_bad_delay_sections(self):
        bad = [
            {"client_delays": 5},
            {"client_delays": {"eu": {"min": 1, "max": 2}}},
            {"client_delays": {"global": 5}},
            {"client_delays": {"global": {"min": "soon", "max": 2}}},
            {"client_delays": {"global": {"min": 5, "max": 2}}},
        ]
        for raw in bad:
            with self.subTest(raw), tempfile.TemporaryDirectory() as d:
                cfg = Path(d) / "c.json"
                cfg.write_text(json.dumps(raw))
                with self.assertRaises(ConfigError):
                    load_settings(cfg)


class CliWiringTests(unittest.TestCase):
    SAVE = json.dumps({"identifier": "kyle", "access_token": "at", "secret": "s",
                       "user_id": 42, "client": "japan"})

    def _args(self, d, **overrides):
        values = dict(gateway="http", save=Path(d) / "save.json", client=None,
                      api_url=None, version_file=Path(d) / "version.json",
                      version_url=None)
        values.update(overrides)
        return SimpleNamespace(**values)

    def _settings(self, d, delays=None):
        cfg = Path(d) / "c.json"
        cfg.write_text(json.dumps({"client_delays": delays or {}}))
        return load_settings(cfg)

    def test_http_gateway_is_wrapped_in_pacer_for_the_saves_client(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "save.json").write_text(self.SAVE)
            from dokkan.cli.main import _build_gateway
            gateway = _build_gateway(self._args(d), self._settings(
                d, {"japan": {"min": 3, "max": 4}}))
            self.assertIsInstance(gateway, PacedGateway)
            self.assertEqual(gateway.delay, DelayRange(3.0, 4.0))

    def test_simulated_gateway_is_not_paced(self):
        with tempfile.TemporaryDirectory() as d:
            from dokkan.adapters.simulated_gateway import SimulatedGateway
            from dokkan.cli.main import _build_gateway
            gateway = _build_gateway(self._args(d, gateway="simulated", save=None),
                                     self._settings(d))
            self.assertIsInstance(gateway, SimulatedGateway)

    def test_version_url_builds_remote_fetching_source(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "save.json").write_text(self.SAVE)
            from dokkan.adapters.http_gateway import HttpGateway
            from dokkan.adapters.version_store import FileVersionSource
            from dokkan.cli.main import _build_gateway
            gateway = _build_gateway(self._args(d, version_url="http://127.0.0.1:9/feed"),
                                     self._settings(d))
            self.assertIsInstance(gateway, PacedGateway)
            http = gateway.inner
            self.assertIsInstance(http, HttpGateway)
            self.assertIsInstance(http._versions, FileVersionSource)
            self.assertEqual(http._versions._url, "http://127.0.0.1:9/feed")


if __name__ == "__main__":
    unittest.main()
