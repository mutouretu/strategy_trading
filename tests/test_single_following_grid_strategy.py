from __future__ import annotations

import sys
import unittest
from dataclasses import replace
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

from grid_rule import (  # noqa: E402
    GridRuleConfig,
    GridRuleEngine,
    GridMode,
)
from grid_strategies import (  # noqa: E402
    SingleFollowingGridStrategy,
    SingleFollowingGridStrategyConfig,
)
from grid_strategies.adapters import (  # noqa: E402
    SingleFollowingGridSimulationAdapter,
)
from market_simulator import FixedBarMarketSource  # noqa: E402
from simulation_runtime import SimulationRunner  # noqa: E402


def following_rule(grid_count: int = 1) -> GridRuleConfig:
    return GridRuleConfig(
        grid_id="single-following-grid-rule",
        instrument="BTCUSDT",
        mode=GridMode.LONG,
        anchor_price=Decimal("110"),
        grid_ratio=Decimal("0.10"),
        grid_count=grid_count,
        order_notional=Decimal("100"),
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        move_grid=True,
    )


class SingleFollowingGridStrategyTests(unittest.TestCase):
    def test_strategy_requires_a_following_grid(self) -> None:
        with self.assertRaisesRegex(ValueError, "move_grid=True"):
            SingleFollowingGridStrategyConfig(
                strategy_id="static-is-not-this-strategy",
                rule=replace(following_rule(), move_grid=False),
            )

    def test_strategy_deploys_and_moves_exactly_one_grid(self) -> None:
        strategy = SingleFollowingGridStrategy(
            SingleFollowingGridStrategyConfig(
                strategy_id="one-following-grid",
                rule=following_rule(grid_count=2),
            )
        )

        strategy.initialize(Decimal("105"))
        strategy.on_market(Decimal("121"))

        self.assertIsInstance(strategy.engine, GridRuleEngine)
        self.assertEqual(len(strategy.engine.cells), 2)
        self.assertEqual(
            [
                (cell.buy_price, cell.sell_price)
                for cell in strategy.engine.cells
            ],
            [
                (Decimal("110"), Decimal("121.00")),
                (Decimal("121.00"), Decimal("133.10")),
            ],
        )
        self.assertEqual(strategy.engine.cells_added, 2)
        self.assertEqual(strategy.engine.cells_reclaimed, 2)
        with self.assertRaisesRegex(RuntimeError, "already initialized"):
            strategy.initialize(Decimal("121"))

    def test_runner_calls_adapter_strategy_and_rule_in_order(self) -> None:
        adapter = SingleFollowingGridSimulationAdapter(
            SingleFollowingGridStrategyConfig(
                strategy_id="simulation-call-chain",
                rule=following_rule(),
            )
        )
        source = FixedBarMarketSource(
            "BTCUSDT",
            [
                ("105", "106", "104", "105"),
                ("105", "106", "99", "101"),
                ("101", "111", "100", "110"),
            ],
        )

        result = SimulationRunner(
            source,
            adapter,
            initial_equity=Decimal("1000"),
        ).run()

        self.assertIsInstance(
            adapter.strategy,
            SingleFollowingGridStrategy,
        )
        self.assertIsInstance(adapter.strategy.engine, GridRuleEngine)
        self.assertEqual(
            [(fill.side.value, fill.price) for fill in result.fills],
            [("BUY", Decimal("100")), ("SELL", Decimal("110"))],
        )
        self.assertEqual(adapter.strategy.engine.completed_cycles, 1)
        self.assertEqual(
            {fill.tags["strategy"] for fill in result.fills},
            {"single_following_grid"},
        )
        self.assertEqual(
            {fill.tags["strategy_id"] for fill in result.fills},
            {"simulation-call-chain"},
        )


if __name__ == "__main__":
    unittest.main()
