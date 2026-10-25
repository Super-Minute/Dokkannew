"""Account save loading (memory 01 field list, memory 05 signin record)."""
import json
import tempfile
import unittest
from pathlib import Path

from dokkan.adapters.save_store import SaveRecord, load_save
from dokkan.application.errors import CollectionError


def write(directory: str, text: str) -> Path:
    path = Path(directory) / "save.json"
    path.write_text(text)
    return path


class LoadSaveTests(unittest.TestCase):
    def test_reads_minimal_record(self):
        record = {"identifier": "kyle", "access_token": "at", "secret": "s",
                  "user_id": 12345, "client": "glb"}
        with tempfile.TemporaryDirectory() as d:
            save = load_save(write(d, json.dumps(record)))
        self.assertEqual(
            (save.identifier, save.access_token, save.secret, save.user_id, save.client),
            ("kyle", "at", "s", 12345, "global"))

    def test_extra_fields_are_preserved(self):
        record = {"identifier": "k", "access_token": "a", "secret": "s", "client": "japan",
                  "user_rank": 250, "device_token": "AgAAAA"}
        with tempfile.TemporaryDirectory() as d:
            save = load_save(write(d, json.dumps(record)))
        self.assertEqual(save.extras["user_rank"], 250)
        self.assertEqual(save.extras["device_token"], "AgAAAA")

    def test_client_defaults_to_global_and_rejects_unknown(self):
        base = {"identifier": "k", "access_token": "a", "secret": "s"}
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(load_save(write(d, json.dumps(base))).client, "global")
        base["client"] = "mars"
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(CollectionError):
                load_save(write(d, json.dumps(base)))

    def test_missing_required_fields_are_collection_errors(self):
        base = {"identifier": "k", "access_token": "a", "secret": "s", "client": "glb"}
        for key in ("identifier", "access_token", "secret"):
            broken = {k: v for k, v in base.items() if k != key}
            with tempfile.TemporaryDirectory() as d, self.subTest(key):
                with self.assertRaises(CollectionError):
                    load_save(write(d, json.dumps(broken)))

    def test_unreadable_or_malformed_files_are_collection_errors(self):
        for text in ("{not json", "[]", "null"):
            with tempfile.TemporaryDirectory() as d, self.subTest(text):
                with self.assertRaises(CollectionError):
                    load_save(write(d, text))
        with self.assertRaises(CollectionError):
            load_save(Path("/nonexistent/save.json"))


if __name__ == "__main__":
    unittest.main()
