from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from grid_experiments import PreparedGridRun, build_registry
from grid_experiments.cli import main as grid_experiment_main

from experiment_system import (
    CodeRevision,
    ExperimentValidationError,
    ParquetMarketStore,
    SQLiteExperimentRepository,
    execute_single_run,
    load_experiment_spec,
    parse_experiment_spec,
    plan_experiment,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_PATH = (
    PROJECT_ROOT
    / "experiments"
    / "single_following_grid_baseline.json"
)
KEY_PARAMETER_MATRIX_PATH = (
    PROJECT_ROOT
    / "experiments"
    / "single_following_grid_key_parameter_matrix.json"
)
CODE_REVISIONS = {
    "market_simulator": CodeRevision(commit="a" * 40),
    "grid_trading": CodeRevision(commit="b" * 40),
}


def _plan():
    registry = build_registry()
    return (
        registry,
        plan_experiment(
            load_experiment_spec(EXPERIMENT_PATH),
            registry,
            code_revisions=CODE_REVISIONS,
        ),
    )


class GridExperimentProviderTests(unittest.TestCase):
    def test_provider_owns_strategy_display_names(self) -> None:
        descriptors = build_registry().component_descriptors

        self.assertEqual(
            {
                descriptor["type"]: descriptor["display_name"]
                for descriptor in descriptors
            },
            {
                "single-following-grid/v1": "单组跟随网格",
                "layered-following-grid/v1": "分层跟随网格",
            },
        )

    def test_key_parameter_matrix_expands_grid_dimensions(self) -> None:
        plan = plan_experiment(
            load_experiment_spec(KEY_PARAMETER_MATRIX_PATH),
            build_registry(),
            code_revisions=CODE_REVISIONS,
        )

        combinations = {
            (
                run.configuration.parameter_values[
                    "/strategy/parameters/grid_count"
                ],
                run.configuration.parameter_values[
                    "/strategy/parameters/grid_ratio"
                ],
                run.configuration.parameter_values[
                    "/strategy/parameters/order_coin_quantity"
                ],
            )
            for run in plan.runs
        }

        self.assertEqual(plan.scenario_count, 12)
        self.assertEqual(plan.run_count, 24)
        self.assertEqual(
            combinations,
            {
                (grid_count, grid_ratio, order_quantity)
                for grid_count in (3, 5)
                for grid_ratio in ("0.02", "0.04", "0.06")
                for order_quantity in ("0.005", "0.01")
            },
        )
        self.assertEqual({run.seed for run in plan.runs}, {42, 43})
        self.assertEqual(
            {
                (
                    run.configuration.account.parameters["margin_model"],
                    run.configuration.account.parameters["leverage"],
                    run.configuration.account.parameters[
                        "maintenance_margin_rate"
                    ],
                    run.configuration.account.parameters[
                        "mark_price_sampling"
                    ],
                    run.configuration.account.parameters["spot_btc"],
                    run.configuration.account.parameters[
                        "futures_wallet_btc"
                    ],
                )
                for run in plan.runs
            },
            {
                (
                    "flat-maintenance/v1",
                    "5",
                    "0.005",
                    "ADVERSE_EXTREME",
                    "0",
                    "1.1",
                )
            },
        )

    def test_baseline_resolves_to_one_canonical_run(self) -> None:
        registry, plan = _plan()
        run = plan.runs[0]

        self.assertEqual(registry.provider_ids, ("grid-simulation/v1",))
        self.assertEqual(plan.scenario_count, 1)
        self.assertEqual(plan.run_count, 1)
        self.assertEqual(run.seed, 42)
        self.assertEqual(
            run.configuration.market.type,
            "anchored-gbm/v1",
        )
        self.assertEqual(
            run.configuration.strategy.type,
            "single-following-grid/v1",
        )
        self.assertEqual(
            run.configuration.execution.type,
            "daily-bar-execution/v1",
        )
        self.assertEqual(
            run.configuration.account.type,
            "coinm-inverse/v1",
        )
        self.assertEqual(
            run.configuration.account.parameters["margin_model"],
            "none",
        )

    def test_single_baseline_preserves_characterized_result(self) -> None:
        registry, plan = _plan()
        provider = registry.get("grid-simulation/v1")
        prepared = provider.prepare(plan.runs[0])
        self.assertIsInstance(prepared, PreparedGridRun)

        result = prepared.execute()
        provider_summary = prepared.summarize(result)
        self.assertEqual(len(result.frames), 1097)
        self.assertEqual(len(result.fills), 155)
        self.assertEqual(
            result.final_positions,
            {"BTCUSD_PERP": 90},
        )
        self.assertEqual(
            provider_summary["strategy_type"],
            "single_following_grid",
        )
        self.assertEqual(provider_summary["completed_cycles"], 75)
        self.assertEqual(provider_summary["cells_added"], 29)
        self.assertEqual(provider_summary["cells_reclaimed"], 29)
        self.assertEqual(provider_summary["final_cell_count"], 5)
        self.assertEqual(provider_summary["fill_count"], 155)
        self.assertNotIn("minimum_futures_equity_btc", provider_summary)
        self.assertNotIn("futures_equity_nonpositive", provider_summary)

    def test_provider_run_persists_grid_summary_and_external_market(
        self,
    ) -> None:
        registry, plan = _plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = SQLiteExperimentRepository(
                root / "results" / "baseline.sqlite3"
            )

            outcome = execute_single_run(
                plan,
                registry=registry,
                repository=repository,
                market_store=ParquetMarketStore(
                    root / "market_data"
                ),
            )

            self.assertEqual(outcome.record.status.value, "SUCCEEDED")
            self.assertEqual(
                list((root / "results").iterdir()),
                [root / "results" / "baseline.sqlite3"],
            )
            self.assertEqual(
                len(list((root / "market_data").glob("*.parquet"))),
                1,
            )
            summary = repository.get_summary(outcome.record.run_id)
            grid_summary = summary["provider_summary"][
                "grid-simulation/v1"
            ]
            self.assertEqual(grid_summary["completed_cycles"], 75)
            self.assertEqual(grid_summary["fill_count"], 155)
            self.assertEqual(
                summary["result"]["final_positions"],
                {"BTCUSD_PERP": "90"},
            )
            trace = repository.load_trace(outcome.record.run_id)
            self.assertNotIn("market", trace)
            self.assertEqual(len(trace["fills"]), 155)

    def test_preflight_rejects_cross_component_mismatch(self) -> None:
        document = json.loads(EXPERIMENT_PATH.read_text(encoding="utf-8"))
        mismatch = copy.deepcopy(document)
        mismatch["scenario_groups"][0]["accounts"][0]["parameters"][
            "contract_size"
        ] = "10"
        registry = build_registry()

        with self.assertRaisesRegex(
            ExperimentValidationError,
            "contract_size must match",
        ):
            plan_experiment(
                parse_experiment_spec(mismatch),
                registry,
                code_revisions=CODE_REVISIONS,
            )

    def test_explicit_margin_configuration_activates_liquidation(
        self,
    ) -> None:
        document = json.loads(EXPERIMENT_PATH.read_text(encoding="utf-8"))
        account = document["scenario_groups"][0]["accounts"][0][
            "parameters"
        ]
        account.update(
            {
                "margin_model": "flat-maintenance/v1",
                "leverage": "3",
                "maintenance_margin_rate": "0.005",
                "mark_price_sampling": "ADVERSE_EXTREME",
            }
        )
        registry = build_registry()
        plan = plan_experiment(
            parse_experiment_spec(document),
            registry,
            code_revisions=CODE_REVISIONS,
        )

        result = registry.get("grid-simulation/v1").prepare(
            plan.runs[0]
        ).execute()

        self.assertTrue(result.margin_snapshots)
        self.assertTrue(result.liquidated)
        self.assertLess(len(result.frames), 1097)

    def test_grid_cli_is_only_a_registered_generic_host(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            code = grid_experiment_main(
                ["validate", str(EXPERIMENT_PATH)],
                code_revisions=CODE_REVISIONS,
            )

        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(output.getvalue())["provider_ids"],
            ["grid-simulation/v1"],
        )


if __name__ == "__main__":
    unittest.main()
