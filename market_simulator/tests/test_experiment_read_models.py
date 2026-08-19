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
    performance_document,
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
            self.assertEqual(
                first["provider_summary"]["provider_summary"]
                ["test-simulation/v1"]["fill_count"],
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

            performance = performance_document(reader, run_id)
            self.assertEqual(performance["source_point_count"], 6)
            self.assertEqual(performance["sampled_point_count"], 7)
            self.assertEqual(performance["assets"], ["USDT"])
            self.assertEqual(
                performance["statistics"]["USDT"]["initial_equity"],
                "1000",
            )

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

    def test_performance_document_exposes_daily_margin_risk_rate(self) -> None:
        class Reader:
            @staticmethod
            def run_detail(run_id: str) -> dict[str, object]:
                return {
                    "status": "SUCCEEDED",
                    "trace_state": "STORED",
                    "summary": {
                        "result": {
                            "initial_equity": "1000",
                            "equity_asset": "USDT",
                            "initial_account_metrics": {},
                        }
                    },
                }

            @staticmethod
            def load_trace(run_id: str) -> dict[str, object]:
                return {
                    "equity": [
                        {
                            "sequence": 0,
                            "timestamp": 86_400_000,
                            "date": "1970-01-02",
                            "equity": "990",
                            "equity_asset": "USDT",
                            "account_metrics": {},
                        },
                        {
                            "sequence": 1,
                            "timestamp": 172_800_000,
                            "date": "1970-01-03",
                            "equity": "900",
                            "equity_asset": "USDT",
                            "account_metrics": {},
                        },
                    ],
                    "margin": [
                        {
                            "sequence": 0,
                            "timestamp": 86_400_000,
                            "date": "1970-01-02",
                            "settlement_asset": "USDT",
                            "position_quantity": "1",
                            "mark_price": "100",
                            "estimated_liquidation_price": "70",
                            "margin_balance": "400",
                            "maintenance_margin": "20",
                            "maintenance_margin_utilization": "0.05",
                            "liquidation_triggered": False,
                        },
                        {
                            "sequence": 1,
                            "timestamp": 86_400_001,
                            "date": "1970-01-02",
                            "settlement_asset": "USDT",
                            "position_quantity": "1",
                            "mark_price": "80",
                            "estimated_liquidation_price": "70",
                            "margin_balance": "300",
                            "maintenance_margin": "30",
                            "maintenance_margin_utilization": "0.1",
                            "liquidation_triggered": False,
                        },
                        {
                            "sequence": 2,
                            "timestamp": 172_800_000,
                            "date": "1970-01-03",
                            "settlement_asset": "USDT",
                            "position_quantity": "1",
                            "mark_price": "80",
                            "estimated_liquidation_price": "72",
                            "margin_balance": "200",
                            "maintenance_margin": "100",
                            "maintenance_margin_utilization": None,
                            "liquidation_triggered": False,
                        },
                    ],
                }

        performance = performance_document(Reader(), "margin-probe")
        self.assertEqual(performance["margin_source_point_count"], 3)
        self.assertEqual(
            performance["margin_statistics"],
            {
                "settlement_asset": "USDT",
                "maximum_risk_rate": "0.5",
                "minimum_margin_balance": "200",
                "minimum_liquidation_distance_rate": "0.1",
                "risk_threshold_rate": "1",
                "liquidation_triggered": False,
            },
        )
        self.assertEqual(
            [point["margin"]["risk_rate"] for point in performance["points"]],
            ["0", "0.1", "0.5"],
        )
        self.assertEqual(
            [point["margin"]["mark_price"] for point in performance["points"]],
            [None, "80", "80"],
        )
        self.assertEqual(
            [
                point["margin"]["estimated_liquidation_price"]
                for point in performance["points"]
            ],
            [None, "70", "72"],
        )
        self.assertEqual(
            [
                point["margin"]["liquidation_distance_rate"]
                for point in performance["points"]
            ],
            [None, "0.125", "0.1"],
        )
        self.assertEqual(
            [
                point["margin"]["minimum_liquidation_distance_rate"]
                for point in performance["points"]
            ],
            [None, "0.125", "0.1"],
        )


if __name__ == "__main__":
    unittest.main()
