from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import strategy_simulation  # noqa: F401 - activates local checkout imports

from strategy_simulation.adapters import (
    LayeredFollowingGridSimulationAdapter,
)
from strategy_simulation.experiment_provider import build_provider_registry

from experiment_system import (
    CodeRevision,
    ExperimentReader,
    ParquetMarketStore,
    SQLiteExperimentRepository,
    execute_single_run,
    load_experiment_spec,
    plan_experiment,
    viewer_document,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_PATH = (
    PROJECT_ROOT
    / "experiments"
    / "layered_following_grid_baseline.json"
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


class LayeredFollowingGridSimulationTests(unittest.TestCase):
    def test_layered_component_builds_the_expected_adapter(self) -> None:
        registry, plan = _plan()
        self.assertEqual(plan.run_count, 1)
        self.assertEqual(
            plan.runs[0].configuration.strategy.type,
            "layered-following-grid/v1",
        )
        prepared = registry.get("strategies-simulation/v1").prepare(
            plan.runs[0]
        )
        self.assertIsInstance(
            prepared.components.binding.trade_port,
            LayeredFollowingGridSimulationAdapter,
        )

    def test_three_year_layered_baseline_preserves_result(self) -> None:
        registry, plan = _plan()
        prepared = registry.get("strategies-simulation/v1").prepare(
            plan.runs[0]
        )
        result = prepared.execute()
        summary = prepared.summarize(result)

        self.assertEqual(len(result.frames), 1097)
        self.assertEqual(
            min(frame.low for frame in result.frames),
            Decimal("40000"),
        )
        self.assertEqual(
            max(frame.high for frame in result.frames),
            Decimal("200000"),
        )
        self.assertEqual(summary["strategy_type"], "layered-following-grid/v1")
        self.assertEqual(summary["layer_count"], 6)
        self.assertEqual(
            [layer["anchor_price"] for layer in summary["layers"]],
            ["65000", "60000", "55000", "50000", "45000", "40000"],
        )
        self.assertEqual(
            [layer["reset_count"] for layer in summary["layers"]],
            [0, 4, 5, 6, 7, 1],
        )
        self.assertEqual(summary["reset_count"], 23)
        self.assertEqual(summary["completed_cycles"], 364)
        self.assertEqual(summary["fill_count"], 736)
        self.assertEqual(summary["retiring_grid_count"], 0)
        self.assertEqual(
            result.final_positions,
            {"BTCUSD_PERP": 41},
        )
        self.assertEqual(
            result.final_account_metrics["total_equity_btc"],
            Decimal("1.218365064328432631408023886"),
        )
        self.assertEqual(
            result.final_account_metrics["total_equity_usdt"],
            Decimal("194938.4102925492210252838218"),
        )
        self.assertEqual(
            result.total_fees,
            Decimal("0.0004255339508114483709521293336"),
        )
        self.assertGreater(
            result.final_account_metrics["futures_equity_btc"],
            Decimal("0"),
        )
        self.assertEqual(
            {fill.tags["strategy"] for fill in result.fills},
            {"layered_following_grid"},
        )
        self.assertEqual(
            {fill.tags["layer_index"] for fill in result.fills},
            {"0", "1", "2", "3", "4", "5"},
        )

    def test_layered_run_persists_and_exports_to_existing_viewer(self) -> None:
        registry, plan = _plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "results" / "layered.sqlite3"
            outcome = execute_single_run(
                plan,
                registry=registry,
                repository=SQLiteExperimentRepository(database),
                market_store=ParquetMarketStore(root / "market_data"),
            )
            summary = outcome.summary["provider_summary"][
                "strategies-simulation/v1"
            ]
            document = viewer_document(
                ExperimentReader(database),
                outcome.record.run_id,
            )

            self.assertEqual(summary["layer_count"], 6)
            self.assertEqual(summary["reset_count"], 23)
            self.assertEqual(document["schema_version"], 2)
            self.assertEqual(len(document["market"]), 1097)
            self.assertEqual(len(document["fills"]), 736)
            self.assertEqual(
                document["summary"]["final_positions"],
                {"BTCUSD_PERP": "41"},
            )


if __name__ == "__main__":
    unittest.main()
