from __future__ import annotations

import csv
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from experiment_system import (
    CodeRevision,
    ExperimentCatalog,
    ExperimentReader,
    ParquetMarketStore,
    RunQuery,
    SQLiteExperimentRepository,
    comparison_csv_text,
    comparison_table,
    execute_experiment,
    export_comparison_csv,
    export_viewer_json,
    parse_experiment_spec,
    plan_experiment,
    viewer_document,
)

from experiment_test_support import (
    executable_registry,
    experiment_document,
)


class ExperimentReadModelTests(unittest.TestCase):
    @staticmethod
    def _execute(root: Path):
        registry, _ = executable_registry()
        plan = plan_experiment(
            parse_experiment_spec(experiment_document()),
            registry,
            code_revisions={
                "market_simulator": CodeRevision(commit="a" * 40),
            },
        )
        database = root / "results" / "grid-research.sqlite3"
        outcome = execute_experiment(
            plan,
            registry=registry,
            repository=SQLiteExperimentRepository(database),
            market_store=ParquetMarketStore(root / "market_data"),
        )
        return plan, outcome, database

    def test_catalog_filters_sorts_and_expands_raw_scalars(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan, _, database = self._execute(root)
            unrelated = root / "results" / "unrelated.sqlite3"
            with sqlite3.connect(unrelated) as connection:
                connection.execute("CREATE TABLE notes(value TEXT)")

            catalog = ExperimentCatalog(root / "results")
            experiments = catalog.experiments()
            self.assertEqual(len(experiments), 1)
            self.assertEqual(
                experiments[0]["experiment_id"],
                "grid-research",
            )
            reader = catalog.reader("grid-research")
            detail = reader.experiment_detail()
            self.assertEqual(detail["planned_run_count"], 10)
            self.assertEqual(
                detail["status_counts"],
                {"SUCCEEDED": 10},
            )

            seed_43 = reader.query_runs(
                RunQuery(
                    seed=43,
                    sort_by="scenario_id",
                    descending=True,
                )
            )
            self.assertEqual(seed_43.total, 5)
            self.assertTrue(
                all(row["seed"] == 43 for row in seed_43.rows)
            )
            market_c = reader.query_runs(
                RunQuery(search="market-c")
            )
            self.assertEqual(market_c.total, 2)
            first = reader.run_detail(plan.runs[0].run_id)
            self.assertEqual(
                first["components"]["market"],
                "market-a",
            )
            self.assertEqual(
                first["summary_scalars"][
                    "result.final_equity"
                ],
                "1006",
            )
            self.assertEqual(
                first["summary_scalars"][
                    "provider_summary.test-simulation/v1.fill_count"
                ],
                3,
            )
            self.assertEqual(reader.database_path, database.resolve())

    def test_comparison_and_viewer_exports_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan, outcome, database = self._execute(root)
            reader = ExperimentReader(database)
            before_json = set(root.rglob("*.json"))

            table = comparison_table(reader)
            self.assertEqual(len(table.rows), 10)
            self.assertIn(
                "parameter:/strategy/parameters/order_quantity",
                table.columns,
            )
            self.assertIn(
                (
                    "summary:provider_summary."
                    "test-simulation/v1.fill_count"
                ),
                table.columns,
            )
            csv_text = comparison_csv_text(table)
            csv_rows = list(csv.DictReader(io.StringIO(csv_text)))
            self.assertEqual(len(csv_rows), 10)
            self.assertEqual(
                csv_rows[0]["summary:result.final_equity"],
                "1006",
            )

            run_id = plan.runs[0].run_id
            dynamic_viewer = viewer_document(reader, run_id)
            self.assertEqual(dynamic_viewer["schema_version"], 2)
            self.assertEqual(len(dynamic_viewer["market"]), 6)
            self.assertEqual(len(dynamic_viewer["fills"]), 3)
            self.assertEqual(
                dynamic_viewer["summary"]["final_equity"],
                "1006",
            )
            self.assertEqual(set(root.rglob("*.json")), before_json)

            csv_path = export_comparison_csv(
                reader,
                root / "exports" / "comparison.csv",
            )
            viewer_path = export_viewer_json(
                reader,
                run_id,
                root / "exports" / "viewer-run.json",
            )
            self.assertTrue(csv_path.is_file())
            self.assertTrue(viewer_path.is_file())
            self.assertEqual(
                json.loads(
                    viewer_path.read_text(encoding="utf-8")
                ),
                dynamic_viewer,
            )
            self.assertEqual(
                outcome.runs[0].market_reference.market_path_id,
                dynamic_viewer["manifest"]["market_path_id"],
            )


if __name__ == "__main__":
    unittest.main()
