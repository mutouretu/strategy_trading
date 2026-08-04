from __future__ import annotations

import ast
import unittest
from pathlib import Path

from experiment_system import (
    DuplicateProviderError,
    ProviderRegistry,
    UnknownProviderError,
)

from experiment_test_support import TestProvider


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ProviderRegistryTests(unittest.TestCase):
    def test_duplicate_and_unknown_provider_ids_are_rejected(self) -> None:
        registry = ProviderRegistry()
        registry.register(TestProvider())

        with self.assertRaises(DuplicateProviderError):
            registry.register(TestProvider())
        with self.assertRaises(UnknownProviderError):
            registry.get("missing/v1")

    def test_experiment_system_does_not_import_grid_application(self) -> None:
        package_root = (
            PROJECT_ROOT
            / "packages"
            / "experiment_system"
            / "src"
            / "experiment_system"
        )
        imported_roots: set[str] = set()
        for source_path in package_root.glob("*.py"):
            tree = ast.parse(
                source_path.read_text(encoding="utf-8"),
                filename=str(source_path),
            )
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_roots.update(
                        alias.name.split(".", 1)[0]
                        for alias in node.names
                    )
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    if node.module:
                        imported_roots.add(node.module.split(".", 1)[0])

        self.assertNotIn("grid_trading", imported_roots)
        self.assertNotIn("grid_rule", imported_roots)
        self.assertNotIn("grid_strategies", imported_roots)


if __name__ == "__main__":
    unittest.main()
