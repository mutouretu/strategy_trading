from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SIMULATOR_ROOT = PROJECT_ROOT.parent / "market_simulator"
if not SIMULATOR_ROOT.exists():
    raise unittest.SkipTest("sibling market_simulator project is not available")
for package_path in (
    PROJECT_ROOT,
    SIMULATOR_ROOT / "packages" / "market_protocol" / "src",
    SIMULATOR_ROOT / "packages" / "market_simulator" / "src",
    SIMULATOR_ROOT / "packages" / "simulation_runtime" / "src",
):
    sys.path.insert(0, str(package_path))

from scripts.run_layered_following_grid_simulation import (  # noqa: E402
    build_run,
)


class LayeredFollowingGridSimulationTests(unittest.TestCase):
    def test_three_year_layered_run_is_reproducible(self) -> None:
        document = build_run()
        summary = document["summary"]

        self.assertEqual(
            document["manifest"]["simulation_adapter"],
            "layered_following_grid_simulation_adapter",
        )
        self.assertEqual(document["manifest"]["deployment_step"], "5000")
        self.assertEqual(
            document["manifest"]["maker_fee_rate"],
            "0.0002",
        )
        self.assertEqual(len(document["market"]), 1097)
        self.assertEqual(
            min(Decimal(bar["low"]) for bar in document["market"]),
            Decimal("40000"),
        )
        self.assertEqual(
            max(Decimal(bar["high"]) for bar in document["market"]),
            Decimal("200000"),
        )
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
        self.assertEqual(
            sum(
                layer["completed_cycles"]
                for layer in summary["layers"]
            ),
            summary["completed_cycles"],
        )
        self.assertEqual(summary["fill_count"], 736)
        self.assertEqual(summary["retiring_grid_count"], 0)
        self.assertEqual(
            summary["final_positions"],
            {"BTCUSD_PERP": "41"},
        )
        self.assertEqual(
            summary["final_account_metrics"]["total_equity_btc"],
            "1.218365064328432631408023886",
        )
        self.assertEqual(
            summary["final_account_metrics"]["total_equity_usdt"],
            "194938.4102925492210252838218",
        )
        self.assertEqual(
            summary["total_fees"],
            "0.0004255339508114483709521293336",
        )
        self.assertFalse(summary["futures_equity_nonpositive"])

        fill_tags = [fill["tags"] for fill in document["fills"]]
        self.assertEqual(
            {tags["strategy"] for tags in fill_tags},
            {"layered_following_grid"},
        )
        self.assertEqual(
            {tags["layer_index"] for tags in fill_tags},
            {"0", "1", "2", "3", "4", "5"},
        )
        self.assertGreater(
            max(int(tags["layer_generation"]) for tags in fill_tags),
            0,
        )


if __name__ == "__main__":
    unittest.main()
