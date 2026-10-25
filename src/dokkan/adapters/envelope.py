"""Cleanroom reimplementation of the game's request envelope crypto (memory 04).

The target derives an AES-256-CBC key/iv with OpenSSL's EVP_BytesToKey (MD5),
encrypts a JSON payload with PKCS7 padding, and authenticates the ciphertext
with an HMAC-MD5 ``mac``. This module provides exactly those primitives with
zero external dependencies (pure-Python AES-256).

Wire details that the recovered spec leaves unconfirmed (which constant is
password vs salt per client/direction, the exact MAC key and input) are kept
parameterised in :class:`Envelope` / :func:`envelope_secrets`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

BLOCK_SIZE = 16  # AES block / PKCS7 unit (the target's BLOCK_SIZE constant)
KEY_SIZE = 32  # AES-256

# The two 64-char secrets recovered from the constants blob (memory 04).
# Confirmed by the 2026-10-18 Ghidra/blob-neighborhood pass: the secrets sit
# right after the `client`/`global` locals in cryption.py's constant pool
# (source order: global -> fk0I first, xNJZ second), and the config module
# names them JP_SIGN_KEY/GLB_SIGN_KEY with xNJZ adjacent to the JP name.
# Still unconfirmed: the request-vs-response ordering and the MAC key/input
# (memory 04 "STILL UNCONFIRMED"); envelope_secrets() is the single flip
# point.
SECRET_CLIENT = "fk0I+QfaSOz9fqGt3Ocn8T7uMzuUj2RyDtm8PQEEon6C9huxF1IkLnFzfF5kYfUm"
SECRET_GLOBAL = "xNJZYtOueWClcR04Kqb0UXa0o1Mi7SqQXRsZ5RZqG30UKSB0pFcKDgDlod6NLetb"


def envelope_secrets(client: str, direction: str) -> tuple[bytes, bytes]:
    """Base64-decoded ``(password, salt)`` pair for a client and direction.

    The target swaps the two constants between GLB/JP and request/response;
    the request-direction table below matches the confirmed constant-pool
    order (memory 04), the response ordering remains the documented default
    and is the one place to change if a live capture ever contradicts it.
    """
    clients = {"global": ("client", "global"), "japan": ("global", "client")}
    if client not in clients:
        raise ValueError(f"unknown client: {client!r}")
    if direction not in ("request", "response"):
        raise ValueError(f"unknown direction: {direction!r}")
    order = clients[client] if direction == "request" else tuple(reversed(clients[client]))
    secrets = {"client": SECRET_CLIENT, "global": SECRET_GLOBAL}
    return (base64.b64decode(secrets[order[0]]), base64.b64decode(secrets[order[1]]))


class Envelope:
    """AES-256-CBC + HMAC-MD5 envelope for one (password, salt) pair."""

    def __init__(self, password: bytes, salt: bytes) -> None:
        self.key, self.iv = evp_bytes_to_key(password, salt, KEY_SIZE, BLOCK_SIZE)

    def seal(self, payload: dict) -> tuple[str, str]:
        """Encrypt a JSON payload; return ``(base64 data, hex mac)``."""
        ciphertext = cbc_encrypt(self.key, self.iv, json.dumps(payload).encode())
        data = base64.b64encode(ciphertext).decode()
        return data, self.mac(ciphertext)

    def open(self, data: str) -> dict:
        """Decrypt base64 ciphertext back into a JSON payload."""
        try:
            ciphertext = base64.b64decode(data, validate=True)
            plaintext = cbc_decrypt(self.key, self.iv, ciphertext)
            payload = json.loads(plaintext)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"cannot decrypt envelope: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("envelope payload is not a JSON object")
        return payload

    def verify(self, data: str, sign: str) -> bool:
        """Check the mac over a base64 data field."""
        try:
            ciphertext = base64.b64decode(data, validate=True)
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(self.mac(ciphertext), sign)

    def mac(self, data: bytes) -> str:
        """The target's md5-based MAC: HMAC-MD5 keyed with the derived key."""
        return hmac.new(self.key, data, hashlib.md5).hexdigest()


def _xtime(a: int) -> int:
    """Multiply by x in GF(2^8) modulo the AES polynomial."""
    a <<= 1
    return (a ^ 0x1B) & 0xFF if a & 0x100 else a


def _gmul(a: int, b: int) -> int:
    """Multiply two elements of GF(2^8)."""
    result = 0
    while b:
        if b & 1:
            result ^= a
        a = _xtime(a)
        b >>= 1
    return result


def _build_sboxes() -> tuple[list[int], list[int]]:
    """Derive the AES S-box and its inverse (FIPS-197 §5.1.1, §5.3.2).

    Built from the multiplicative inverse in GF(2^8) followed by the affine
    transform, so no hand-typed tables can introduce typos.
    """
    exp = [0] * 255
    log = [0] * 256
    value = 1
    for i in range(255):
        exp[i] = value
        log[value] = i
        value ^= _xtime(value)  # multiply by the generator 0x03

    def inverse(a: int) -> int:
        return exp[(255 - log[a]) % 255] if a else 0

    sbox = [0] * 256
    for a in range(256):
        b = inverse(a)
        c = b
        for _ in range(4):
            c = ((c << 1) | (c >> 7)) & 0xFF
            b ^= c
        sbox[a] = b ^ 0x63
    inv = [0] * 256
    for a, s in enumerate(sbox):
        inv[s] = a
    return sbox, inv


_SBOX, _INV_SBOX = _build_sboxes()
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40]


def _expand_key256(key: bytes) -> list[list[int]]:
    """Expand a 32-byte key into the 15 AES-256 round keys (FIPS-197 §5.2)."""
    words = [list(key[4 * i:4 * i + 4]) for i in range(8)]
    for i in range(8, 60):
        temp = list(words[i - 1])
        if i % 8 == 0:
            temp = temp[1:] + temp[:1]  # RotWord
            temp = [_SBOX[b] for b in temp]  # SubWord
            temp[0] ^= _RCON[i // 8 - 1]
        elif i % 8 == 4:
            temp = [_SBOX[b] for b in temp]
        words.append([a ^ b for a, b in zip(words[i - 8], temp)])
    return [sum(words[4 * r:4 * r + 4], []) for r in range(15)]


def _xor_state(state: list[int], word: list[int], column: int) -> None:
    for row in range(4):
        state[4 * column + row] ^= word[row]


def aes_encrypt_block(key: bytes, block: bytes) -> bytes:
    """Encrypt one 16-byte block with AES-256 (FIPS-197 §5)."""
    if len(key) != KEY_SIZE:
        raise ValueError(f"key must be {KEY_SIZE} bytes, got {len(key)}")
    if len(block) != BLOCK_SIZE:
        raise ValueError(f"block must be {BLOCK_SIZE} bytes, got {len(block)}")
    round_keys = _expand_key256(key)
    # State is column-major: flat index 4*column + row.
    state = list(block)
    for column in range(4):
        _xor_state(state, round_keys[0][4 * column:4 * column + 4], column)
    for rnd in range(1, 15):
        state = [_SBOX[b] for b in state]  # SubBytes
        state = _shift_rows(state)  # ShiftRows
        if rnd != 14:
            state = _mix_columns(state)  # MixColumns
        for column in range(4):
            _xor_state(state, round_keys[rnd][4 * column:4 * column + 4], column)
    return bytes(state)


def aes_decrypt_block(key: bytes, block: bytes) -> bytes:
    """Decrypt one 16-byte block with AES-256 (FIPS-197 §5.3)."""
    if len(key) != KEY_SIZE:
        raise ValueError(f"key must be {KEY_SIZE} bytes, got {len(key)}")
    if len(block) != BLOCK_SIZE:
        raise ValueError(f"block must be {BLOCK_SIZE} bytes, got {len(block)}")
    round_keys = _expand_key256(key)
    state = list(block)
    for column in range(4):
        _xor_state(state, round_keys[14][4 * column:4 * column + 4], column)
    for rnd in range(13, -1, -1):
        state = _inv_shift_rows(state)
        state = [_INV_SBOX[b] for b in state]
        for column in range(4):
            _xor_state(state, round_keys[rnd][4 * column:4 * column + 4], column)
        if rnd:
            state = _inv_mix_columns(state)
    return bytes(state)


def _shift_rows(state: list[int]) -> list[int]:
    out = list(state)
    for row in range(1, 4):
        for column in range(4):
            out[4 * column + row] = state[4 * ((column + row) % 4) + row]
    return out


def _inv_shift_rows(state: list[int]) -> list[int]:
    out = list(state)
    for row in range(1, 4):
        for column in range(4):
            out[4 * ((column + row) % 4) + row] = state[4 * column + row]
    return out


def _mix_columns(state: list[int]) -> list[int]:
    out = list(state)
    for column in range(4):
        a = state[4 * column:4 * column + 4]
        out[4 * column + 0] = _gmul(a[0], 2) ^ _gmul(a[1], 3) ^ a[2] ^ a[3]
        out[4 * column + 1] = a[0] ^ _gmul(a[1], 2) ^ _gmul(a[2], 3) ^ a[3]
        out[4 * column + 2] = a[0] ^ a[1] ^ _gmul(a[2], 2) ^ _gmul(a[3], 3)
        out[4 * column + 3] = _gmul(a[0], 3) ^ a[1] ^ a[2] ^ _gmul(a[3], 2)
    return out


def _inv_mix_columns(state: list[int]) -> list[int]:
    out = list(state)
    for column in range(4):
        a = state[4 * column:4 * column + 4]
        out[4 * column + 0] = _gmul(a[0], 14) ^ _gmul(a[1], 11) ^ _gmul(a[2], 13) ^ _gmul(a[3], 9)
        out[4 * column + 1] = _gmul(a[0], 9) ^ _gmul(a[1], 14) ^ _gmul(a[2], 11) ^ _gmul(a[3], 13)
        out[4 * column + 2] = _gmul(a[0], 13) ^ _gmul(a[1], 9) ^ _gmul(a[2], 14) ^ _gmul(a[3], 11)
        out[4 * column + 3] = _gmul(a[0], 11) ^ _gmul(a[1], 13) ^ _gmul(a[2], 9) ^ _gmul(a[3], 14)
    return out


def evp_bytes_to_key(password: bytes, salt: bytes, key_len: int, iv_len: int) -> tuple[bytes, bytes]:
    """OpenSSL EVP_BytesToKey with MD5 (the target's ``get_key_and_iv``).

    D_i = MD5(D_{i-1} + password + salt); the first ``key_len`` bytes are the
    key, the next ``iv_len`` the IV. Matches ``openssl enc -md md5``.
    """
    if key_len < 1 or iv_len < 0:
        raise ValueError("key_len must be >= 1 and iv_len >= 0")
    derived = b""
    block = b""
    while len(derived) < key_len + iv_len:
        block = hashlib.md5(block + password + salt).digest()
        derived += block
    return derived[:key_len], derived[key_len:key_len + iv_len]


def pkcs7_pad(data: bytes) -> bytes:
    """PKCS7 pad to the AES block size (always appends a full block at 16)."""
    pad_len = BLOCK_SIZE - len(data) % BLOCK_SIZE
    return data + bytes([pad_len]) * pad_len


def pkcs7_unpad(data: bytes) -> bytes:
    """Strip PKCS7 padding; raise ValueError when it is malformed."""
    if not data or len(data) % BLOCK_SIZE:
        raise ValueError("padded data must be a non-empty whole number of blocks")
    pad_len = data[-1]
    if not 1 <= pad_len <= BLOCK_SIZE or data[-pad_len:] != bytes([pad_len]) * pad_len:
        raise ValueError("malformed PKCS7 padding")
    return data[:-pad_len]


def cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    """AES-256-CBC encrypt with PKCS7 padding."""
    data = pkcs7_pad(plaintext)
    out = bytearray()
    previous = iv
    for i in range(0, len(data), BLOCK_SIZE):
        block = bytes(a ^ b for a, b in zip(data[i:i + BLOCK_SIZE], previous))
        previous = aes_encrypt_block(key, block)
        out += previous
    return bytes(out)


def cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    """AES-256-CBC decrypt and strip PKCS7 padding."""
    if not ciphertext or len(ciphertext) % BLOCK_SIZE:
        raise ValueError("ciphertext must be a non-empty whole number of blocks")
    out = bytearray()
    previous = iv
    for i in range(0, len(ciphertext), BLOCK_SIZE):
        block = ciphertext[i:i + BLOCK_SIZE]
        out += bytes(a ^ b for a, b in zip(aes_decrypt_block(key, block), previous))
        previous = block
    return pkcs7_unpad(bytes(out))
