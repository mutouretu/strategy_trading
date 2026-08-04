from __future__ import annotations

import inspect
import unittest
from pathlib import Path

import strategy_simulation  # noqa: F401 - activates local checkout imports

from experiment_system import load_experiment_spec, validate_experiment

from strategy_simulation.experiment_provider import (
    StrategiesSimulationProvider,
    build_provider_registry,
)


class StrategyExperimentProviderTests(unittest.TestCase):
    def test_baseline_spec_is_six_runs_and_three_strategies(self) -> None:
        spec = load_experiment_spec(
            Path(__file__).parents[1]
            / "experiments"
            / "strategy_baselines_v1.json"
        )
        report = validate_experiment(spec, build_provider_registry())
        self.assertEqual(report.scenario_count, 3)
        self.assertEqual(report.run_count, 6)

    def test_generic_provider_has_no_concrete_strategy_type_branch(self) -> None:
        source = inspect.getsource(StrategiesSimulationProvider._build_components)
        self.assertNotIn("strategy_type ==", source)
        self.assertNotIn("isinstance(", source)
        self.assertNotIn("grid_experiments", inspect.getsource(
            inspect.getmodule(StrategiesSimulationProvider)
        ))
        descriptors = build_provider_registry().component_descriptors
        self.assertEqual(len(descriptors), 5)
        self.assertTrue(all(item.get("formulae") for item in descriptors))


if __name__ == "__main__":
    unittest.main()
