"""The sign-in flow against the real auth API (memories 02, 05, 06, 08).

``AuthFlow`` performs the two-call handshake the recovered spec describes:
``POST /auth/nonce`` to obtain an ``auth_transaction_id``, then
``POST /auth/sign_in`` carrying the account's ``identifier``/``secret`` and
that transaction id. Transport mirrors :mod:`dokkan.adapters.http_gateway`:
bodies are form-encoded ``data``+``sign`` sealed with the request-direction
envelope, responses are plaintext-first then ``{"sign": ...}`` with the
response secrets, and a ``new_client_version_exists`` reply forces one
version refresh and retry.

Wire details the recovered spec leaves unconfirmed are collected here:
the nonce body (we send only ``client_version``), a ``Basic`` auth-scheme
hint sitting next to the recovered sign-in constants (not implemented),
the exact response field carrying the "Server verification expired" signal
(we scan the common error keys), and the placement of the platform block
(we send its fields flat in the payload, and only when provided).
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from typing import Mapping

from dokkan.adapters.envelope import Envelope, envelope_secrets
from dokkan.adapters.http_gateway import DEFAULT_HOSTS, VersionSource
from dokkan.adapters.save_store import SaveRecord
from dokkan.application.errors import ApplicationError
from dokkan.application.settings import DelayRange

__all__ = [
    "DEFAULT_HOSTS", "AccountTransferred", "AuthFlow", "AuthFlowError",
    "CaptchaRequired", "SignInResult", "apply_to_save",
]

# Platform block the original attaches to its sign-in retries (memory 05).
# Field placement and the full set are unconfirmed; only provided keys of the
# mapping are sent, flattened straight into the payload.
_PLATFORM_KEYS = ("device", "device_model", "os_version", "graphics_api",
                  "is_usable_astc", "os_architecture")
# Verbatim transferred codes from the recovered strings.
_TRANSFERRED_CODES = ("oauth2_mac_rails/client_transferred",
                      "oauth2_bearer_rails/client_transferred")
# Common response fields error values travel in (memory 06).
_ERROR_KEYS = ("error", "err_code", "code", "message")
_EXPIRED_MARKER = "Server verification expired"


class AuthFlowError(ApplicationError):
    """The sign-in flow could not be completed."""


class CaptchaRequired(AuthFlowError):
    """The server demanded a captcha (the original opens a browser)."""

    def __init__(self, captcha_url: str) -> None:
        super().__init__(f"captcha required: {captcha_url}")
        self.captcha_url = captcha_url


class AccountTransferred(AuthFlowError):
    """The account has been transferred to another device."""


@dataclass(frozen=True)
class SignInResult:
    access_token: str
    user_id: int | None
    raw: dict


def apply_to_save(save: SaveRecord, result: SignInResult) -> SaveRecord:
    """Refresh a save with a completed sign-in (memory 05).

    Identifier/secret/client and the extras travel on; only the tokens the
    flow produced are replaced.
    """
    return replace(save, access_token=result.access_token, user_id=result.user_id)


def _error_values(response: dict):
    for key in _ERROR_KEYS:
        if key in response:
            yield response[key]


def _transferred_code(response: dict) -> str | None:
    for value in _error_values(response):
        if isinstance(value, str) and value in _TRANSFERRED_CODES:
            return value
    return None


def _verification_expired(response: dict) -> bool:
    return any(isinstance(value, str) and _EXPIRED_MARKER in value
               for value in _error_values(response))


class AuthFlow:
    """Nonce + sign-in handshake over the recovered HTTP envelope."""

    def __init__(self, client: str, versions: VersionSource | None = None,
                 base_url: str | None = None, timeout: float = 30.0,
                 delay: DelayRange | None = None, sleep=time.sleep,
                 rng=random.uniform) -> None:
        self._client = client
        self._versions = versions
        self._base = (base_url or DEFAULT_HOSTS[client]).rstrip("/")
        self._timeout = timeout
        self._delay = delay
        self._sleep = sleep
        self._rng = rng
        self._out = Envelope(*envelope_secrets(client, "request"))
        self._in = Envelope(*envelope_secrets(client, "response"))

    # -- transport ----------------------------------------------------------
    def ping(self) -> bool:
        """GET /ping connectivity probe (memory 02)."""
        try:
            with urllib.request.urlopen(
                urllib.request.Request(f"{self._base}/ping", method="GET"),
                timeout=self._timeout,
            ) as response:
                return response.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def _post(self, path: str, payload: dict,
              headers: Mapping[str, str] | None = None) -> dict:
        data, sign = self._out.seal(payload)
        fields = {"data": data, "sign": sign}
        request = urllib.request.Request(
            f"{self._base}{path}",
            data=urllib.parse.urlencode(fields).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     **dict(headers or {})},
            method="POST")
        return self._read(request)

    def _read(self, request) -> dict:
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise AuthFlowError(f"{request.full_url} returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise AuthFlowError(f"cannot reach {self._base}: {exc}") from exc
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise AuthFlowError(f"non-JSON response from {request.full_url}") from exc
        if not isinstance(parsed, dict):
            raise AuthFlowError(f"unexpected response shape from {request.full_url}")
        # Plaintext fields first, then inside "sign" (memory 04/06).
        if isinstance(parsed.get("sign"), str) and len(parsed) == 1:
            try:
                return self._in.open(parsed["sign"])
            except ValueError as exc:
                raise AuthFlowError(
                    f"cannot decrypt response from {request.full_url}: {exc}") from exc
        return parsed

    def _version_payload(self) -> dict:
        payload = {}
        if self._versions is not None:
            payload["client_version"] = self._versions.code
        return payload

    def _pace(self) -> None:
        if self._delay is not None:
            self._sleep(self._rng(self._delay.min, self._delay.max))

    # -- handshake ----------------------------------------------------------
    def nonce(self) -> str:
        """POST /auth/nonce and return the ``auth_transaction_id``."""
        for attempt in (0, 1):
            response = self._post("/auth/nonce", self._version_payload())
            if not response.get("new_client_version_exists"):
                transaction = response.get("auth_transaction_id")
                if not isinstance(transaction, str) or not transaction:
                    raise AuthFlowError(
                        f"No auth_transaction_id in nonce response: {response}")
                return transaction
            if attempt or self._versions is None:
                raise AuthFlowError("Client version mismatch during nonce")
            self._versions.refresh()
        raise AuthFlowError("unreachable")

    def sign_in(self, identifier: str, secret: str, *, user_country: str = "US",
                currency: str = "USD", device_token: str | None = None,
                platform: Mapping[str, object] | None = None) -> SignInResult:
        """Run the full handshake and return the fresh tokens."""
        retried_version = False
        retried_expired = False
        while True:
            transaction = self.nonce()
            self._pace()
            payload = {
                "identifier": identifier,
                "secret": secret,
                "auth_transaction_id": transaction,
                **self._version_payload(),
            }
            if device_token:
                payload["device_token"] = device_token
            if platform:
                for key in _PLATFORM_KEYS:
                    if key in platform:
                        payload[key] = platform[key]
            response = self._post("/auth/sign_in", payload, headers={
                "Cache-Control": "no-cache",
                "X-UserCountry": user_country,
                "X-UserCurrency": currency,
            })
            if response.get("new_client_version_exists"):
                if retried_version or self._versions is None:
                    raise AuthFlowError("Client version mismatch during signin")
                retried_version = True
                self._versions.refresh()
                continue
            if _transferred_code(response) is not None:
                raise AccountTransferred(
                    "account was transferred to another device: "
                    f"{_transferred_code(response)}")
            if isinstance(response.get("captcha_url"), str) and response.get("captcha_url"):
                raise CaptchaRequired(response["captcha_url"])
            if _verification_expired(response):
                if retried_expired:
                    raise AuthFlowError(
                        "Server verification expired. Retrying... failed")
                retried_expired = True
                continue  # one retry with a fresh nonce
            access_token = response.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise AuthFlowError(
                    f"sign-in failed: no access_token in response: {response}")
            user_id = response.get("user_id")
            return SignInResult(access_token,
                                user_id if isinstance(user_id, int) else None,
                                response)
