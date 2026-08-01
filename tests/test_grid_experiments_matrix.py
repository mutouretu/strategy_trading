from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from grid_experiments import build_registry

from experiment_system import (
    CodeRevision,
    ParquetMarketStore,
    SQLiteExperimentRepository,
    execute_experiment,
    execute_single_run,
    load_experiment_spec,
    parse_experiment_spec,
    plan_experiment,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = (
    PROJECT_ROOT
    / "experiments"
    / "single_following_grid_matrix.json"
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
            load_experiment_spec(MATRIX_PATH),
            registry,
            code_revisions=CODE_REVISIONS,
        ),
    )


class GridExperimentMatrixTests(unittest.TestCase):
    def test_matrix_expands_in_stable_cartesian_and_seed_order(
        self,
    ) -> None:
        _, plan = _plan()

        self.assertEqual(plan.scenario_count, 4)
        self.assertEqual(plan.run_count, 8)
        self.assertEqual(
            [
                (
                    run.configuration.strategy.key,
                    run.configuration.strategy.parameters["grid_count"],
                    run.seed,
                )
                for run in plan.runs
            ],
            [
                ("single-following-small", 3, 42),
                ("single-following-small", 3, 43),
                ("single-following-small", 5, 42),
                ("single-following-small", 5, 43),
                ("single-following-base", 3, 42),
                ("single-following-base", 3, 43),
                ("single-following-base", 5, 42),
                ("single-following-base", 5, 43),
            ],
        )

    def test_matrix_runs_into_one_database_and_shared_market_paths(
        self,
    ) -> None:
        registry, plan = _plan()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "results" / "matrix.sqlite3"
            repository = SQLiteExperimentRepository(database)
            market_root = root / "market_data"
            outcome = execute_experiment(
                plan,
                registry=registry,
                repository=repository,
                market_store=ParquetMarketStore(market_root),
            )

            self.assertEqual(outcome.run_count, 8)
            self.assertEqual(outcome.succeeded_count, 8)
            self.assertEqual(
                [run.record.run_id for run in outcome.runs],
                [run.run_id for run in plan.runs],
            )
            self.assertEqual(
                len(
                    {
                        run.market_reference.market_path_id
                        for run in outcome.runs
                    }
                ),
                2,
            )
            self.assertEqual(
                len(list(market_root.glob("*.parquet"))),
                2,
            )
            self.assertEqual(
                list((root / "results").iterdir()),
                [database],
            )

            for run in outcome.runs:
                summary = repository.get_summary(run.record.run_id)
                self.assertIn("result", summary)
                self.assertIn(
                    "grid-simulation/v1",
                    summary["provider_summary"],
                )

            with sqlite3.connect(database) as connection:
                experiment_row = connection.execute(
                    """
                    SELECT status, planned_run_count
                    FROM experiments
                    """
                ).fetchone()
                run_count = connection.execute(
                    "SELECT COUNT(*) FROM runs WHERE status = 'SUCCEEDED'"
                ).fetchone()[0]
                market_count = connection.execute(
                    "SELECT COUNT(*) FROM market_references"
                ).fetchone()[0]
            self.assertEqual(experiment_row, ("SUCCEEDED", 8))
            self.assertEqual(run_count, 8)
            self.assertEqual(market_count, 2)

    def test_single_and_batch_use_equivalent_run_execution(self) -> None:
        registry, batch_plan = _plan()
        document = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        single_document = copy.deepcopy(document)
        single_document["experiment_id"] = "matrix-first-run-single"
        group = single_document["scenario_groups"][0]
        group["strategies"] = [group["strategies"][0]]
        group["parameter_axes"][0]["values"] = [3]
        single_document["seeds"] = [42]
        single_document["controls"]["max_runs"] = 1
        single_plan = plan_experiment(
            parse_experiment_spec(single_document),
            registry,
            code_revisions=CODE_REVISIONS,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            market_store = ParquetMarketStore(root / "market_data")
            batch = execute_experiment(
                batch_plan,
                registry=registry,
                repository=SQLiteExperimentRepository(
                    root / "batch.sqlite3"
                ),
                market_store=market_store,
            )
            single = execute_single_run(
                single_plan,
                registry=registry,
                repository=SQLiteExperimentRepository(
                    root / "single.sqlite3"
                ),
                market_store=market_store,
            )

            self.assertEqual(
                batch.runs[0].market_reference.market_path_id,
                single.market_reference.market_path_id,
            )
            self.assertEqual(
                batch.runs[0].summary["result"],
                single.summary["result"],
            )
            self.assertEqual(
                batch.runs[0].summary["provider_summary"],
                single.summary["provider_summary"],
            )


if __name__ == "__main__":
    unittest.main()
