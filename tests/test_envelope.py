"""Cleanroom crypto for the game request envelope (memory 04).

Vectors: FIPS-197 appendix C.3 (AES-256) and OpenSSL-generated references
(`openssl enc -aes-256-cbc -md md5`, OpenSSL 3.5.9 on this host).
"""
import base64
import hashlib
import hmac
import json
import unittest

from dokkan.adapters.envelope import (
    Envelope,
    SECRET_CLIENT,
    SECRET_GLOBAL,
    aes_decrypt_block,
    aes_encrypt_block,
    cbc_decrypt,
    cbc_encrypt,
    envelope_secrets,
    evp_bytes_to_key,
    pkcs7_pad,
    pkcs7_unpad,
)

FIPS_KEY = bytes.fromhex(
    "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
)
FIPS_PLAIN = bytes.fromhex("00112233445566778899aabbccddeeff")
FIPS_CIPHER = bytes.fromhex("8ea2b7ca516745bfeafc49904b496089")


class AesBlockTests(unittest.TestCase):
    def test_encrypts_fips197_aes256_vector(self):
        self.assertEqual(aes_encrypt_block(FIPS_KEY, FIPS_PLAIN), FIPS_CIPHER)

    def test_decrypts_fips197_aes256_vector(self):
        self.assertEqual(aes_decrypt_block(FIPS_KEY, FIPS_CIPHER), FIPS_PLAIN)

    def test_rejects_non_256_bit_keys(self):
        with self.assertRaises(ValueError):
            aes_encrypt_block(b"short", FIPS_PLAIN)

    def test_rejects_non_block_sized_input(self):
        for bad in (b"", b"123456789", FIPS_PLAIN + b"x"):
            with self.subTest(len(bad)), self.assertRaises(ValueError):
                aes_encrypt_block(FIPS_KEY, bad)


# Generated with: printf 'test payload 123' | openssl enc -aes-256-cbc -md md5 ...
OPENSSL_UNSALTED = {
    "password": b"topsecret",
    "salt": b"",
    "key": bytes.fromhex("EA847988BA59727DBF4E34EE75726DC34D7B80C8597180AD34EBEE5C4CA6659D"),
    "iv": bytes.fromhex("C558FDFB2B5BBBC5F7A07696DEF854CC"),
    "ciphertext": bytes.fromhex(
        "3baa255c731f68484ad56170be5f4e38d8aee398f9faba472fe3e6aa9bde2b3f"
    ),
}
OPENSSL_SALTED = {
    "password": b"topsecret",
    "salt": bytes.fromhex("0123456789abcdef"),
    "key": bytes.fromhex("CC9CD977AB5B9BD0D7502BAC3F3377267AE26A9D58B02B565D28F05453491370"),
    "iv": bytes.fromhex("7D7B29321B29DCBA4A7A67938A2652FC"),
}


class EvpBytesToKeyTests(unittest.TestCase):
    def test_matches_openssl_md5_derivation_without_salt(self):
        v = OPENSSL_UNSALTED
        self.assertEqual(evp_bytes_to_key(v["password"], v["salt"], 32, 16),
                         (v["key"], v["iv"]))

    def test_matches_openssl_md5_derivation_with_salt(self):
        v = OPENSSL_SALTED
        self.assertEqual(evp_bytes_to_key(v["password"], v["salt"], 32, 16),
                         (v["key"], v["iv"]))

    def test_rejects_impossible_lengths(self):
        with self.assertRaises(ValueError):
            evp_bytes_to_key(b"x", b"", 0, 0)


class Pkcs7Tests(unittest.TestCase):
    def test_pads_to_next_block_boundary(self):
        self.assertEqual(pkcs7_pad(b"abc"), b"abc" + b"\r" * 13)
        self.assertEqual(pkcs7_pad(b"x" * 16), b"x" * 16 + b"\x10" * 16)

    def test_unpad_reverses_pad(self):
        for n in range(0, 33):
            self.assertEqual(pkcs7_unpad(pkcs7_pad(b"a" * n)), b"a" * n)

    def test_rejects_invalid_padding(self):
        for bad in (b"", b"abc", b"a" * 15 + b"\x00"):
            with self.subTest(bad), self.assertRaises(ValueError):
                pkcs7_unpad(bad)


class CbcTests(unittest.TestCase):
    def test_matches_openssl_ciphertext(self):
        v = OPENSSL_UNSALTED
        self.assertEqual(cbc_encrypt(v["key"], v["iv"], b"test payload 123"),
                         v["ciphertext"])

    def test_roundtrips_arbitrary_lengths(self):
        key, iv = OPENSSL_UNSALTED["key"], OPENSSL_UNSALTED["iv"]
        for n in (0, 1, 15, 16, 17, 100):
            with self.subTest(n):
                self.assertEqual(cbc_decrypt(key, iv, cbc_encrypt(key, iv, b"z" * n)),
                                 b"z" * n)

    def test_rejects_misaligned_ciphertext(self):
        with self.assertRaises(ValueError):
            cbc_decrypt(OPENSSL_UNSALTED["key"], OPENSSL_UNSALTED["iv"], b"short")


class EnvelopeTests(unittest.TestCase):
    def test_seal_open_roundtrip(self):
        env = Envelope(b"password", b"salt")
        data, sign = env.seal({"user_id": 1, "name": "kyle"})
        self.assertEqual(env.open(data), {"user_id": 1, "name": "kyle"})
        self.assertIsInstance(sign, str)

    def test_seal_data_is_base64_ciphertext_and_sign_is_hmac_md5_of_it(self):
        env = Envelope(b"password", b"salt")
        data, sign = env.seal({"a": 1})
        ciphertext = base64.b64decode(data)
        self.assertEqual(cbc_decrypt(env.key, env.iv, ciphertext), b'{"a": 1}')
        self.assertEqual(sign, hmac.new(env.key, ciphertext, hashlib.md5).hexdigest())

    def test_open_rejects_garbage(self):
        env = Envelope(b"password", b"salt")
        for bad in ("", "!!!", base64.b64encode(b"not json at all").decode()):
            with self.subTest(bad), self.assertRaises(ValueError):
                env.open(bad)

    def test_verify_sign_checks_mac(self):
        env = Envelope(b"password", b"salt")
        data, sign = env.seal({"a": 1})
        self.assertTrue(env.verify(data, sign))
        self.assertFalse(env.verify(data, "0" * 32))
        self.assertFalse(env.verify(data + "A==", sign))


class SecretTests(unittest.TestCase):
    def test_secrets_are_the_two_recovered_64_char_constants(self):
        self.assertEqual(
            (SECRET_CLIENT, SECRET_GLOBAL),
            ("fk0I+QfaSOz9fqGt3Ocn8T7uMzuUj2RyDtm8PQEEon6C9huxF1IkLnFzfF5kYfUm",
             "xNJZYtOueWClcR04Kqb0UXa0o1Mi7SqQXRsZ5RZqG30UKSB0pFcKDgDlod6NLetb"))

    def test_secrets_factory_decodes_and_swaps_by_direction(self):
        for client in ("global", "japan"):
            request = envelope_secrets(client, "request")
            response = envelope_secrets(client, "response")
            self.assertNotEqual(request, response)
            self.assertEqual(sorted([request, response]),
                             sorted([(base64.b64decode(SECRET_CLIENT), base64.b64decode(SECRET_GLOBAL)),
                                     (base64.b64decode(SECRET_GLOBAL), base64.b64decode(SECRET_CLIENT))]))
            self.assertTrue(all(isinstance(x, bytes) for pair in (request, response) for x in pair))

    def test_secrets_factory_rejects_unknown_client_or_direction(self):
        for client, direction in (("mars", "request"), ("global", "sideways")):
            with self.subTest((client, direction)), self.assertRaises(ValueError):
                envelope_secrets(client, direction)


if __name__ == "__main__":
    unittest.main()
