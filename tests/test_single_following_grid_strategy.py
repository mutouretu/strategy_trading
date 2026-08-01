from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

import strategy_simulation  # noqa: F401 - activates local checkout imports

from grid_rule import (  # noqa: E402
    GridRuleConfig,
    GridMode,
)
from trading_strategies.grid_following import (  # noqa: E402
    SingleFollowingGridStrategy,
    SingleFollowingGridStrategyConfig,
)
from strategy_simulation.adapters import (  # noqa: E402
    GridRuleEngineFactory,
    GridRuleEnginePort,
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
            ),
            GridRuleEngineFactory(),
        )

        strategy.initialize(Decimal("105"))
        strategy.on_market(Decimal("121"))

        self.assertIsInstance(strategy.rule, GridRuleEnginePort)
        snapshot = strategy.rule.snapshot()
        self.assertEqual(len(snapshot.cells), 2)
        self.assertEqual(
            [
                (cell.buy_price, cell.sell_price)
                for cell in snapshot.cells
            ],
            [
                (Decimal("110"), Decimal("121.00")),
                (Decimal("121.00"), Decimal("133.10")),
            ],
        )
        self.assertEqual(snapshot.cells_added, 2)
        self.assertEqual(snapshot.cells_reclaimed, 2)
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
            trade_port=adapter,
            initial_equity=Decimal("1000"),
        ).run()

        self.assertIsInstance(
            adapter.strategy,
            SingleFollowingGridStrategy,
        )
        self.assertIsInstance(adapter.strategy.rule, GridRuleEnginePort)
        self.assertEqual(
            [(fill.side.value, fill.price) for fill in result.fills],
            [("BUY", Decimal("100")), ("SELL", Decimal("110"))],
        )
        self.assertEqual(
            adapter.strategy.rule.snapshot().completed_cycles,
            1,
        )
        self.assertEqual(
            {fill.tags["strategy"] for fill in result.fills},
            {"single_following_grid"},
        )
        self.assertEqual(
            {fill.tags["strategy_id"] for fill in result.fills},
            {"simulation-call-chain"},
        )
        self.assertEqual(
            [record.intent.reduce_only for record in result.intents],
            [False, True, False],
        )


if __name__ == "__main__":
    unittest.main()
