from __future__ import annotations

import importlib
import unittest

import simulation_runtime


class LegacyExecutionApiRemovalTests(unittest.TestCase):
    def test_order_matching_types_are_not_public(self) -> None:
        for name in (
            "ActiveOrder",
            "BarTouchExecutionModel",
            "OrderRecord",
            "OrderStatus",
            "OrderType",
            "SimOrder",
            "SimulationDecision",
            "SimulationDecisionPort",
        ):
            with self.subTest(name):
                self.assertFalse(hasattr(simulation_runtime, name))

    def test_legacy_port_and_execution_modules_are_removed(self) -> None:
        for module_name in (
            "simulation_runtime.decision",
            "simulation_runtime.execution",
        ):
            with self.subTest(module_name):
                with self.assertRaises(ModuleNotFoundError):
                    importlib.import_module(module_name)


if __name__ == "__main__":
    unittest.main()
