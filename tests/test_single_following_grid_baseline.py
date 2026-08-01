from __future__ import annotations

import copy
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import strategy_simulation  # noqa: F401 - activates local checkout imports

from experiment_system import (
    CodeRevision,
    ExperimentMetricStore,
    ExperimentValidationError,
    ParquetMarketStore,
    SQLiteExperimentRepository,
    execute_single_run,
    load_experiment_spec,
    parse_experiment_spec,
    plan_experiment,
)
from metric_system import MetricEvaluationService
from strategy_simulation.experiment_provider import build_provider_registry
from strategy_simulation.metrics import build_metric_registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_PATH = (
    PROJECT_ROOT / "experiments" / "single_following_grid_baseline.json"
)
CODE_REVISIONS = {
    "market_simulator": CodeRevision(commit="a" * 40),
    "grid_trading": CodeRevision(commit="b" * 40),
    "strategies_system": CodeRevision(commit="c" * 40),
}


def _plan():
    registry = build_provider_registry()
    return (
        registry,
        plan_experiment(
            load_experiment_spec(EXPERIMENT_PATH),
            registry,
            code_revisions=CODE_REVISIONS,
        ),
    )


class SingleFollowingGridBaselineTests(unittest.TestCase):
    def test_three_year_result_survives_strategy_migration(self) -> None:
        registry, plan = _plan()
        prepared = registry.get("strategies-simulation/v1").prepare(
            plan.runs[0]
        )
        result = prepared.execute()
        summary = prepared.summarize(result)

        self.assertEqual(len(result.frames), 1097)
        self.assertEqual(len(result.fills), 155)
        self.assertEqual(result.final_positions, {"BTCUSD_PERP": 90})
        self.assertEqual(summary["strategy_type"], "single-following-grid/v1")
        self.assertEqual(summary["completed_cycles"], 75)
        self.assertEqual(summary["cells_added"], 29)
        self.assertEqual(summary["cells_reclaimed"], 29)
        self.assertEqual(summary["final_cell_count"], 5)
        self.assertEqual(summary["fill_count"], 155)

    def test_persistence_and_grid_metrics_use_the_new_provider(self) -> None:
        registry, plan = _plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "baseline.sqlite3"
            execute_single_run(
                plan,
                registry=registry,
                repository=SQLiteExperimentRepository(database),
                market_store=ParquetMarketStore(root / "market"),
            )
            service = MetricEvaluationService(
                database,
                registry=build_metric_registry(),
            )
            core = service.evaluate_experiment(
                metric_set_id="core",
                version="v1",
            )
            grid = service.evaluate_experiment(
                metric_set_id="grid",
                version="v1",
            )

            self.assertEqual(core.evaluated_count, 1)
            self.assertEqual(grid.evaluated_count, 1)
            store = ExperimentMetricStore(database)
            evaluation = store.run_evaluation(
                plan.runs[0].run_id,
                "grid",
                "v1",
            )
            self.assertIsNotNone(evaluation)
            values = {
                value["metric_key"]: value
                for value in evaluation["values"]
                if not value["dimensions"]
            }
            self.assertEqual(values["grid.completed_cycles"]["value"], 75)
            self.assertEqual(
                values["grid.completed_cycle_count_from_trace"]["value"],
                75,
            )

    def test_rule_and_account_mismatch_is_rejected_before_run(self) -> None:
        document = json.loads(EXPERIMENT_PATH.read_text(encoding="utf-8"))
        mismatch = copy.deepcopy(document)
        mismatch["scenario_groups"][0]["accounts"][0]["parameters"][
            "contract_size"
        ] = "10"

        with self.assertRaisesRegex(
            ExperimentValidationError,
            "contract_size must match",
        ):
            plan_experiment(
                parse_experiment_spec(mismatch),
                build_provider_registry(),
                code_revisions=CODE_REVISIONS,
            )

    def test_explicit_margin_model_still_triggers_liquidation(self) -> None:
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
        registry = build_provider_registry()
        plan = plan_experiment(
            parse_experiment_spec(document),
            registry,
            code_revisions=CODE_REVISIONS,
        )
        result = registry.get("strategies-simulation/v1").prepare(
            plan.runs[0]
        ).execute()

        self.assertTrue(result.margin_snapshots)
        self.assertTrue(result.liquidated)
        self.assertLess(len(result.frames), 1097)
        self.assertLessEqual(
            min(snapshot.margin_buffer for snapshot in result.margin_snapshots),
            Decimal("0"),
        )


if __name__ == "__main__":
    unittest.main()
