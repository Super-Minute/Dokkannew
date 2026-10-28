import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from dokkan.adapters.json_settings import load_settings
from dokkan.adapters.simulated_gateway import SimulatedGateway
from dokkan.application.errors import ConfigError
from dokkan.application.settings import Settings
from dokkan.application.use_cases.find_stage import FindStage, StageNotFound
from dokkan.application.use_cases.summon_cards import InvalidTargets
from dokkan.cli.commands import CommandRunner, UsageError
from dokkan.cli.main import main
from test_http_gateway import FakeGameServer, header


def run(*lines, gateway=None, settings=None) -> str:
    out = io.StringIO()
    runner = CommandRunner(gateway or SimulatedGateway(), settings or Settings(), out)
    for line in lines:
        runner.run_line(line)
    return out.getvalue()


class SettingsLoaderTests(unittest.TestCase):
    def test_missing_file_gives_defaults(self):
        self.assertEqual(load_settings(Path("/nonexistent.json")), Settings())

    def test_reads_known_keys_and_ignores_the_rest(self):
        cfg = {"stamina": {"prefer_meat": True, "use_stone": False},
               "auto_sell": {"enabled": False}, "game": {"team_build": False},
               "proxy": {"enabled": True}}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text(json.dumps(cfg))
            s = load_settings(p)
        self.assertEqual((s.stamina.prefer_meat, s.stamina.use_stone, s.auto_sell, s.team_build),
                         (True, False, False, False))


class SettingsErrorTests(unittest.TestCase):
    def _load(self, text: str):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text(text)
            return load_settings(p)

    def test_bad_configs_raise_config_error(self):
        for text in ("{not json", "[]", '{"stamina": null}', '{"auto_sell": true}',
                     '{"stamina": {"use_stone": "false"}}'):
            with self.subTest(text), self.assertRaises(ConfigError):
                self._load(text)


class SimulatedGatewayTests(unittest.TestCase):
    def test_find_stage(self):
        self.assertEqual(FindStage(SimulatedGateway()).execute("1001").name, "Easy Field")
        with self.assertRaises(StageNotFound):
            FindStage(SimulatedGateway()).execute("nope")


class CommandTests(unittest.TestCase):
    def test_info_and_events(self):
        out = run("info", "events")
        self.assertIn("stamina 30/50", out)
        self.assertIn("1002 Z-Hard Field", out)

    def test_farm_refills_and_auto_sells(self):
        gw = SimulatedGateway()  # 30 stamina, cost 6/run: needs a refill; box cap 6: needs a sale
        out = run("farm 1001 8", gateway=gw)
        self.assertIn("8 runs, 8 cleared", out)
        self.assertNotIn(" 0 sold", out)
        # selling happens before each run, so at most one fresh drop sits above capacity
        self.assertLessEqual(len(gw.fetch_account().cards), 6 + 1)

    def test_sell_auto(self):
        self.assertIn("sold 2 cards", run("sell auto"))

    def test_comments_blank_lines_and_errors(self):
        self.assertEqual(run("", "# comment"), "")
        for bad in ("bogus", "sell now", "farm 1001", "farm 1001 x"):
            with self.subTest(bad), self.assertRaises(UsageError):
                run(bad)

    def test_parse_errors_are_usage_errors(self):
        for bad in ('sell "auto', "farm 1001 \u00b2", "farm 1001 -1"):
            with self.subTest(bad), self.assertRaises(UsageError):
                run(bad)

    def test_summons_and_plain_summon_rounds(self):
        out = run("summons")
        self.assertIn("1 Simulated Legends", out)
        self.assertIn("2 Simulated Ticket", out)
        out = run("summon 1 m 2")
        self.assertIn("2 rounds, 14 drawn", out)
        out = run("summon 1 s 3")
        self.assertIn("3 rounds, 3 drawn", out)

    def test_summon_targets_count_owned_copies(self):
        # card id 1 ("Hero") is already owned: seeking 1x1 needs nothing
        out = run("summon 1 m 1x1")
        self.assertIn("0 rounds, 0 drawn, targets met", out)
        # an id the simulated gateway never draws stays unmet at the round cap
        out = run("summon 1 s 999999x2")
        self.assertIn("1000 rounds, 1000 drawn, targets unmet", out)
        self.assertIn("999999: need 2 more", out)

    def test_summon_target_reached_mid_loop_stops(self):
        # the simulated gateway draws ids 100, 101, ... one copy each;
        # both targets are met after exactly two rounds (cap is 1000)
        out = run("summon 1 s 100x1,101x1")
        self.assertIn("2 rounds, 2 drawn, targets met", out)

    def test_daily_collects_everything(self):
        out = run("daily")
        self.assertIn("login bonus: claimed, 3 gifts, 2 missions", out)

    def test_board_runs_and_reports(self):
        self.assertIn("board 3: cleared", run("board 3"))
        for bad in ("board", "board x", "board 1 2", "board 0x1"):
            with self.subTest(bad), self.assertRaises(UsageError):
                run(bad)

    def test_links_displays_verbatim_states(self):
        out = run("links")
        self.assertIn("Google: LINKED", out)
        self.assertIn("Apple: UNLINKED", out)
        self.assertIn("Facebook: UNLINKED", out)
        self.assertIn("Game Center: - NOT AVAILABLE", out)
        for bad in ("links extra",):
            with self.subTest(bad), self.assertRaises(UsageError):
                run(bad)

    def test_new_command_usage_errors(self):
        from dokkan.application.errors import ApplicationError
        for bad in ("summons extra", "summon", "summon 1", "summon 1 m",
                    "summon 1 x 2", "summon one m 2", "summon 1 m -1",
                    "summon 1 m not-a-target", "daily now"):
            with self.subTest(bad), self.assertRaises((UsageError, ApplicationError)):
                run(bad)


class MainTests(unittest.TestCase):
    def _main(self, *argv, files=None):
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            for name, text in (files or {}).items():
                (Path(d) / name).write_text(text)
            argv = [a.replace("{d}", d) for a in argv]
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = main(["--config", f"{d}/none.json", *argv])
                except SystemExit as exc:  # argparse errors
                    code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_script_success(self):
        code, out, _ = self._main("--script", "{d}/s.txt", files={"s.txt": "info\nfarm 1001 1\n"})
        self.assertEqual(code, 0)
        self.assertIn("1 runs, 1 cleared", out)

    def test_script_failure_reports_line(self):
        code, _, err = self._main("--script", "{d}/s.txt", files={"s.txt": "info\nbogus\n"})
        self.assertEqual(code, 1)
        self.assertIn("s.txt:2: unknown command: bogus", err)

    def test_missing_script_and_unknown_stage(self):
        code, _, err = self._main("--script", "{d}/missing.txt")
        self.assertEqual((code, "cannot read script" in err), (1, True))
        code, _, err = self._main("farm", "zzz", "1")
        self.assertEqual((code, err.strip()), (1, "error: zzz"))

    def test_malformed_config_is_clean_error(self):
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "c.json"
            cfg.write_text("{oops")
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["--config", str(cfg), "info"])
        self.assertEqual(code, 1)
        self.assertIn("cannot read", err.getvalue())

    def test_no_command_is_argparse_error(self):
        code, _, _ = self._main()
        self.assertEqual(code, 2)


class GatewaySelectionTests(unittest.TestCase):
    SAVE = json.dumps({"identifier": "kyle", "access_token": "at", "secret": "s",
                       "user_id": 42, "client": "japan"})

    def _main(self, server, *argv, save_text: str = SAVE, files=None):
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            save = Path(d) / "save.json"
            save.write_text(save_text)
            # zero client delays: these tests exercise wiring, not pacing
            (Path(d) / "zero.json").write_text(json.dumps(
                {"client_delays": {"global": {"min": 0, "max": 0},
                                   "japan": {"min": 0, "max": 0}}}))
            for name, text in (files or {}).items():
                (Path(d) / name).write_text(text)
            argv = [a.replace("{d}", d) for a in argv]
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["--config", f"{d}/zero.json", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_http_gateway_wires_save_and_reaches_api(self):
        server = FakeGameServer()
        try:
            code, out, err = self._main(
                server, "--gateway", "http", "--save", "{d}/save.json",
                "--client", "glb", "--api-url", server.url(), "info")
            self.assertEqual((code, err), (0, ""))
            self.assertIn("Testy: stamina 40/50, 2 cards", out)
        finally:
            server.close()

    def test_http_gateway_requires_save(self):
        code, out, err = self._main(None, "--gateway", "http", "info")
        self.assertEqual(code, 1)
        self.assertIn("--save", err)

    def test_client_flag_overrides_save_client(self):
        # The save says japan; --client glb must make the gateway speak the
        # global envelope (the default-client fake server would reject JP).
        server = FakeGameServer()
        try:
            code, out, err = self._main(
                server, "--gateway", "http", "--save", "{d}/save.json",
                "--client", "glb", "--api-url", server.url(), "info")
            self.assertEqual(code, 0)
        finally:
            server.close()

    def test_ping_command_ok_over_http_and_unreachable_reported(self):
        server = FakeGameServer()
        try:
            code, out, err = self._main(
                server, "--gateway", "http", "--save", "{d}/save.json",
                "--api-url", server.url(), "ping")
            self.assertEqual((code, err), (0, ""))
            self.assertIn("ok", out)
        finally:
            server.close()
        code, out, err = self._main(
            None, "--gateway", "http", "--save", "{d}/save.json",
            "--api-url", "http://127.0.0.1:1", "ping")
        self.assertEqual(code, 1)
        self.assertIn("unreachable", err)

    def test_version_rejection_triggers_remote_refresh_and_retry(self):
        from test_version_store import _FeedServer
        server = FakeGameServer()
        server.version_rejections = 1
        new_code = "9.9.9-" + "d" * 64
        try:
            with _FeedServer(f"version_code_global={new_code}\n".encode()) as feed:
                code, out, err = self._main(
                    server, "--gateway", "http", "--save", "{d}/save.json",
                    "--client", "glb", "--api-url", server.url(),
                    "--version-url", feed, "--version-file", "{d}/version.json", "info")
                self.assertEqual((code, err), (0, ""))
                self.assertIn("Testy: stamina 40/50, 2 cards", out)
            # the retried request carried the freshly fetched client version
            versions = [header(h, "X-ClientVersion") for h in server.request_headers]
            self.assertIn(new_code, versions)
        finally:
            server.close()

    def test_simulated_ping_and_default_gateway(self):
        code, out, err = self._main(None, "ping")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("ok", out)

    def test_server_taught_version_cached_when_not_in_store(self):
        # no --version-url and an empty cache: the game server's rejection
        # teaches the code, the retry carries it, and it lands in version.json
        server = FakeGameServer()
        server.version_rejections = 1
        try:
            code, out, err = self._main(
                server, "--gateway", "http", "--save", "{d}/save.json",
                "--client", "glb", "--api-url", server.url(),
                "--version-file", "{d}/version.json", "info")
            self.assertEqual((code, err), (0, ""))
            self.assertIn("Testy: stamina 40/50, 2 cards", out)
            versions = [header(h, "X-ClientVersion") for h in server.request_headers]
            self.assertEqual(versions[-1], "9.9.9")
        finally:
            server.close()

    def test_http_board_wiring(self):
        server = FakeGameServer()

        def route(path, payload):
            if path == "/gashas":
                return {"gashas": [{"id": 5, "name": "Fake Banner"}]}
            if path.startswith("/gashas/"):
                return {"cards": [{"id": 77, "name": "Pulled", "rarity": 3,
                                   "type": "INT", "level": 1, "attack": 10}]}
            if path == "/gifts":
                return {"gifts": [{"id": 11}, {"id": 12}]}
            if path == "/gifts/accept":
                return {"success": True}
            if path == "/missions":
                return {"missions": [{"id": 31}]}
            if path == "/missions/accept":
                return {"success": True}
            if path == "/login_bonuses/accept":
                return {"success": True}
            if path.startswith("/resources/home"):
                return {}  # no random login bonus pending
            if path.startswith("/sugoroku_maps/") and path.endswith("/start"):
                return {"success": True}
            if path.startswith("/sugoroku_maps/") and path.endswith("/finish"):
                return {"is_cleared": True}
            raise AssertionError(path)

        server.route = route
        try:
            code, out, err = self._main(
                server, "--gateway", "http", "--save", "{d}/save.json",
                "--client", "glb", "--api-url", server.url(), "board 4")
            self.assertEqual((code, err), (0, ""))
            self.assertIn("board 4: cleared", out)
            boards = [r[1] for r in server.requests if r[1].startswith("/sugoroku_maps/")]
            self.assertEqual(boards, ["/sugoroku_maps/4/start", "/sugoroku_maps/4/finish"])
        finally:
            server.close()

    def test_http_summon_and_daily_wiring(self):
        server = FakeGameServer()

        def route(path, payload):
            if path == "/gashas":
                return {"gashas": [{"id": 5, "name": "Fake Banner"}]}
            if path.startswith("/gashas/"):
                return {"cards": [{"id": 77, "name": "Pulled", "rarity": 3,
                                   "type": "INT", "level": 1, "attack": 10}]}
            if path == "/gifts":
                return {"gifts": [{"id": 11}, {"id": 12}]}
            if path == "/gifts/accept":
                return {"success": True}
            if path == "/missions":
                return {"missions": [{"id": 31}]}
            if path == "/missions/accept":
                return {"success": True}
            if path == "/login_bonuses/accept":
                return {"success": True}
            if path.startswith("/resources/home"):
                return {}  # no random login bonus pending
            raise AssertionError(path)

        server.route = route
        try:
            code, out, err = self._main(
                server, "--gateway", "http", "--save", "{d}/save.json",
                "--client", "glb", "--api-url", server.url(), "summons")
            self.assertEqual((code, err), (0, ""))
            self.assertIn("5 Fake Banner", out)
            code, out, err = self._main(
                server, "--gateway", "http", "--save", "{d}/save.json",
                "--client", "glb", "--api-url", server.url(), "summon 5 m 2")
            self.assertEqual((code, err), (0, ""))
            self.assertIn("2 rounds, 2 drawn", out)
            code, out, err = self._main(
                server, "--gateway", "http", "--save", "{d}/save.json",
                "--client", "glb", "--api-url", server.url(), "daily")
            self.assertEqual((code, err), (0, ""))
            self.assertIn("login bonus: claimed, 2 gifts, 1 missions", out)
        finally:
            server.close()

    def test_login_signs_in_and_refreshes_the_save_file(self):
        server = FakeGameServer()

        def route(path, payload):
            if path == "/auth/nonce":
                return {"auth_transaction_id": "tx-1"}
            if path == "/auth/sign_in":
                assert payload.get("identifier") == "kyle"
                assert payload.get("secret") == "s"
                assert payload.get("auth_transaction_id") == "tx-1"
                return {"access_token": "fresh-at", "user_id": 42}
            raise AssertionError(path)

        server.route = route
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            save = Path(d) / "save.json"
            save.write_text(self.SAVE)
            (Path(d) / "zero.json").write_text(json.dumps(
                {"client_delays": {"global": {"min": 0, "max": 0},
                                   "japan": {"min": 0, "max": 0}}}))
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = main(["--config", f"{d}/zero.json", "--gateway", "http",
                                 "--save", str(save), "--client", "glb",
                                 "--api-url", server.url(), "login"])
            finally:
                server.close()
            refreshed = json.loads(save.read_text())
        self.assertEqual((code, err.getvalue()), (0, ""))
        self.assertIn("signed in: user 42", out.getvalue())
        self.assertEqual(refreshed["access_token"], "fresh-at")
        self.assertEqual(refreshed["user_id"], 42)
        self.assertEqual(refreshed["secret"], "s")

    def test_login_google_with_token_is_verbose_and_writes_save(self):
        server = FakeGameServer()

        def route(path, payload):
            if path == "/user/succeed/google":
                assert payload.get("google_token") == "tok-xyz"
                return {"identifier": "gg-1", "access_token": "at",
                        "secret": "sec", "user_id": 7}
            raise AssertionError(path)

        server.route = route
        out, err = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            save = Path(d) / "save.json"
            (Path(d) / "zero.json").write_text(json.dumps(
                {"client_delays": {"global": {"min": 0, "max": 0},
                                   "japan": {"min": 0, "max": 0}}}))
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = main(["--config", f"{d}/zero.json", "--gateway", "http",
                                 "--save", str(save), "--client", "glb",
                                 "--api-url", server.url(), "--google-token", "tok-xyz",
                                 "login", "google"])
            finally:
                server.close()
            written = json.loads(save.read_text())
        self.assertEqual((code, err.getvalue()), (0, ""))
        text = out.getvalue()
        # the whole step-by-step narration, not just the final line
        self.assertIn("Google sign-in: client=global", text)
        self.assertIn("Using the Google token passed on the command line", text)
        self.assertIn("Submitting the Google token to the Dokkan API", text)
        self.assertIn("Dokkan API accepted the Google token: identifier=gg-1, "
                      "user_id=7", text)
        self.assertIn("signed in with Google: gg-1", text)
        self.assertEqual(written["identifier"], "gg-1")


class RequireGoogleClientSecretTests(unittest.TestCase):
    """Google's token endpoint rejects the code exchange with
    "client_secret is missing." for "Desktop app" OAuth clients even
    under PKCE — confirmed live, 2026-10-26. These test the CLI's own
    get-it-from-somewhere helper directly, same pattern as test_pacing's
    _build_gateway tests.
    """

    def _args(self, d, **overrides):
        from types import SimpleNamespace
        values = dict(config=Path(d) / "c.json", google_client_secret=None)
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_returns_cli_flag_without_prompting(self):
        from dokkan.cli.main import _require_google_client_secret
        with tempfile.TemporaryDirectory() as d:
            args = self._args(d, google_client_secret="shh-flag")
            self.assertEqual(_require_google_client_secret(args), "shh-flag")

    def test_returns_env_var_when_no_flag(self):
        import os
        from dokkan.cli.main import _require_google_client_secret
        with tempfile.TemporaryDirectory() as d:
            os.environ["DOKKAN_GOOGLE_CLIENT_SECRET"] = "shh-env"
            try:
                self.assertEqual(
                    _require_google_client_secret(self._args(d)), "shh-env")
            finally:
                del os.environ["DOKKAN_GOOGLE_CLIENT_SECRET"]

    def test_returns_config_value_when_no_flag_or_env(self):
        from dokkan.cli.main import _require_google_client_secret
        with tempfile.TemporaryDirectory() as d:
            args = self._args(d)
            args.config.write_text(json.dumps({"google_client_secret": "shh-cfg"}))
            self.assertEqual(_require_google_client_secret(args), "shh-cfg")

    def test_raises_helpful_usage_error_without_a_terminal(self):
        from unittest import mock
        from dokkan.cli.main import _require_google_client_secret
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("sys.stdin.isatty", return_value=False):
                with self.assertRaises(UsageError) as exc:
                    _require_google_client_secret(self._args(d))
            message = str(exc.exception)
            self.assertIn("--google-client-secret", message)
            self.assertIn("Desktop app", message)

    def test_prompts_and_remembers_when_missing_with_a_terminal(self):
        from unittest import mock
        from dokkan.cli.main import _require_google_client_secret
        with tempfile.TemporaryDirectory() as d:
            args = self._args(d)
            with mock.patch("sys.stdin.isatty", return_value=True), \
                    mock.patch("builtins.input", return_value="typed-secret"):
                secret = _require_google_client_secret(args)
            self.assertEqual(secret, "typed-secret")
            self.assertEqual(
                json.loads(args.config.read_text())["google_client_secret"],
                "typed-secret")

    def test_blank_prompt_cancels(self):
        from unittest import mock
        from dokkan.cli.main import _require_google_client_secret
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("sys.stdin.isatty", return_value=True), \
                    mock.patch("builtins.input", return_value=""):
                with self.assertRaises(UsageError):
                    _require_google_client_secret(self._args(d))


if __name__ == "__main__":
    unittest.main()
