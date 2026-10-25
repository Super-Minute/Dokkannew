"""Version cache behaviour recovered in memory 08 (version.json)."""
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dokkan.adapters.version_store import (
    ClientVersions, FileVersionSource, VersionFetchError, VersionStore,
    fetch_version_payload, parse_version_payload,
)


class ParsePayloadTests(unittest.TestCase):
    def test_parses_json_object(self):
        text = json.dumps({"version_code_global": "6.6.0-" + "a" * 64, "timestamp": "1.5"})
        self.assertEqual(parse_version_payload(text)["version_code_global"], "6.6.0-" + "a" * 64)

    def test_parses_key_value_lines(self):
        text = "version_code_global=6.6.0-abc\nasset_version_japan=1760000000\n"
        self.assertEqual(parse_version_payload(text),
                         {"version_code_global": "6.6.0-abc", "asset_version_japan": "1760000000"})

    def test_rejects_garbage(self):
        for text in ("[1, 2]", "just some words", "{\"k\": 5}", ""):
            with self.subTest(text), self.assertRaises(ValueError):
                parse_version_payload(text)


class VersionStoreTests(unittest.TestCase):
    def _store(self, directory: str, **files: str) -> VersionStore:
        for name, text in files.items():
            (Path(directory) / name).write_text(text)
        return VersionStore(Path(directory) / "version.json")

    def test_missing_file_is_empty_and_get_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)
            self.assertEqual(store.load(), {})
            self.assertIsNone(store.get("global"))

    def test_reads_json_cache_with_string_values(self):
        cache = {"version_code_global": "6.6.0-x", "asset_version_global": "17",
                 "database_version_global": "18", "unrelated": "kept"}
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d, **{"version.json": json.dumps(cache)})
            self.assertEqual(store.get("global"),
                             ClientVersions("6.6.0-x", "17", "18"))
            self.assertIsNone(store.get("japan"))

    def test_get_accepts_client_aliases(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d, **{"version.json": json.dumps({"version_code_japan": "7.0.0-y"})})
            self.assertEqual(store.get("japan").version_code, "7.0.0-y")
            self.assertEqual(store.get("jp").version_code, "7.0.0-y")
            self.assertEqual(store.get("glb"), store.get("global"))

    def test_merge_writes_and_preserves_other_keys(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d, **{"version.json": json.dumps({"keep": "me"})})
            store.merge({"version_code_global": "6.7.0-z"})
            on_disk = json.loads((Path(d) / "version.json").read_text())
            self.assertEqual(on_disk["keep"], "me")
            self.assertEqual(on_disk["version_code_global"], "6.7.0-z")
            self.assertIn("timestamp", on_disk)
            self.assertEqual(store.get("global").version_code, "6.7.0-z")

    def test_concurrent_merges_do_not_lose_updates(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d)
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda i: store.merge({f"key_{i}": str(i)}), range(64)))
            on_disk = json.loads((Path(d) / "version.json").read_text())
            self.assertEqual(len([k for k in on_disk if k.startswith("key_")]), 64)

    def test_legacy_db_version_txt_is_imported_then_removed(self):
        with tempfile.TemporaryDirectory() as d:
            store = self._store(d, **{"db_version.txt": "6.6.0-legacy\n"})
            store.load()
            self.assertFalse((Path(d) / "db_version.txt").exists())
            self.assertEqual(store.get("global").version_code, "6.6.0-legacy")


class _FeedServer:
    """A real local HTTP server playing the version feed (memory 03/08)."""

    def __init__(self, body: bytes, status: int = 200, content_type: str = "text/plain"):
        self.body, self.status = body, status
        self.hits = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                server.hits.append(self.path)
                self.send_response(server.status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(server.body)))
                self.end_headers()
                self.wfile.write(server.body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/version_feed"

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self.thread.join(timeout=5)


class FetchVersionPayloadTests(unittest.TestCase):
    def test_gets_json_feed_over_http(self):
        feed = json.dumps({"version_code_global": "6.7.0-" + "b" * 64}).encode()
        with _FeedServer(feed, content_type="application/json") as url:
            self.assertEqual(fetch_version_payload(url)["version_code_global"], "6.7.0-" + "b" * 64)

    def test_gets_key_value_feed_over_http(self):
        feed = b"version_code_japan=7.0.0-abc\nasset_version_japan=42\n"
        with _FeedServer(feed) as url:
            self.assertEqual(fetch_version_payload(url),
                             {"version_code_japan": "7.0.0-abc", "asset_version_japan": "42"})

    def test_non_200_is_an_error(self):
        with _FeedServer(b"gone", status=503) as url:
            with self.assertRaises(VersionFetchError):
                fetch_version_payload(url)

    def test_unreachable_feed_is_an_error(self):
        with self.assertRaises(VersionFetchError):
            fetch_version_payload("http://127.0.0.1:1/nope", timeout=2.0)

    def test_garbage_payload_is_an_error(self):
        with _FeedServer(b"total nonsense") as url:
            with self.assertRaises(VersionFetchError):
                fetch_version_payload(url)


class FileVersionSourceRemoteTests(unittest.TestCase):
    def test_refresh_fetches_merges_and_updates_code(self):
        feed = b"version_code_global=6.8.0-" + b"c" * 64 + b"\n"
        with tempfile.TemporaryDirectory() as d, _FeedServer(feed) as url:
            store = VersionStore(Path(d) / "version.json")
            store.merge({"version_code_global": "6.6.0-old", "keep": "me"})
            source = FileVersionSource(store, "global", url=url)
            self.assertEqual(source.code, "6.6.0-old")
            source.refresh()
            self.assertEqual(source.code, "6.8.0-" + "c" * 64)
            on_disk = json.loads((Path(d) / "version.json").read_text())
            self.assertEqual(on_disk["keep"], "me")  # locked merge, not replace
            self.assertIn("timestamp", on_disk)

    def test_refresh_without_url_keeps_cache_only_behaviour(self):
        with tempfile.TemporaryDirectory() as d:
            store = VersionStore(Path(d) / "version.json")
            store.merge({"version_code_global": "6.6.0-old"})
            source = FileVersionSource(store, "global")
            source.refresh()
            self.assertEqual(source.code, "6.6.0-old")

    def test_refresh_failure_leaves_cached_code_intact(self):
        with tempfile.TemporaryDirectory() as d:
            store = VersionStore(Path(d) / "version.json")
            store.merge({"version_code_global": "6.6.0-old"})
            source = FileVersionSource(store, "global", url="http://127.0.0.1:1/nope")
            source._timeout = 2.0
            with self.assertRaises(VersionFetchError):
                source.refresh()
            self.assertEqual(source.code, "6.6.0-old")


if __name__ == "__main__":
    unittest.main()
