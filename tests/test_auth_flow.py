"""AuthFlow against a fake envelope-speaking auth server (memory 02/05/06/08).

The fake server implements the wire contract the recovered spec describes:
POST bodies carry ``data``/``sign`` form fields, responses are JSON
``{"sign": "<b64>"}``. It is an in-file copy of the FakeGameServer pattern
from tests/test_http_gateway.py (not imported from there), extended to
record request headers so the sign-in header contract can be asserted.
"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from dokkan.adapters.auth_flow import (
    DEFAULT_HOSTS,
    AccountTransferred,
    AuthFlow,
    AuthFlowError,
    CaptchaRequired,
    SignInResult,
    apply_to_save,
)
from dokkan.adapters.envelope import Envelope, envelope_secrets
from dokkan.adapters.save_store import SaveRecord
from dokkan.application.settings import DelayRange


class FakeAuthServer:
    """Minimal auth API speaking the recovered envelope."""

    def __init__(self, client: str = "global") -> None:
        self.client = client
        self.request_env = Envelope(*envelope_secrets(client, "request"))
        self.response_env = Envelope(*envelope_secrets(client, "response"))
        self.requests: list[tuple[str, str, dict, dict, dict]] = []
        # (method, path, query, payload, headers) — headers lowercased.
        self.version_rejections = 0  # reject the next N requests (any path)
        self.signin_version_rejections = 0  # reject the next N /auth/sign_in only
        self.nonce_payload: dict | None = None  # override for /auth/nonce replies
        self.signin_script: list[dict] = []  # queued /auth/sign_in replies
        self.nonces = 0

        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _respond(self, code: int, payload: dict | None = None) -> None:
                body = b"pong" if payload is None else json.dumps(
                    {"sign": server._seal(payload)}).encode()
                self.send_response(code)
                self.send_header("Content-Type",
                                  "application/json" if payload is not None else "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle(self, method: str) -> None:
                split = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(split.query).items()}
                headers = {k.lower(): v for k, v in self.headers.items()}
                if split.path == "/ping":
                    server.requests.append((method, split.path, query, {}, headers))
                    self._respond(200)
                    return
                if method == "POST":
                    length = int(self.headers.get("Content-Length", 0))
                    fields = {k: v[0] for k, v in
                              parse_qs(self.rfile.read(length).decode()).items()}
                else:
                    fields = query
                payload = server.request_env.open(fields["data"])
                server.requests.append((method, split.path, query, payload, headers))
                if server._rejects_version(split.path):
                    self._respond(200, {"new_client_version_exists": True,
                                        "client_version": "9.9.9"})
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
    def _rejects_version(self, path: str) -> bool:
        if path == "/auth/sign_in" and self.signin_version_rejections > 0:
            self.signin_version_rejections -= 1
            return True
        if self.version_rejections > 0:
            self.version_rejections -= 1
            return True
        return False

    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def _seal(self, payload: dict) -> str:
        return self.response_env.seal(payload)[0]

    def route(self, path: str, payload: dict) -> dict:
        if path == "/auth/nonce":
            self.nonces += 1
            if self.nonce_payload is not None:
                return dict(self.nonce_payload)
            return {"auth_transaction_id": f"txn-{self.nonces}"}
        if path == "/auth/sign_in":
            if self.signin_script:
                return self.signin_script.pop(0)
            return {"access_token": "new-token", "user_id": 42}
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


class AuthFlowTestCase(unittest.TestCase):
    def setUp(self):
        self.server = FakeAuthServer()
        self.versions = StubVersions()

    def tearDown(self):
        self.server.close()

    def make_flow(self, **overrides) -> AuthFlow:
        kwargs = dict(versions=self.versions, base_url=self.server.url())
        kwargs.update(overrides)
        return AuthFlow("global", **kwargs)

    def _requests(self, path: str) -> list:
        return [r for r in self.server.requests if r[1] == path]


class NonceTests(AuthFlowTestCase):
    def test_nonce_returns_auth_transaction_id(self):
        self.assertEqual(self.make_flow().nonce(), "txn-1")
        method, path, _, payload, _ = self.server.requests[-1]
        self.assertEqual((method, path), ("POST", "/auth/nonce"))

    def test_nonce_missing_transaction_id_raises(self):
        self.server.nonce_payload = {}
        with self.assertRaises(AuthFlowError) as ctx:
            self.make_flow().nonce()
        self.assertIn("No auth_transaction_id in nonce response", str(ctx.exception))

    def test_nonce_version_rejection_refreshes_and_retries_once(self):
        self.server.version_rejections = 1
        self.assertEqual(self.make_flow().nonce(), "txn-1")
        self.assertEqual(self.versions.refreshes, 1)
        self.assertEqual(len(self._requests("/auth/nonce")), 2)

    def test_nonce_persistent_version_rejection_raises_after_one_retry(self):
        self.server.version_rejections = 5
        with self.assertRaises(AuthFlowError):
            self.make_flow().nonce()
        self.assertEqual(self.versions.refreshes, 1)
        self.assertEqual(len(self._requests("/auth/nonce")), 2)

    def test_nonce_without_version_source_and_rejection_raises(self):
        self.server.version_rejections = 5
        with self.assertRaises(AuthFlowError):
            AuthFlow("global", base_url=self.server.url()).nonce()


class SignInTests(AuthFlowTestCase):
    def test_successful_sign_in_sends_credentials_headers_and_tokens(self):
        result = self.make_flow().sign_in("kyle", "s3cret")
        self.assertEqual(result.access_token, "new-token")
        self.assertEqual(result.user_id, 42)
        self.assertEqual(result.raw["access_token"], "new-token")
        nonce_headers = self._requests("/auth/nonce")[0][4]
        _, _, _, payload, headers = self._requests("/auth/sign_in")[0]
        # memory 02/12 update: client_version travels as the X-ClientVersion
        # header (confirmed live against ishin-global), not in the envelope.
        self.assertEqual(nonce_headers["x-clientversion"], "6.6.0-abc")
        self.assertEqual(payload["identifier"], "kyle")
        self.assertEqual(payload["secret"], "s3cret")
        self.assertEqual(payload["auth_transaction_id"], "txn-1")
        self.assertEqual(headers["x-clientversion"], "6.6.0-abc")
        self.assertEqual(headers["cache-control"], "no-cache")
        self.assertEqual(headers["x-usercountry"], "US")
        self.assertEqual(headers["x-usercurrency"], "USD")

    def test_sign_in_country_and_currency_are_overridable(self):
        self.make_flow().sign_in("kyle", "s", user_country="JP", currency="JPY")
        headers = self._requests("/auth/sign_in")[0][4]
        self.assertEqual(headers["x-usercountry"], "JP")
        self.assertEqual(headers["x-usercurrency"], "JPY")

    def test_sign_in_includes_device_token_only_when_provided(self):
        self.make_flow().sign_in("kyle", "s")
        self.assertNotIn("device_token", self._requests("/auth/sign_in")[0][3])
        self.make_flow().sign_in("kyle", "s", device_token="tok-1")
        self.assertEqual(self._requests("/auth/sign_in")[1][3]["device_token"], "tok-1")

    def test_sign_in_platform_fields_only_when_provided(self):
        self.make_flow().sign_in("kyle", "s")
        self.assertNotIn("device", self._requests("/auth/sign_in")[0][3])
        self.make_flow().sign_in("kyle", "s", platform={
            "device": "Pixel 7 Pro", "os_architecture": "arm64-v8a", "bogus": 1})
        payload = self._requests("/auth/sign_in")[1][3]
        self.assertEqual(payload["device"], "Pixel 7 Pro")
        self.assertEqual(payload["os_architecture"], "arm64-v8a")
        self.assertNotIn("device_model", payload)
        self.assertNotIn("bogus", payload)

    def test_sign_in_version_rejection_refreshes_and_retries_once(self):
        self.server.signin_version_rejections = 1
        result = self.make_flow().sign_in("kyle", "s")
        self.assertEqual(result.access_token, "new-token")
        self.assertEqual(self.versions.refreshes, 1)
        self.assertEqual(len(self._requests("/auth/sign_in")), 2)

    def test_sign_in_persistent_version_rejection_raises_after_one_retry(self):
        self.server.signin_version_rejections = 5
        with self.assertRaisesRegex(AuthFlowError, "Client version mismatch during signin"):
            self.make_flow().sign_in("kyle", "s")
        self.assertEqual(self.versions.refreshes, 1)
        self.assertEqual(len(self._requests("/auth/sign_in")), 2)

    def test_captcha_url_raises_captcha_required(self):
        self.server.signin_script = [{"captcha_url": "https://captcha.example/x",
                                      "captcha_session_key": "sess-1"}]
        with self.assertRaises(CaptchaRequired) as ctx:
            self.make_flow().sign_in("kyle", "s")
        self.assertEqual(ctx.exception.captcha_url, "https://captcha.example/x")

    def test_client_transferred_raises_account_transferred(self):
        self.server.signin_script = [
            {"error": "oauth2_mac_rails/client_transferred"},
            {"err_code": "oauth2_bearer_rails/client_transferred"},
        ]
        for _ in range(2):
            with self.assertRaises(AccountTransferred):
                self.make_flow().sign_in("kyle", "s")

    def test_server_verification_expired_retries_once_with_fresh_nonce(self):
        self.server.signin_script = [
            {"error": "Server verification expired. Retrying..."},
            {"access_token": "retry-token", "user_id": 7},
        ]
        result = self.make_flow().sign_in("kyle", "s")
        self.assertEqual(result.access_token, "retry-token")
        self.assertEqual(result.user_id, 7)
        self.assertEqual(len(self._requests("/auth/nonce")), 2)
        signins = self._requests("/auth/sign_in")
        self.assertEqual(len(signins), 2)
        self.assertEqual(signins[0][3]["auth_transaction_id"], "txn-1")
        self.assertEqual(signins[1][3]["auth_transaction_id"], "txn-2")

    def test_server_verification_expired_raises_after_one_retry(self):
        self.server.signin_script = [
            {"error": "Server verification expired. Retrying..."}] * 3
        with self.assertRaises(AuthFlowError):
            self.make_flow().sign_in("kyle", "s")
        self.assertEqual(len(self._requests("/auth/sign_in")), 2)
        self.assertEqual(len(self._requests("/auth/nonce")), 2)

    def test_missing_access_token_raises_sign_in_failed(self):
        self.server.signin_script = [{"user_id": 42}]
        with self.assertRaisesRegex(AuthFlowError, "sign-in failed"):
            self.make_flow().sign_in("kyle", "s")

    def test_sign_in_user_id_optional(self):
        self.server.signin_script = [{"access_token": "tok-no-user"}]
        result = self.make_flow().sign_in("kyle", "s")
        self.assertEqual(result.access_token, "tok-no-user")
        self.assertIsNone(result.user_id)


class PacingTests(AuthFlowTestCase):
    def test_pacing_sleeps_exactly_once_between_nonce_and_sign_in(self):
        sleeps = []
        flow = self.make_flow(delay=DelayRange(20.0, 21.0), sleep=sleeps.append,
                              rng=lambda a, b: (a + b) / 2)
        flow.sign_in("kyle", "s")
        self.assertEqual(sleeps, [20.5])

    def test_default_is_no_pacing(self):
        sleeps = []
        flow = self.make_flow(sleep=sleeps.append)
        self.assertTrue(flow.sign_in("kyle", "s").access_token)
        self.assertEqual(sleeps, [])


class PingTests(AuthFlowTestCase):
    def test_ping_returns_true_on_200(self):
        self.assertTrue(self.make_flow().ping())

    def test_ping_sends_x_client_version_header(self):
        self.make_flow().ping()
        _, _, _, _, headers = self._requests("/ping")[0]
        self.assertEqual(headers["x-clientversion"], "6.6.0-abc")

    def test_default_hosts_match_the_recovered_constants(self):
        self.assertEqual(DEFAULT_HOSTS, {
            "global": "https://ishin-global.aktsk.com",
            "japan": "https://ishin-production.aktsk.jp",
        })


class ApplyToSaveTests(unittest.TestCase):
    def test_apply_to_save_preserves_identity_and_updates_tokens(self):
        save = SaveRecord("kyle", "old-token", "s", "global", user_id=1,
                          extras={"device_token_glb": "dt", "note": "hi"})
        result = SignInResult("new-token", 42, {"access_token": "new-token", "user_id": 42})
        refreshed = apply_to_save(save, result)
        self.assertEqual(refreshed.access_token, "new-token")
        self.assertEqual(refreshed.user_id, 42)
        self.assertEqual(refreshed.identifier, "kyle")
        self.assertEqual(refreshed.secret, "s")
        self.assertEqual(refreshed.client, "global")
        self.assertEqual(refreshed.extras, {"device_token_glb": "dt", "note": "hi"})

    def test_apply_to_save_clears_user_id_when_absent(self):
        save = SaveRecord("kyle", "old-token", "s", "japan", user_id=1)
        refreshed = apply_to_save(save, SignInResult("t", None, {}))
        self.assertEqual(refreshed.access_token, "t")
        self.assertIsNone(refreshed.user_id)
        self.assertEqual(refreshed.client, "japan")


if __name__ == "__main__":
    unittest.main()
