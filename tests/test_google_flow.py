"""Google login/transfer flow against a fake envelope-speaking server plus
the one-shot /callback server (memories 02, 03, 05).
"""
import base64
import hashlib
import hmac
import json
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

from dokkan.adapters.envelope import Envelope, envelope_secrets
from dokkan.adapters.google_flow import (
    GoogleFlow, GoogleFlowError, TokenCallbackServer, build_auth_url,
    exchange_code, make_pkce_pair,
)
from dokkan.adapters.save_store import load_save
from pathlib import Path


class FakeAuthServer:
    """Minimal game API speaking the recovered envelope, incl. DELETE.

    ``required_mac_label`` (default ``"aes_key"``, matching the client's
    current default guess) picks which of ``Envelope.candidate_mac_keys()``
    the server insists on; a request signed with any other candidate gets
    a 400 ``{"error": {"code": "decrypt_failed"}}``, exactly like the real
    server did live on 2026-10-28 — this is what
    ``GoogleFlow._send_trying_mac_candidates`` must recover from.
    """

    def __init__(self, client: str = "global", required_mac_label: str = "aes_key") -> None:
        self.request_env = Envelope(*envelope_secrets(client, "request"))
        self.response_env = Envelope(*envelope_secrets(client, "response"))
        self.requests: list[tuple[str, str, dict]] = []  # (method, path, payload)
        self.request_headers: list[dict] = []  # lowercased
        self.required_mac_label = required_mac_label
        self._required_key = dict(self.request_env.candidate_mac_keys())[required_mac_label]
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

            def _reject_decrypt_failed(self) -> None:
                body = json.dumps({"error": {"code": "decrypt_failed"}}).encode()
                self.send_response(400)
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
                ciphertext = base64.b64decode(fields["data"], validate=True)
                expected = hmac.new(server._required_key, ciphertext, hashlib.md5).hexdigest()
                if fields.get("sign") != expected:
                    self._reject_decrypt_failed()
                    return
                server.requests.append((method, split.path, payload))
                server.request_headers.append(
                    {k.lower(): v for k, v in self.headers.items()})
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
        # authorization-code + PKCE: the implicit id_token flow this used
        # to request is rejected live by Google for "Desktop app" OAuth
        # clients (Error 400: unsupported_response_type, memory 05
        # correction, 2026-10-26).
        url = build_auth_url("cid-123", "http://127.0.0.1:9/callback")
        self.assertTrue(url.startswith(
            "https://accounts.google.com/o/oauth2/v2/auth?"))
        for fragment in ("client_id=cid-123", "response_type=code", "scope=openid"):
            self.assertIn(fragment, url)
        self.assertNotIn("response_type=id_token", url)

    def test_url_carries_pkce_challenge_when_given(self):
        url = build_auth_url("cid-123", "http://127.0.0.1:9/callback",
                             code_challenge="chal-abc")
        self.assertIn("code_challenge=chal-abc", url)
        self.assertIn("code_challenge_method=S256", url)

    def test_no_challenge_omits_pkce_fields(self):
        url = build_auth_url("cid-123", "http://127.0.0.1:9/callback")
        self.assertNotIn("code_challenge", url)


class MakePkcePairTests(unittest.TestCase):
    def test_challenge_is_sha256_of_verifier(self):
        import base64
        import hashlib
        verifier, challenge = make_pkce_pair()
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(challenge, expected)

    def test_pairs_are_not_repeated(self):
        first = make_pkce_pair()
        second = make_pkce_pair()
        self.assertNotEqual(first, second)


class FakeTokenEndpoint:
    """Minimal stand-in for https://oauth2.googleapis.com/token."""

    def __init__(self, response: dict, status: int = 200) -> None:
        self.response = response
        self.status = status
        self.requests: list[dict] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                fields = {k: v[0] for k, v in parse_qs(
                    self.rfile.read(length).decode()).items()}
                server.requests.append(fields)
                body = json.dumps(server.response).encode()
                self.send_response(server.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self.thread.start()

    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self.thread.join(timeout=5)


class ExchangeCodeTests(unittest.TestCase):
    def test_posts_pkce_fields_and_returns_tokens(self):
        endpoint = FakeTokenEndpoint({"id_token": "jwt-1", "access_token": "at-1"})
        try:
            tokens = exchange_code(
                "cid-123", "code-xyz", "http://127.0.0.1:9/callback", "verifier-1",
                client_secret="shh", token_endpoint=endpoint.url())
            self.assertEqual(tokens["id_token"], "jwt-1")
            sent = endpoint.requests[-1]
            self.assertEqual(sent["client_id"], "cid-123")
            self.assertEqual(sent["code"], "code-xyz")
            self.assertEqual(sent["code_verifier"], "verifier-1")
            self.assertEqual(sent["client_secret"], "shh")
            self.assertEqual(sent["grant_type"], "authorization_code")
        finally:
            endpoint.close()

    def test_omits_client_secret_when_not_given(self):
        endpoint = FakeTokenEndpoint({"id_token": "jwt-1"})
        try:
            exchange_code("cid-123", "code-xyz", "http://127.0.0.1:9/callback",
                          "verifier-1", token_endpoint=endpoint.url())
            self.assertNotIn("client_secret", endpoint.requests[-1])
        finally:
            endpoint.close()

    def test_missing_id_token_in_response_fails_cleanly(self):
        endpoint = FakeTokenEndpoint({"access_token": "at-1"})
        try:
            with self.assertRaises(GoogleFlowError):
                exchange_code("cid-123", "code-xyz", "http://127.0.0.1:9/callback",
                              "verifier-1", token_endpoint=endpoint.url())
        finally:
            endpoint.close()

    def test_http_error_is_wrapped(self):
        endpoint = FakeTokenEndpoint({"error": "invalid_grant"}, status=400)
        try:
            with self.assertRaises(GoogleFlowError) as exc:
                exchange_code("cid-123", "code-xyz", "http://127.0.0.1:9/callback",
                              "verifier-1", token_endpoint=endpoint.url())
            self.assertIn("400", str(exc.exception))
        finally:
            endpoint.close()


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

    def test_captures_id_token_via_form_post(self):
        # response_mode=form_post (build_auth_url) delivers the id_token
        # as a POST body, not a query string.
        with TokenCallbackServer() as callback:
            body = urlencode({"id_token": "jwt-xyz"}).encode()
            request = urllib.request.Request(callback.url, data=body, method="POST")
            response = urllib.request.urlopen(request)
            self.assertEqual(response.status, 200)
            self.assertEqual(
                response.read(),
                b"<html><body>Token received. You can close this tab.</body></html>")
            self.assertEqual(callback.wait(timeout=5), "jwt-xyz")

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


class StubVersions:
    def __init__(self, code: str = "6.6.0-abc") -> None:
        self.code = code
        self.refreshes = 0

    def refresh(self) -> None:
        self.refreshes += 1

    def learn(self, code: str) -> None:
        self.code = code


class GoogleFlowMacFallbackTests(unittest.TestCase):
    """The server rejecting our default MAC-key guess with decrypt_failed
    (as the real server did live on 2026-10-28) must self-correct by
    trying Envelope.candidate_mac_keys() — exactly like the existing
    404-\u2192/validate fallback self-corrects on a path guess."""

    def _flow_against(self, required_mac_label: str) -> tuple[GoogleFlow, FakeAuthServer]:
        server = FakeAuthServer(required_mac_label=required_mac_label)
        flow = GoogleFlow("global", versions=StubVersions(), base_url=server.url())
        return flow, server

    def test_default_mac_key_still_works_when_server_wants_it(self):
        flow, server = self._flow_against("aes_key")
        try:
            account = flow.succeed("tok-1")
            self.assertEqual(account.identifier, "gg-1")
        finally:
            server.close()

    def test_recovers_when_server_wants_a_different_mac_key(self):
        flow, server = self._flow_against("md5_password_salt")
        narration = []
        try:
            account = flow.succeed("tok-1", progress=narration.append)
            self.assertEqual(account.identifier, "gg-1")
            self.assertTrue(
                any("md5_password_salt" in line and "accepted" in line
                    for line in narration),
                narration)
        finally:
            server.close()

    def test_every_candidate_rejected_raises_a_clear_error(self):
        # A server requiring a key not in our candidate list at all —
        # proves the "ran out of guesses" path reports plainly instead of
        # looping or showing a bare decrypt_failed from the last attempt.
        flow, server = self._flow_against("aes_key")
        server.required_mac_label = "aes_key"
        # Sabotage: make every candidate wrong by requiring a key that is
        # never produced by candidate_mac_keys() (a never-matching sentinel).
        original_candidates = Envelope.candidate_mac_keys
        try:
            Envelope.candidate_mac_keys = lambda self: [("x", b"not-the-real-key")]
            with self.assertRaises(GoogleFlowError) as exc:
                flow.succeed("tok-1")
            self.assertIn("decrypt_failed for every candidate", str(exc.exception))
        finally:
            Envelope.candidate_mac_keys = original_candidates
            server.close()


class GoogleFlowTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeAuthServer()
        self.versions = StubVersions()
        self.flow = GoogleFlow("global", versions=self.versions,
                               base_url=self.server.url())

    def tearDown(self):
        self.server.close()

    def test_succeed_posts_token_and_maps_account(self):
        account = self.flow.succeed("tok-1")
        self.assertEqual((account.identifier, account.access_token,
                          account.secret, account.user_id), ("gg-1", "at", "sec", 7))
        method, path, payload = self.server.requests[-1]
        self.assertEqual((method, path), ("POST", "/user/succeed/google"))
        self.assertEqual(payload.get("google_token"), "tok-1")

    def test_succeed_sends_x_client_version_header(self):
        # memory 02/12 update: client_version travels as the
        # X-ClientVersion header (confirmed live against ishin-global),
        # not inside the encrypted envelope.
        self.flow.succeed("tok-1")
        self.assertEqual(
            self.server.request_headers[-1].get("x-clientversion"), "6.6.0-abc")
        self.assertNotIn("client_version", self.server.requests[-1][2])

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

    def test_succeed_http_404_includes_unconfirmed_endpoint_hint(self):
        # Hitting a wrong/non-existent path on the real server is expected
        # for a cleanroom-recovered-but-unconfirmed endpoint — the error
        # should say so plainly, with the full method+URL, not just "path".
        class NotFoundHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.send_response(404)
                self.end_headers()

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), NotFoundHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
            bad_flow = GoogleFlow("global", versions=self.versions, base_url=base)
            with self.assertRaises(GoogleFlowError) as exc:
                bad_flow.succeed("tok")
            message = str(exc.exception)
            self.assertIn(f"POST {base}/user/succeed/google/validate returned HTTP 404", message)
            self.assertIn("recovered-but-unconfirmed endpoint", message)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_succeed_http_error_includes_response_body_detail(self):
        # 2026-10-26, live: the /validate fallback hit a real HTTP 400 from
        # the game server. The raised error should surface whatever body
        # came back (plain JSON here; envelope-decrypted in the live case)
        # instead of just the bare status code, so the user isn't stuck
        # debugging blind.
        class BadRequestHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.dumps({"error": "missing_field", "field": "google_token"}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), BadRequestHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
            bad_flow = GoogleFlow("global", versions=self.versions, base_url=base)
            with self.assertRaises(GoogleFlowError) as exc:
                bad_flow.succeed("tok")
            message = str(exc.exception)
            self.assertIn("returned HTTP 400", message)
            self.assertIn("missing_field", message)
            self.assertIn("google_token", message)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_succeed_falls_back_to_validate_suffix_on_404(self):
        # memory 12 Ghidra finding (2026-10-26): both `/user/succeed/google`
        # and `/user/succeed/google/validate` are real compiled endpoint
        # templates in libcocos2dcpp.so. A 404 on the bare path should
        # retry once against the `/validate` variant instead of failing.
        backend = FakeAuthServer()
        backend.route = lambda path, payload: (
            {"identifier": "gg-1", "access_token": "at", "secret": "sec", "user_id": 7}
            if path == "/user/succeed/google/validate" else
            (_ for _ in ()).throw(AssertionError(f"unexpected path {path}")))

        class Handler404ThenProxy(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                if self.path == "/user/succeed/google":
                    self.send_response(404)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                forwarded = urllib.request.Request(
                    backend.url() + self.path, data=body, method="POST",
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
                response = urllib.request.urlopen(forwarded)
                payload = response.read()
                self.send_response(response.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler404ThenProxy)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
            flow = GoogleFlow("global", versions=self.versions, base_url=base)
            account = flow.succeed("tok-1")
            self.assertEqual(account.identifier, "gg-1")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
            backend.close()

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
