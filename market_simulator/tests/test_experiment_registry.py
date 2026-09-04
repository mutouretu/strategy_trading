from __future__ import annotations

import ast
import unittest
from pathlib import Path

from experiment_system import (
    DuplicateProviderError,
    ProviderRegistry,
    UnknownProviderError,
    parse_experiment_spec,
    validate_experiment,
)

from experiment_test_support import TestProvider, experiment_document


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

    def test_optional_provider_experiment_validator_is_called_once(self) -> None:
        class ValidatingProvider(TestProvider):
            def __init__(self) -> None:
                super().__init__()
                self.spec_calls = 0

            def validate_experiment_spec(self, spec) -> None:
                self.spec_calls += 1
                if spec.metadata.get("forbidden"):
                    raise ValueError("host metadata is invalid")

        provider = ValidatingProvider()
        registry = ProviderRegistry()
        registry.register(provider)
        document = experiment_document()
        document["metadata"]["forbidden"] = True
        with self.assertRaisesRegex(ValueError, "host metadata"):
            validate_experiment(parse_experiment_spec(document), registry)
        self.assertEqual(provider.spec_calls, 1)


if __name__ == "__main__":
    unittest.main()
