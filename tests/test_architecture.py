from __future__ import annotations

import ast
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER_ROOT = PROJECT_ROOT / "grid_server"
RESEARCH_PACKAGE_ROOTS = (
    PROJECT_ROOT / "grid_rule",
    PROJECT_ROOT / "grid_strategies",
)
STRATEGY_ROOT = PROJECT_ROOT / "grid_strategies"
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
            for path in (SERVER_ROOT / package).glob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ImportFrom):
                        continue
                    module = node.module or ""
                    first = module.split(".", 1)[0]
                    if node.level >= 2 and first in LEGACY_MODULES:
                        violations.append(f"{path.relative_to(SERVER_ROOT)} -> {'.' * node.level}{module}")
                    if module.startswith("grid_server."):
                        imported = module.split(".", 2)[1]
                        if imported in LEGACY_MODULES:
                            violations.append(f"{path.relative_to(SERVER_ROOT)} -> {module}")
        self.assertEqual(violations, [], "canonical code imported compatibility modules")

    def test_domain_is_independent_of_other_project_layers(self):
        violations: list[str] = []
        for path in (SERVER_ROOT / "domain").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level >= 2:
                    violations.append(f"{path.name}:{node.lineno}")
        self.assertEqual(violations, [], "domain must not depend on outer layers")

    def test_rule_and_strategy_packages_do_not_import_grid_server(self):
        violations: list[str] = []
        for package_root in RESEARCH_PACKAGE_ROOTS:
            for path in package_root.rglob("*.py"):
                tree = ast.parse(
                    path.read_text(encoding="utf-8"),
                    filename=str(path),
                )
                for node in ast.walk(tree):
                    modules: list[str] = []
                    if isinstance(node, ast.Import):
                        modules.extend(alias.name for alias in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.level == 0:
                        modules.append(node.module or "")
                    for module in modules:
                        if module.split(".", 1)[0] == "grid_server":
                            violations.append(
                                f"{path.relative_to(PROJECT_ROOT)}:"
                                f"{node.lineno} -> {module}"
                            )
        self.assertEqual(
            violations,
            [],
            "rule and strategy packages must not depend on grid_server",
        )

    def test_strategy_core_does_not_import_simulation_runtime(self):
        forbidden = {
            "market_protocol",
            "market_simulator",
            "simulation_runtime",
        }
        violations: list[str] = []
        for path in STRATEGY_ROOT.glob("*.py"):
            tree = ast.parse(
                path.read_text(encoding="utf-8"),
                filename=str(path),
            )
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    modules.append(node.module or "")
                for module in modules:
                    if module.split(".", 1)[0] in forbidden:
                        violations.append(
                            f"{path.relative_to(PROJECT_ROOT)}:"
                            f"{node.lineno} -> {module}"
                        )
        self.assertEqual(
            violations,
            [],
            "strategy core must not depend on simulation runtime",
        )


if __name__ == "__main__":
    unittest.main()
