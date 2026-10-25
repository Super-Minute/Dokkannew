import io
import json
import tempfile
import unittest
from pathlib import Path

from dokkan.adapters.json_collection import load_collection
from dokkan.adapters.simulated_gateway import SimulatedGateway
from dokkan.application.errors import CollectionError
from dokkan.application.settings import Settings
from dokkan.cli.commands import CommandRunner, UsageError

CARDS = [
    {"id": 1, "name": "Hero", "rarity": "LR", "type": "STR", "level": 80, "attack": 9000},
    {"id": 2, "name": "Recruit", "rarity": "N", "type": "AGL", "attack": 100},
    {"id": 3, "name": "Soldier", "rarity": "SR", "type": "TEQ", "attack": 1500},
    {"id": 4, "name": "Pet", "rarity": "R", "type": "INT", "favorite": True},
]


def write(d, data) -> Path:
    p = Path(d) / "cards.json"
    p.write_text(json.dumps(data))
    return p


def run(line) -> str:
    out = io.StringIO()
    CommandRunner(SimulatedGateway(), Settings(), out, load_collection).run_line(line)
    return out.getvalue()


class LoaderTests(unittest.TestCase):
    def test_loads_cards(self):
        with tempfile.TemporaryDirectory() as d:
            cards = load_collection(write(d, CARDS))
        self.assertEqual([c.id for c in cards], [1, 2, 3, 4])

    def test_rejects_bad_input(self):
        bad = [{"id": 1, "name": "x", "rarity": "ZZ", "type": "STR"},
               {"id": "1", "name": "x", "rarity": "N", "type": "STR"},
               {"id": 1, "name": "", "rarity": "N", "type": "STR"},
               [CARDS[0], CARDS[0]], {"not": "a list"}]
        for data in bad:
            with tempfile.TemporaryDirectory() as d, self.assertRaises(CollectionError, msg=data):
                load_collection(write(d, [data] if isinstance(data, dict) and "id" in data else data))

    def test_missing_file(self):
        with self.assertRaises(CollectionError):
            load_collection(Path("/nonexistent.json"))


class CommandTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.path = write(self._d.name, CARDS)

    def test_box_summary(self):
        out = run(f"box {self.path}")
        self.assertIn("4 cards, 2 sellable", out)
        self.assertIn("LR 1", out)

    def test_plan_sell_skips_favorites_and_does_not_sell(self):
        out = run(f"plan-sell {self.path}")
        self.assertEqual(out.count("\n"), 2)
        self.assertNotIn("Pet", out)

    def test_team_respects_restrictions(self):
        out = run(f"team {self.path} --max-rarity SR --size 2")
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("3 "))

    def test_team_usage_errors(self):
        for line in (f"team {self.path} --size", f"team {self.path} --size x", "team", f"team {self.path} --bogus 1"):
            with self.assertRaises(UsageError, msg=line):
                run(line)
