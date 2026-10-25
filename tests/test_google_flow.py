"""Google login/transfer flow against a fake envelope-speaking server plus
the one-shot /callback server (memories 02, 03, 05).
"""
import json
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from dokkan.adapters.envelope import Envelope, envelope_secrets
from dokkan.adapters.google_flow import (
    GoogleFlow, GoogleFlowError, TokenCallbackServer, build_auth_url,
)
from dokkan.adapters.save_store import load_save
from pathlib import Path


class FakeAuthServer:
    """Minimal game API speaking the recovered envelope, incl. DELETE."""

    def __init__(self, client: str = "global") -> None:
        self.request_env = Envelope(*envelope_secrets(client, "request"))
        self.response_env = Envelope(*envelope_secrets(client, "response"))
        self.requests: list[tuple[str, str, dict]] = []  # (method, path, payload)
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _respond(self, payload: dict) -> None:
                body = json.dumps({"sign": server.response_env.seal(payload)[0]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle(self, method: str) -> None:
                split = urlsplit(self.path)
                length = int(self.headers.get("Content-Length", 0))
                fields = {k: v[0] for k, v in parse_qs(
                    self.rfile.read(length).decode()).items()}
                payload = server.request_env.open(fields["data"])
                server.requests.append((method, split.path, payload))
                self._respond(server.route(split.path, payload))

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_DELETE(self):
                self._handle("DELETE")

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self.thread.start()

    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def route(self, path: str, payload: dict) -> dict:
        if path == "/user/succeed/google":
            return {"identifier": "gg-1", "access_token": "at",
                    "secret": "sec", "user_id": 7}
        if path == "/user/link/google":
            return {"success": True}
        if path == "/user/links":
            return {"external_links": {"google": "linked", "apple": "unlinked",
                                       "facebook": "unserved"}}
        raise AssertionError(path)

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self.thread.join(timeout=5)


class BuildAuthUrlTests(unittest.TestCase):
    def test_url_carries_oauth_fields(self):
        url = build_auth_url("cid-123", "http://127.0.0.1:9/callback")
        self.assertTrue(url.startswith(
            "https://accounts.google.com/o/oauth2/v2/auth?"))
        for fragment in ("client_id=cid-123", "response_type=code", "scope=openid"):
            self.assertIn(fragment, url)


class TokenCallbackServerTests(unittest.TestCase):
    def test_captures_token_and_replies_verbatim(self):
        with TokenCallbackServer() as callback:
            with urllib.request.urlopen(
                    f"{callback.url}?token=abc-xyz") as response:
                body = response.read()
                self.assertEqual(response.status, 200)
            self.assertEqual(
                body, b"<html><body>Token received. You can close this tab.</body></html>")
            self.assertEqual(callback.wait(timeout=5), "abc-xyz")

    def test_captures_code_when_no_token(self):
        with TokenCallbackServer() as callback:
            urllib.request.urlopen(f"{callback.url}?code=auth-code").read()
            self.assertEqual(callback.wait(timeout=5), "auth-code")

    def test_wait_returns_none_on_timeout(self):
        with TokenCallbackServer() as callback:
            self.assertIsNone(callback.wait(timeout=0.05))

    def test_unknown_path_is_404(self):
        with TokenCallbackServer() as callback:
            try:
                urllib.request.urlopen(f"{callback.url.rsplit('/', 1)[0]}/nope")
                self.fail("expected HTTPError")
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 404)


class GoogleFlowTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeAuthServer()
        self.flow = GoogleFlow("global", base_url=self.server.url())

    def tearDown(self):
        self.server.close()

    def test_succeed_posts_token_and_maps_account(self):
        account = self.flow.succeed("tok-1")
        self.assertEqual((account.identifier, account.access_token,
                          account.secret, account.user_id), ("gg-1", "at", "sec", 7))
        method, path, payload = self.server.requests[-1]
        self.assertEqual((method, path), ("POST", "/user/succeed/google"))
        self.assertEqual(payload.get("google_token"), "tok-1")

    def test_succeed_to_save_round_trips(self):
        import tempfile
        account = self.flow.succeed("tok-1")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "save.json"
            path.write_text(json.dumps({
                **account.to_save("global").__dict__,
                **({"user_id": account.user_id} if account.user_id else {}),
                "identifier": account.identifier, "access_token": account.access_token,
                "secret": account.secret, "client": "global"}))
            loaded = load_save(path)
            self.assertEqual((loaded.identifier, loaded.access_token, loaded.secret),
                             ("gg-1", "at", "sec"))

    def test_succeed_transfer_error_is_verbatim(self):
        self.server.route = lambda path, payload: {"error": "oauth2_bearer_rails/client_transferred"}
        with self.assertRaises(GoogleFlowError) as exc:
            self.flow.succeed("tok")
        self.assertIn("Client transferred", str(exc.exception))

    def test_succeed_missing_fields_fails_cleanly(self):
        self.server.route = lambda path, payload: {"success": True}
        with self.assertRaises(GoogleFlowError):
            self.flow.succeed("tok")

    def test_link_already_linked_error_is_verbatim(self):
        self.server.route = lambda path, payload: {"error": "already_linked_google_by_others"}
        with self.assertRaises(GoogleFlowError) as exc:
            self.flow.link("tok", google_id="gid")
        self.assertIn("already been linked to another Google account", str(exc.exception))

    def test_link_happy_path(self):
        self.flow.link("tok", google_id="gid")
        method, path, payload = self.server.requests[-1]
        self.assertEqual((method, path), ("POST", "/user/link/google"))
        self.assertEqual(payload.get("google_id"), "gid")

    def test_unlink_sends_delete_and_tolerates_already_unlinked(self):
        self.server.route = lambda path, payload: {"error": "already_un_linked"}
        self.flow.unlink()  # no raise
        method, path, _ = self.server.requests[-1]
        self.assertEqual((method, path), ("DELETE", "/user/link/google"))


if __name__ == "__main__":
    unittest.main()
