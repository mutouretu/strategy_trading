from __future__ import annotations

import inspect
import json
import unittest
from pathlib import Path

import strategy_simulation  # noqa: F401 - activates local checkout imports

from experiment_system import (
    CodeRevision,
    ComponentSpec,
    load_experiment_spec,
    parse_experiment_spec,
    plan_experiment,
    validate_experiment,
)

from strategy_simulation.components import build_market_source

from strategy_simulation.experiment_provider import (
    StrategiesSimulationProvider,
    build_provider_registry,
)


class StrategyExperimentProviderTests(unittest.TestCase):
    @staticmethod
    def ladder_baseline_document() -> dict[str, object]:
        path = (
            Path(__file__).parents[1]
            / "experiments"
            / "btc_hold_coinm_ladder_baseline_v1.json"
        )
        return json.loads(path.read_text(encoding="utf-8"))

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

    def test_btc_hold_coinm_ladder_baseline_is_exactly_one_run(self) -> None:
        spec = load_experiment_spec(
            Path(__file__).parents[1]
            / "experiments"
            / "btc_hold_coinm_ladder_baseline_v1.json"
        )
        registry = build_provider_registry()
        report = validate_experiment(spec, registry)
        plan = plan_experiment(
            spec,
            registry,
            code_revisions={
                "strategy_trading": CodeRevision(commit="a" * 40)
            },
        )

        self.assertEqual(report.scenario_count, 1)
        self.assertEqual(report.run_count, 1)
        self.assertEqual(len(plan.runs), 1)
        run = plan.runs[0]
        self.assertEqual(run.seed, 0)
        self.assertEqual(
            run.configuration.market.type,
            "locked-market-path/v1",
        )
        self.assertEqual(
            run.configuration.strategy.type,
            "coinm-long-take-profit-ladder/v1",
        )
        self.assertEqual(
            run.configuration.strategy.parameters[
                "strategy_definition_type"
            ],
            "entry-then-ladder-exit/v1",
        )
        self.assertEqual(
            run.configuration.strategy.parameters["strategy_parameters"]
            ["exit_level_count"],
            10,
        )
        self.assertEqual(
            run.configuration.account.parameters["futures_wallet_btc"],
            "1.1",
        )

    def test_effective_leverage_scan_is_six_positions_and_one_seed(self) -> None:
        spec = load_experiment_spec(
            Path(__file__).parents[1]
            / "experiments"
            / "btc_coinm_effective_leverage_scan_v1.json"
        )
        registry = build_provider_registry()
        report = validate_experiment(spec, registry)
        plan = plan_experiment(
            spec,
            registry,
            code_revisions={
                "strategy_trading": CodeRevision(commit="a" * 40)
            },
        )

        self.assertEqual(report.scenario_count, 6)
        self.assertEqual(report.run_count, 6)
        self.assertEqual({run.seed for run in plan.runs}, {0})
        self.assertEqual(
            {
                run.configuration.strategy.parameters[
                    "entry_effective_leverage"
                ]
                for run in plan.runs
            },
            {"0.5", "1.0", "1.5", "2.0", "2.5", "3.0"},
        )
        self.assertEqual(
            {
                run.configuration.strategy.parameters[
                    "strategy_parameters"
                ]["entry_sizing_parameters"]["entry_effective_leverage"]
                for run in plan.runs
            },
            {"0.5", "1.0", "1.5", "2.0", "2.5", "3.0"},
        )
        self.assertTrue(
            all(
                run.configuration.account.parameters["leverage"] == "10"
                for run in plan.runs
            )
        )

    def test_aave_funding_study_is_five_rates_on_one_market_path(self) -> None:
        spec = load_experiment_spec(
            Path(__file__).parents[1]
            / "experiments"
            / "aave_coinm_funding_rate_study_v1.json"
        )
        registry = build_provider_registry()
        report = validate_experiment(spec, registry)
        plan = plan_experiment(
            spec,
            registry,
            code_revisions={
                "strategy_trading": CodeRevision(commit="a" * 40)
            },
        )

        self.assertEqual(report.scenario_count, 5)
        self.assertEqual(report.run_count, 5)
        # The selected market path already carries market seed 4105; the
        # experiment Run itself deliberately uses one fixed execution seed.
        self.assertEqual({run.seed for run in plan.runs}, {0})
        self.assertEqual(
            {
                run.configuration.execution.parameters["funding_rate"]
                for run in plan.runs
            },
            {
                "-0.00015",
                "0",
                "0.0001421008888888889",
                "0.0003",
                "0.0006",
            },
        )
        self.assertEqual(
            {run.configuration.market.key for run in plan.runs},
            {"aave-long-range-train-4105"},
        )
        self.assertTrue(
            all(
                run.configuration.account.parameters["base_asset"] == "AAVE"
                and run.configuration.account.parameters[
                    "futures_wallet_base"
                ] == "135.07285718"
                and run.configuration.execution.parameters["fee_asset"]
                == "AAVE"
                for run in plan.runs
            )
        )

    def test_ladder_experiment_requires_registered_strategy_definition(
        self,
    ) -> None:
        document = self.ladder_baseline_document()
        parameters = document["scenario_groups"][0]["strategies"][0][
            "parameters"
        ]
        parameters["strategy_definition_type"] = "missing-strategy/v1"
        with self.assertRaisesRegex(ValueError, "is not registered"):
            validate_experiment(
                parse_experiment_spec(document),
                build_provider_registry(),
            )

    def test_ladder_experiment_requires_explicit_strategy_reference(
        self,
    ) -> None:
        document = self.ladder_baseline_document()
        parameters = document["scenario_groups"][0]["strategies"][0][
            "parameters"
        ]
        del parameters["strategy_definition_type"]
        with self.assertRaisesRegex(
            ValueError,
            "requires strategy_definition_type",
        ):
            validate_experiment(
                parse_experiment_spec(document),
                build_provider_registry(),
            )

    def test_nested_strategy_parameters_are_validated_before_run(self) -> None:
        document = self.ladder_baseline_document()
        parameters = document["scenario_groups"][0]["strategies"][0][
            "parameters"
        ]["strategy_parameters"]
        parameters["unknown_parameter"] = "not-allowed"
        with self.assertRaisesRegex(ValueError, "unknown parameters"):
            validate_experiment(
                parse_experiment_spec(document),
                build_provider_registry(),
            )

    def test_experiment_kind_is_validated_by_strategy_provider(self) -> None:
        document = self.ladder_baseline_document()
        document["metadata"]["experiment_kind"] = "UNKNOWN_KIND"
        with self.assertRaisesRegex(ValueError, "experiment_kind"):
            validate_experiment(
                parse_experiment_spec(document),
                build_provider_registry(),
            )

        document["metadata"]["experiment_kind"] = "PARAMETER_STUDY"
        with self.assertRaisesRegex(ValueError, "parameter axis"):
            validate_experiment(
                parse_experiment_spec(document),
                build_provider_registry(),
            )

    def test_generic_provider_has_no_concrete_strategy_type_branch(self) -> None:
        source = inspect.getsource(StrategiesSimulationProvider._build_components)
        self.assertNotIn("strategy_type ==", source)
        self.assertNotIn("isinstance(", source)
        self.assertNotIn("grid_experiments", inspect.getsource(
            inspect.getmodule(StrategiesSimulationProvider)
        ))
        descriptors = build_provider_registry().component_descriptors
        self.assertEqual(len(descriptors), 8)
        self.assertEqual(
            {
                item["type"]
                for item in descriptors
                if item["kind"] == "strategy-definition"
            },
            {"entry-then-ladder-exit/v1"},
        )
        self.assertEqual(
            {item["type"] for item in descriptors if item["kind"] == "trading-rule"},
            {"initial-entry/v1", "ladder-take-profit/v1"},
        )
        self.assertTrue(
            all(
                item.get("formulae")
                for item in descriptors
                if item["kind"] != "strategy-definition"
            )
        )
        strategy_definition = next(
            item
            for item in descriptors
            if item["kind"] == "strategy-definition"
        )
        self.assertEqual(len(strategy_definition["rule_composition"]), 2)


if __name__ == "__main__":
    unittest.main()
