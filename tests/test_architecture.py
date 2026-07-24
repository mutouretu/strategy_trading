from __future__ import annotations

import ast
import unittest
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "gridtrader"
CANONICAL_PACKAGES = {
    "application",
    "domain",
    "infrastructure",
    "interfaces",
    "ports",
    "runtime",
    "shared",
}
LEGACY_MODULES = {
    "api",
    "binance",
    "config",
    "engine",
    "exchange",
    "grid_math",
    "position_coordinator",
    "price_format",
    "scheduler",
    "service",
    "snapshot_exchange",
    "store",
    "supervisor",
    "web_client",
    "worker",
}


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_canonical_modules_do_not_import_legacy_compatibility_modules(self):
        violations: list[str] = []
        for package in sorted(CANONICAL_PACKAGES):
            for path in (PACKAGE_ROOT / package).glob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ImportFrom):
                        continue
                    module = node.module or ""
                    first = module.split(".", 1)[0]
                    if node.level >= 2 and first in LEGACY_MODULES:
                        violations.append(f"{path.relative_to(PACKAGE_ROOT)} -> {'.' * node.level}{module}")
                    if module.startswith("gridtrader."):
                        imported = module.split(".", 2)[1]
                        if imported in LEGACY_MODULES:
                            violations.append(f"{path.relative_to(PACKAGE_ROOT)} -> {module}")
        self.assertEqual(violations, [], "canonical code imported compatibility modules")

    def test_domain_is_independent_of_other_project_layers(self):
        violations: list[str] = []
        for path in (PACKAGE_ROOT / "domain").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level >= 2:
                    violations.append(f"{path.name}:{node.lineno}")
        self.assertEqual(violations, [], "domain must not depend on outer layers")


if __name__ == "__main__":
    unittest.main()
