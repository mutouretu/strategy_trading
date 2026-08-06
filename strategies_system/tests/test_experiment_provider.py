from __future__ import annotations

import inspect
import unittest
from pathlib import Path

import strategy_simulation  # noqa: F401 - activates local checkout imports

from experiment_system import ComponentSpec, load_experiment_spec, validate_experiment

from strategy_simulation.components import build_market_source

from strategy_simulation.experiment_provider import (
    StrategiesSimulationProvider,
    build_provider_registry,
)


class StrategyExperimentProviderTests(unittest.TestCase):
    def test_locked_market_provider_rejects_holdout_before_loading_data(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be HOLDOUT"):
            build_market_source(
                ComponentSpec(
                    key="forbidden-holdout",
                    type="locked-market-path/v1",
                    parameters={
                        "path_set_id": "probe-set-v1",
                        "path_set_lock_fingerprint": "1" * 64,
                        "manifest_sha256": "2" * 64,
                        "path_key": "probe:HOLDOUT:31",
                        "scenario_id": "probe",
                        "role": "HOLDOUT",
                        "market_seed": 31,
                        "origin": "SYNTHETIC",
                        "market_path_id": "3" * 20,
                        "path": "market_environments/generated/missing.parquet",
                        "instrument": "BTCUSD_PERP",
                        "interval": "1h",
                        "frame_count": 1,
                        "content_sha256": "3" * 64,
                        "file_sha256": "4" * 64,
                    },
                )
            )

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
