"""Google account login/transfer for the game API (memories 02, 03, 05).

Recovered flow (verbatim strings, 2026-10-18): the original captures a Google
token at a local ``/callback`` HTTP server (reply page: "Token received. You
can close this tab."), then ``POST /user/succeed/google`` transfers the
Dokkan account (errors: ``CLIENT_TRANSFERRED``, "Failed to sign in after
Google login. Please try again."). ``POST /user/link/google`` links an
already-signed-in account (``already_linked_google_by_others``),
``DELETE /user/link/google`` unlinks (``already_un_linked`` is tolerated).

Deliberate divergence from the original: it harvests the token with
undetected_chromedriver — bot-detection evasion, out of scope here. This
implementation uses the plain OAuth pattern instead: the user opens the
authorization URL in their own browser and the one-shot local callback
server captures the reply. No browser automation, and no device block is
fabricated for the transfer call.

Wire details left as documented-unconfirmed candidates: the payload field
carrying the Google token (locals near the calls suggest ``user_account`` /
``google_id``; we send ``google_token`` first and document alternatives),
and the OAuth scope (``openid`` default — not recovered).
"""
from __future__ import annotations

import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping

from dokkan.adapters.envelope import Envelope, envelope_secrets
from dokkan.adapters.http_gateway import DEFAULT_HOSTS, VersionSource
from dokkan.adapters.save_store import SaveRecord
from dokkan.application.errors import ApplicationError

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"  # memory 03
_CALLBACK_REPLY = b"<html><body>Token received. You can close this tab.</body></html>"
_TOKEN_FIELD = "google_token"  # documented-unconfirmed (alternatives: id_token, token)


class GoogleFlowError(ApplicationError):
    """The Google login/link flow could not be completed."""


@dataclass(frozen=True)
class GoogleAccount:
    """The Dokkan account a Google transfer produced."""

    identifier: str
    access_token: str
    secret: str
    user_id: int | None
    raw: dict

    def to_save(self, client: str) -> SaveRecord:
        return SaveRecord(self.identifier, self.access_token, self.secret,
                          client, user_id=self.user_id)


def build_auth_url(client_id: str, redirect_uri: str, scope: str = "openid") -> str:
    """Google OAuth authorization URL (endpoint recovered, memory 03)."""
    return GOOGLE_AUTH_ENDPOINT + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,  # documented-unconfirmed
    })


class TokenCallbackServer:
    """One-shot local ``/callback`` server capturing Google's reply.

    Mirrors the original's local OAuth callback: the browser lands on
    ``/callback?token=…`` (or ``code=…``/``id_token=…``) and gets the
    recovered verbatim reply page.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._captured: str | None = None
        self._event = threading.Event()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                split = urllib.parse.urlsplit(self.path)
                if split.path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                query = urllib.parse.parse_qs(split.query)
                for key in ("token", "code", "id_token"):
                    if query.get(key):
                        server._captured = query[key][0]
                        break
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(_CALLBACK_REPLY)))
                self.end_headers()
                self.wfile.write(_CALLBACK_REPLY)
                server._event.set()

            def log_message(self, *args):
                pass

        self._httpd = ThreadingHTTPServer((host, port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}/callback"

    def __enter__(self) -> "TokenCallbackServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def wait(self, timeout: float = 300.0) -> str | None:
        """Block until the callback fires; None on timeout."""
        self._event.wait(timeout)
        return self._captured

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


class GoogleFlow:
    """Google token → account transfer and link/unlink over the envelope."""

    def __init__(self, client: str, versions: VersionSource | None = None,
                 base_url: str | None = None, timeout: float = 30.0) -> None:
        self._client = client
        self._versions = versions
        self._base = (base_url or DEFAULT_HOSTS[client]).rstrip("/")
        self._timeout = timeout
        self._out = Envelope(*envelope_secrets(client, "request"))
        self._in = Envelope(*envelope_secrets(client, "response"))

    # -- transport (mirrors auth_flow/http_gateway) --------------------------
    def _send(self, method: str, path: str, payload: dict) -> dict:
        data, sign = self._out.seal(payload)
        body = urllib.parse.urlencode({"data": data, "sign": sign}).encode()
        print(f"Body: {body} Path: {self._base}{path}")
        request = urllib.request.Request(
            f"{self._base}{path}", data=body, method=method,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise GoogleFlowError(f"{path} returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise GoogleFlowError(f"cannot reach {self._base}: {exc}") from exc
        import json
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise GoogleFlowError(f"non-JSON response from {path}") from exc
        if not isinstance(parsed, dict):
            raise GoogleFlowError(f"unexpected response shape from {path}")
        if isinstance(parsed.get("sign"), str) and len(parsed) == 1:
            try:
                return self._in.open(parsed["sign"])
            except ValueError as exc:
                raise GoogleFlowError(f"cannot decrypt response from {path}: {exc}") from exc
        return parsed

    def _token_payload(self, token: str) -> dict:
        payload = {_TOKEN_FIELD: token}
        if self._versions is not None:
            code = self._versions.code
            if code:
                payload["client_version"] = code
        return payload

    # -- flow ----------------------------------------------------------------
    def succeed(self, token: str) -> GoogleAccount:
        """POST /user/succeed/google: pull the Dokkan account onto this save."""
        print(f"token: {token}")
        payload = self._send("POST", "/user/succeed/google", self._token_payload(token))
        error = str(payload.get("error", ""))
        if "client_transferred" in error:
            raise GoogleFlowError("Client transferred. Please try again.")  # verbatim
        if error:
            raise GoogleFlowError(f"transfer failed: {error}")
        identifier = payload.get("identifier")
        access_token = payload.get("access_token")
        secret = payload.get("secret")
        if not all(isinstance(v, str) and v for v in (identifier, access_token, secret)):
            raise GoogleFlowError(
                "Failed to sign in after Google login. Please try again.")  # verbatim
        user_id = payload.get("user_id")
        return GoogleAccount(identifier, access_token, secret,
                            user_id if isinstance(user_id, int) else None, payload)

    def link(self, token: str, google_id: str | None = None) -> None:
        """POST /user/link/google on the signed-in account."""
        payload = self._token_payload(token)
        if google_id:
            payload["google_id"] = google_id
        response = self._send("POST", "/user/link/google", payload)
        if response.get("error") == "already_linked_google_by_others":
            raise GoogleFlowError(
                "This account has already been linked to another Google account.")  # verbatim
        if response.get("error"):
            raise GoogleFlowError(f"link failed: {response.get('error')}")

    def unlink(self) -> None:
        """DELETE /user/link/google on the signed-in account."""
        response = self._send("DELETE", "/user/link/google", {})
        error = response.get("error")
        if error and error != "already_un_linked":
            raise GoogleFlowError(f"unlink failed: {error}")
