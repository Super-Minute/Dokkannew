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

Correction (2026-10-26, live error): the 2026-10-26 APK-decompile finding
that the real app's Credential Manager flow needs a raw Google ID token is
still right, but the desktop equivalent cannot be Google's **implicit**
``response_type=id_token`` flow — Google rejects that for OAuth clients of
type "Desktop app"/"Installed application" with
``Error 400: unsupported_response_type`` (``flowName=GeneralOAuthFlow``);
Google only allows the implicit flow for "Web application" clients with
registered JS origins, which a CLI tool can't register. The standard
desktop-app substitute that still ends with a raw ID token in hand is the
**authorization-code + PKCE** flow: redirect with ``response_type=code``
and a ``code_challenge``, then exchange the returned ``code`` at Google's
token endpoint (``POST https://oauth2.googleapis.com/token``) for a token
set that includes ``id_token`` whenever ``scope`` contains ``openid`` (it
does, by default) — that id_token is what gets sent on to
``/user/succeed/google``, same as before.

Wire details left as documented-unconfirmed candidates: the payload field
carrying the Google token (locals near the calls suggest ``user_account`` /
``google_id``; we send ``google_token`` first and document alternatives),
and the OAuth scope (``openid`` default — not recovered).
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping

from dokkan.adapters.envelope import Envelope, envelope_secrets
from dokkan.adapters.http_gateway import DEFAULT_HEADERS, DEFAULT_HOSTS, VersionSource
from dokkan.adapters.save_store import SaveRecord
from dokkan.application.errors import ApplicationError

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"  # memory 03
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
_CALLBACK_REPLY = b"<html><body>Token received. You can close this tab.</body></html>"
_TOKEN_FIELD = "google_token"  # documented-unconfirmed (alternatives: id_token, token)


class GoogleFlowError(ApplicationError):
    """The Google login/link flow could not be completed."""


def make_pkce_pair() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` for RFC 7636 PKCE.

    ``code_challenge`` is the base64url (no padding) SHA-256 digest of
    ``code_verifier`` (``code_challenge_method=S256``), required by Google
    for the authorization-code flow on a public/"Desktop app" OAuth client.
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def exchange_code(client_id: str, code: str, redirect_uri: str, code_verifier: str,
                  client_secret: str | None = None,
                  token_endpoint: str = GOOGLE_TOKEN_ENDPOINT,
                  timeout: float = 30.0) -> dict:
    """POST the authorization ``code`` to Google's token endpoint and return
    the token set (``id_token``, ``access_token``, ...).

    ``client_secret`` is accepted (not required for PKCE itself) because
    Google's own token endpoint still expects it for "Desktop app" client
    IDs created in Cloud Console, even though such clients are public.
    """
    fields = {
        "client_id": client_id,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
    }
    if client_secret:
        fields["client_secret"] = client_secret
    body = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(
        token_endpoint, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise GoogleFlowError(
            f"Google rejected the code exchange (HTTP {exc.code}): {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise GoogleFlowError(f"cannot reach {token_endpoint}: {exc}") from exc
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise GoogleFlowError("non-JSON response from Google's token endpoint") from exc
    if not isinstance(parsed, dict) or not parsed.get("id_token"):
        raise GoogleFlowError("Google's token response did not include an id_token")
    return parsed


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


def build_auth_url(client_id: str, redirect_uri: str, scope: str = "openid",
                   code_challenge: str | None = None) -> str:
    """Google OAuth authorization URL — authorization-code + PKCE flow.

    Reverse-engineered correction (2026-10-26, decompiled APK, then
    corrected same day against Google's live rejection): the real mobile
    client does NOT do a browser authorization-code exchange inside the
    app at all — ``jp.aktsk.ishinclient.CredentialManagerHelper`` uses
    Android's ``androidx.credentials`` Credential Manager API
    (``TYPE_GOOGLE_ID_TOKEN_CREDENTIAL``) to obtain a Google-signed ID
    token (a JWT) directly from Google Play Services, with no
    ``redirect_uri`` round trip inside the app. There is no desktop
    equivalent of that native API, so this uses the standard desktop
    substitute instead: Google's **authorization-code + PKCE** flow
    (``response_type=code``, with ``code_challenge``/
    ``code_challenge_method=S256`` from :func:`make_pkce_pair`). Google's
    **implicit** ``response_type=id_token`` flow was tried first and
    rejected live (``Error 400: unsupported_response_type``,
    ``flowName=GeneralOAuthFlow``) — Google only allows that flow for
    "Web application" OAuth clients with registered JS origins, not for
    the "Desktop app" client type this program asks the user to create.
    The code this function requests is exchanged for a token set
    (including an ``id_token``) by :func:`exchange_code`, so the final
    token handed to the game server is still a raw Google ID token,
    matching the TYPE the game server actually expects.
    """
    fields = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "access_type": "offline",
    }
    if code_challenge:
        fields["code_challenge"] = code_challenge
        fields["code_challenge_method"] = "S256"
    return GOOGLE_AUTH_ENDPOINT + "?" + urllib.parse.urlencode(fields)


class TokenCallbackServer:
    """One-shot local ``/callback`` server capturing Google's reply.

    Mirrors the original's local OAuth callback: the browser lands on
    ``/callback?code=…`` (authorization-code + PKCE flow — see
    ``build_auth_url``) and gets the recovered verbatim reply page.
    ``token``/``id_token`` query keys are also accepted for direct-token
    callers, and POST is accepted too in case a flow ever delivers the
    value as form fields instead of a query string.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._captured: str | None = None
        self._event = threading.Event()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def _capture(self, fields: dict[str, list[str]]) -> None:
                for key in ("token", "id_token", "code"):
                    if fields.get(key):
                        server._captured = fields[key][0]
                        break
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(_CALLBACK_REPLY)))
                self.end_headers()
                self.wfile.write(_CALLBACK_REPLY)
                server._event.set()

            def do_GET(self):
                split = urllib.parse.urlsplit(self.path)
                if split.path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                self._capture(urllib.parse.parse_qs(split.query))

            def do_POST(self):
                split = urllib.parse.urlsplit(self.path)
                if split.path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode() if length else ""
                self._capture(urllib.parse.parse_qs(body))

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
    def _request_headers(self) -> dict:
        """DEFAULT_HEADERS plus X-ClientVersion (memory 02/12, confirmed
        live against ishin-global: the recovered ``X-ClientVersion: ``
        string from the real client's libcocos2dcpp.so, not in the
        encrypted envelope)."""
        headers = dict(DEFAULT_HEADERS)
        if self._versions is not None and self._versions.code:
            headers["X-ClientVersion"] = self._versions.code
        return headers

    def _send(self, method: str, path: str, payload: dict) -> dict:
        data, sign = self._out.seal(payload)
        return self._send_sealed(method, path, data, sign)

    def _send_sealed(self, method: str, path: str, data: str, sign: str) -> dict:
        body = urllib.parse.urlencode({"data": data, "sign": sign}).encode()
        request = urllib.request.Request(
            f"{self._base}{path}", data=body, method=method,
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     **self._request_headers()})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            hint = (" (recovered-but-unconfirmed endpoint — the real path, host, "
                    "or method may differ; see this module's docstring)"
                    if exc.code == 404 else "")
            detail = self._describe_error_body(exc.read())
            raise GoogleFlowError(
                f"{method} {self._base}{path} returned HTTP {exc.code}{hint}"
                f"{detail}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise GoogleFlowError(f"cannot reach {self._base}: {exc}") from exc
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

    def _describe_error_body(self, raw: bytes) -> str:
        """Best-effort detail for an HTTP error body: try the same
        envelope-decrypt-or-plain-JSON shape as a normal response, else
        fall back to raw (truncated) text. Never raises — this is purely
        diagnostic context appended to the raised GoogleFlowError."""
        if not raw:
            return ""
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            text = raw[:500].decode("utf-8", "replace")
            return f" — body: {text!r}"
        if isinstance(parsed, dict) and isinstance(parsed.get("sign"), str) and len(parsed) == 1:
            try:
                decrypted = self._in.open(parsed["sign"])
                return f" — body (decrypted): {decrypted!r}"
            except ValueError:
                pass
        return f" — body: {parsed!r}"

    def _token_payload(self, token: str) -> dict:
        return {_TOKEN_FIELD: token}

    def _send_trying_mac_candidates(self, method: str, path: str, payload: dict,
                                     progress=None) -> dict:
        """POST ``payload``, trying each of ``Envelope.candidate_mac_keys()``
        in turn whenever the server rejects one with ``decrypt_failed``.

        The AES ciphertext (``data``) doesn't depend on which key signs it,
        so this only re-signs, never re-encrypts, between attempts. Added
        2026-10-28 after a live ``decrypt_failed`` on this exact call plus
        the recovered ``cryption.py`` local-name evidence that the real
        client's MAC key ("key1") is a different value than the AES key
        this envelope defaults to (see ``candidate_mac_keys``'s docstring).
        """
        candidates = self._out.candidate_mac_keys()
        last_exc: GoogleFlowError | None = None
        for label, mac_key in candidates:
            data, sign = self._out.seal_with_mac_key(payload, mac_key)
            try:
                result = self._send_sealed(method, path, data, sign)
            except GoogleFlowError as exc:
                last_exc = exc
                if "decrypt_failed" not in str(exc):
                    raise  # a different failure — no point trying more MAC keys
                if progress:
                    progress(f"server rejected the envelope signed with MAC-key "
                             f"candidate '{label}' (decrypt_failed) — trying the "
                             f"next candidate...")
                continue
            if progress and label != candidates[0][0]:
                progress(f"MAC-key candidate '{label}' was accepted by the server "
                         f"(the default 'aes_key' guess was wrong for this call).")
            return result
        assert last_exc is not None
        raise GoogleFlowError(
            f"{method} {self._base}{path}: server returned decrypt_failed for every "
            f"candidate MAC key tried ({', '.join(label for label, _ in candidates)}) "
            "— the MAC key derivation needs more reverse-engineering, not just a "
            "different guess.") from last_exc

    def _post_with_validate_fallback(self, path: str, payload: dict,
                                      progress=None) -> dict:
        """POST ``path``; on a 404 retry once against ``path + "/validate"``.

        Ghidra decompile of ``libcocos2dcpp.so`` (2026-10-26, post-login-fix
        investigation) found BOTH ``user/succeed/{0}`` and
        ``user/succeed/{0}/validate`` as real, separately-compiled string
        constants, each with multiple live call sites in the binary — so
        the path *shape* we send is confirmed correct, but which of the two
        variants the Google flow specifically needs couldn't be pinned down
        without reconstructing heavily-optimized C++ virtual-dispatch call
        chains (out of scope for a strings/decompile pass). Trying the bare
        path first (matches the simpler, more common of the two call-site
        groups) and falling back to ``/validate`` on 404 costs one extra
        round trip only in the wrong-guess case and self-corrects either way.
        """
        try:
            return self._send_trying_mac_candidates("POST", path, payload, progress=progress)
        except GoogleFlowError as exc:
            if "returned HTTP 404" not in str(exc):
                raise
            return self._send_trying_mac_candidates(
                "POST", f"{path}/validate", payload, progress=progress)

    # -- flow ----------------------------------------------------------------
    def succeed(self, token: str, progress=None) -> GoogleAccount:
        """POST /user/succeed/google (falling back to .../validate on a 404
        — see _post_with_validate_fallback): pull the Dokkan account onto
        this save. ``progress``, if given, is called with narration lines
        — in particular which MAC-key candidate the server accepted, if the
        default guess was wrong (see _send_trying_mac_candidates)."""
        payload = self._post_with_validate_fallback(
            "/user/succeed/google", self._token_payload(token), progress=progress)
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
