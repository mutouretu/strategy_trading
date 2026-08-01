from __future__ import annotations

import copy
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from grid_experiments.provider import build_registry
from grid_metrics import build_metric_registry

from experiment_system import (
    ExperimentMetricStore,
    ParquetMarketStore,
    SQLiteExperimentRepository,
    execute_single_run,
    parse_experiment_spec,
    plan_experiment,
)
from metric_system import MetricEvaluationService

from test_grid_experiments_provider import (
    CODE_REVISIONS,
    EXPERIMENT_PATH,
    _plan,
)


class GridMetricTests(unittest.TestCase):
    def test_core_dual_valuation_and_grid_metrics_persist(self) -> None:
        experiment_registry, plan = _plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "baseline.sqlite3"
            execute_single_run(
                plan,
                registry=experiment_registry,
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
            run_id = plan.runs[0].run_id
            core_result = store.run_evaluation(run_id, "core", "v1")
            grid_result = store.run_evaluation(run_id, "grid", "v1")
            self.assertIsNotNone(core_result)
            self.assertIsNotNone(grid_result)
            equity_dimensions = {
                (
                    value["dimensions"].get("scope"),
                    value["dimensions"].get("valuation_asset"),
                )
                for value in core_result["values"]
                if value["metric_key"] == "return.total_rate"
            }
            self.assertIn(
                ("account.total_equity", "BTC"),
                equity_dimensions,
            )
            self.assertIn(
                ("account.total_equity", "USDT"),
                equity_dimensions,
            )
            self.assertIn(
                ("account.futures_equity", "BTC"),
                equity_dimensions,
            )
            grid_values = {
                value["metric_key"]: value
                for value in grid_result["values"]
                if not value["dimensions"]
            }
            self.assertEqual(
                grid_values["grid.completed_cycles"]["value"],
                75,
            )
            self.assertEqual(
                grid_values[
                    "grid.completed_cycle_count_from_trace"
                ]["value"],
                75,
            )
            self.assertEqual(
                grid_values["grid.incomplete_entry_count"]["value"],
                5,
            )
            self.assertEqual(len(store.aggregate_evaluations()), 2)

    def test_core_metrics_record_real_coinm_liquidation(self) -> None:
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
            parse_experiment_spec(copy.deepcopy(document)),
            registry,
            code_revisions=CODE_REVISIONS,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "liquidation.sqlite3"
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
            service.evaluate_experiment(metric_set_id="core", version="v1")

            evaluation = ExperimentMetricStore(database).run_evaluation(
                plan.runs[0].run_id,
                "core",
                "v1",
            )
            self.assertIsNotNone(evaluation)
            scalar_values = {
                value["metric_key"]: value["value"]
                for value in evaluation["values"]
                if not value["dimensions"]
            }
            self.assertTrue(scalar_values["run.liquidated"])
            self.assertFalse(scalar_values["run.completed"])
            self.assertEqual(
                scalar_values["run.termination_reason"],
                "LIQUIDATION",
            )
            buffers = [
                value["value"]
                for value in evaluation["values"]
                if value["metric_key"] == "margin.minimum_buffer"
                and value["status"] == "AVAILABLE"
            ]
            self.assertTrue(buffers)
            self.assertLessEqual(
                min(Decimal(value) for value in buffers),
                Decimal("0"),
            )


if __name__ == "__main__":
    unittest.main()
