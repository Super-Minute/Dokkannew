"""Enforces the dependency rule: inner rings never import outer rings."""
import ast
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "dokkan"
ALLOWED = {
    "domain": set(),
    "application": {"domain"},
    "service": {"domain", "application"},
    "adapters": {"domain", "application"},
    "cli": {"domain", "application", "adapters", "service"},
}


def imported_rings(path: Path) -> set[str]:
    rings = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        for name in names:
            parts = name.split(".")
            if parts[0] == "dokkan" and len(parts) > 1:
                rings.add(parts[1])
    return rings


class DependencyRuleTests(unittest.TestCase):
    def test_no_inward_violations(self):
        for ring, allowed in ALLOWED.items():
            for path in (SRC / ring).rglob("*.py"):
                with self.subTest(file=str(path.relative_to(SRC))):
                    self.assertLessEqual(imported_rings(path) - {ring}, allowed)

    def test_domain_has_no_relative_imports_outside_itself(self):
        for path in (SRC / "domain").rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.level > 1:
                    self.fail(f"{path.name} reaches outside the domain ring")


if __name__ == "__main__":
    unittest.main()
