from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from experiment_system import (
    CodeRevision,
    ExperimentMetricStore,
    ExperimentReader,
    ParquetMarketStore,
    SQLiteExperimentRepository,
    execute_experiment,
    parse_experiment_spec,
    plan_experiment,
)
from metric_system import (
    CoreMetricCalculator,
    MetricEvaluationService,
    MetricRegistry,
)

from experiment_test_support import executable_registry, single_experiment_document


class MetricServiceTests(unittest.TestCase):
    def _execute(self, root: Path):
        providers, _ = executable_registry()
        plan = plan_experiment(
            parse_experiment_spec(single_experiment_document()),
            providers,
            code_revisions={
                "market_simulator": CodeRevision(commit="a" * 40),
            },
        )
        database = root / "experiment.sqlite3"
        execute_experiment(
            plan,
            registry=providers,
            repository=SQLiteExperimentRepository(database),
            market_store=ParquetMarketStore(root / "market"),
        )
        return plan, database

    @staticmethod
    def _service(database: Path) -> MetricEvaluationService:
        registry = MetricRegistry()
        registry.register_calculator(CoreMetricCalculator())
        return MetricEvaluationService(database, registry=registry)

    def test_evaluate_persist_aggregate_and_skip_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan, database = self._execute(Path(directory))
            service = self._service(database)

            first = service.evaluate_experiment(
                metric_set_id="core",
                version="v1",
            )
            second = service.evaluate_experiment(
                metric_set_id="core",
                version="v1",
            )

            self.assertEqual(first.evaluated_count, 1)
            self.assertEqual(first.aggregate_count, 1)
            self.assertEqual(second.evaluated_count, 0)
            self.assertEqual(second.skipped_count, 1)
            store = ExperimentMetricStore(database)
            evaluation = store.run_evaluation(
                plan.runs[0].run_id,
                "core",
                "v1",
            )
            self.assertIsNotNone(evaluation)
            self.assertEqual(evaluation["status"], "SUCCEEDED")
            self.assertTrue(evaluation["recomputable"])
            self.assertTrue(store.aggregate_evaluations())
            reader = ExperimentReader(database)
            row = reader.query_runs().rows[0]
            self.assertTrue(row["metric_scalars"])
            detail = reader.run_detail(plan.runs[0].run_id)
            self.assertEqual(len(detail["metrics"]), 1)
            self.assertEqual(
                len(reader.aggregate_metric_evaluations()),
                1,
            )

    def test_purge_keeps_metrics_and_marks_them_not_recomputable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan, database = self._execute(Path(directory))
            service = self._service(database)
            service.evaluate_experiment(metric_set_id="core", version="v1")

            SQLiteExperimentRepository(database).purge_standard_traces()
            evaluation = ExperimentMetricStore(database).run_evaluation(
                plan.runs[0].run_id,
                "core",
                "v1",
            )

            self.assertIsNotNone(evaluation)
            self.assertFalse(evaluation["recomputable"])
            self.assertTrue(evaluation["values"])


if __name__ == "__main__":
    unittest.main()
