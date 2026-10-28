"""Sealed-envelope HTTP transport shared by AuthFlow and HttpGateway.

Owns URL construction, form encoding, HTTPError body inspection, JSON parsing,
``{"sign": ...}`` unwrapping, and bounded transient-network retry. Version
gating is deliberately *not* handled here: the two callers apply different
policies around ``new_client_version_exists`` and inspect the returned payload.

Response integrity: the recovered wire format is ``{"sign": "<b64-ciphertext>"}``
with no separate MAC field, so integrity is delegated to TLS. If the server
ever ships ``{"data": ..., "sign": ...}`` with a MAC over ``data``, ``Envelope.verify``
must be called in ``_decode_payload`` before ``open`` — see the marked TODO.
"""
from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Mapping

from dokkan.adapters.envelope import Envelope
from dokkan.application.errors import ApplicationError

logger = logging.getLogger(__name__)

__all__ = ["Transport", "TransportError", "TransientTransportError"]


class TransportError(ApplicationError):
    """A request could not be completed; body unusable or status fatal."""


class TransientTransportError(TransportError):
    """A network-level failure that may succeed if retried."""


class Transport:
    def __init__(self, *, base_url: str, out_envelope: Envelope,
                 in_envelope: Envelope, timeout: float = 30.0,
                 sleep=time.sleep, network_retries: int = 2,
                 network_backoff: float = 0.5) -> None:
        self.base = base_url.rstrip("/")
        self._out = out_envelope
        self._in = in_envelope
        self._timeout = timeout
        self._sleep = sleep
        self._network_retries = max(0, network_retries)
        self._network_backoff = max(0.0, network_backoff)

    # -- public -------------------------------------------------------------
    def ping(self) -> bool:
        try:
            with urllib.request.urlopen(
                urllib.request.Request(f"{self.base}/ping", method="GET"),
                timeout=self._timeout,
            ) as response:
                return response.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def call(self, method: str, path: str, payload: dict | None = None,
             params: dict | None = None,
             headers: Mapping[str, str] | None = None, *,
             idempotent: bool = True) -> dict:
        attempts = 0
        while True:
            request = self._build(method.upper(), path, payload, params, headers)
            try:
                return self._read(request)
            except TransientTransportError as exc:
                if not idempotent or attempts >= self._network_retries:
                    raise
                attempts += 1
                backoff = self._network_backoff * (2 ** (attempts - 1))
                backoff += random.uniform(0, self._network_backoff)
                logger.warning(
                    "transient transport error (%d/%d) in %.2fs: %s",
                    attempts, self._network_retries, backoff, exc)
                self._sleep(backoff)

    # -- construction -------------------------------------------------------
    def _build(self, method: str, path: str, payload: dict | None,
               params: dict | None, headers: Mapping[str, str] | None):
        body = dict(payload or {})
        data, sign = self._out.seal(body)
        fields = {"data": data, "sign": sign}
        if method == "GET":
            if body:
                raise TransportError("GET requests cannot carry a payload")
            # NOTE: sealed identity goes in the query string to match the
            # target. This exposes it to intermediary URL logs; preserved
            # because it is spec-verified, not a template for new endpoints.
            query = urllib.parse.urlencode(
                {**dict(params or {}), **fields}, doseq=True)
            return urllib.request.Request(f"{self.base}{path}?{query}", method="GET")
        query = urllib.parse.urlencode(params or {}, doseq=True)
        url = f"{self.base}{path}" + (f"?{query}" if query else "")
        return urllib.request.Request(
            url,
            data=urllib.parse.urlencode(fields).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     **dict(headers or {})},
            method="POST",
        )

    # -- reading ------------------------------------------------------------
    def _read(self, request) -> dict:
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read() if hasattr(exc, "read") else b""
            inspected = self._try_decode_body(body)
            # Version-gate rejections can arrive as 4xx; feed them back so
            # the caller's retry loop sees new_client_version_exists.
            if isinstance(inspected, dict) and inspected.get("new_client_version_exists"):
                return inspected
            detail = self._snippet(body)
            message = f"{request.full_url} returned HTTP {exc.code}: {detail}"
            if exc.code >= 500:
                raise TransientTransportError(message) from exc
            raise TransportError(message) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise TransientTransportError(
                f"cannot reach {self.base}: {exc}") from exc
        return self._decode_payload(raw, request.full_url)

    def _decode_payload(self, raw: bytes, url: str) -> dict:
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise TransportError(f"non-JSON response from {url}") from exc
        if not isinstance(parsed, dict):
            raise TransportError(f"unexpected response shape from {url}")
        sign = parsed.get("sign")
        if not isinstance(sign, str):
            return parsed
        # TODO(integrity): if the wire format gains a MAC over `data`, call
        # self._in.verify(parsed["data"], sign) here before open().
        try:
            decrypted = self._in.open(sign)
        except ValueError as exc:
            raise TransportError(
                f"cannot decrypt response from {url}: {exc}") from exc
        if not isinstance(decrypted, dict):
            raise TransportError(f"decrypted response from {url} is not an object")
        # Plaintext-first (memory 04/06): plaintext siblings win on key conflict.
        merged = dict(decrypted)
        merged.update({k: v for k, v in parsed.items() if k != "sign"})
        return merged

    def _try_decode_body(self, raw: bytes) -> dict | None:
        if not raw:
            return None
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(parsed, dict):
            return None
        sign = parsed.get("sign")
        if isinstance(sign, str):
            try:
                decrypted = self._in.open(sign)
            except ValueError:
                return None
            if not isinstance(decrypted, dict):
                return None
            merged = dict(decrypted)
            merged.update({k: v for k, v in parsed.items() if k != "sign"})
            return merged
        return parsed

    @staticmethod
    def _snippet(raw: bytes, limit: int = 200) -> str:
        if not raw:
            return "<empty body>"
        text = raw.decode("utf-8", errors="replace")
        return text[:limit] + ("..." if len(text) > limit else "")