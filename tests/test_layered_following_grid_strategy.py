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
    GridFill,
    GridMode,
    GridOrderRole,
    GridRuleConfig,
)
from grid_strategies import (  # noqa: E402
    LayeredFollowingGridStrategy,
    LayeredFollowingGridStrategyConfig,
)


def layered_rule() -> GridRuleConfig:
    return GridRuleConfig(
        grid_id="layered-rule-template",
        instrument="BTCUSDT",
        mode=GridMode.LONG,
        anchor_price=Decimal("65000"),
        grid_ratio=Decimal("0.02"),
        grid_count=3,
        order_notional=Decimal("100"),
        tick_size=Decimal("0.1"),
        quantity_step=Decimal("0.0001"),
        move_grid=True,
    )


def build_strategy() -> LayeredFollowingGridStrategy:
    return LayeredFollowingGridStrategy(
        LayeredFollowingGridStrategyConfig(
            strategy_id="layered-following-grid-test",
            rule_template=layered_rule(),
            deployment_step=Decimal("5000"),
        )
    )


def fill_for(intent, sequence: int = 1) -> GridFill:
    return GridFill(
        order_key=intent.order_key,
        instrument=intent.instrument,
        side=intent.side,
        price=intent.price,
        quantity=intent.quantity,
        sequence=sequence,
        timestamp=sequence,
    )


class LayeredFollowingGridStrategyTests(unittest.TestCase):
    def test_configuration_rejects_overlapping_initial_layers(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "adjacent grids overlap at deployment",
        ):
            LayeredFollowingGridStrategyConfig(
                strategy_id="overlapping-layers",
                rule_template=replace(
                    layered_rule(),
                    grid_ratio=Decimal("0.04"),
                    grid_count=5,
                ),
                deployment_step=Decimal("5000"),
            )

    def test_deploys_every_crossed_5000_dollar_layer(self) -> None:
        strategy = build_strategy()

        strategy.initialize(Decimal("65000"))
        self.assertEqual(
            [layer.anchor_price for layer in strategy.layers],
            [Decimal("65000")],
        )

        strategy.on_market(Decimal("49999"))

        self.assertEqual(
            [layer.anchor_price for layer in strategy.layers],
            [
                Decimal("65000"),
                Decimal("60000"),
                Decimal("55000"),
                Decimal("50000"),
            ],
        )
        self.assertEqual(strategy.layer_count, 4)

    def test_lower_layer_resets_and_waits_below_its_anchor(self) -> None:
        strategy = build_strategy()
        strategy.initialize(Decimal("65000"))
        strategy.on_market(Decimal("60000"))
        old_lower_order_keys = {
            intent.order_key
            for intent in strategy.desired_orders
            if strategy.order_context(intent.order_key)["layer_index"] == "1"
        }

        strategy.on_market(Decimal("61250"))

        lower = strategy.layers[1]
        self.assertEqual(lower.generation, 1)
        self.assertEqual(lower.reset_count, 1)
        self.assertTrue(lower.waiting_for_reentry)
        self.assertEqual(strategy.reset_count, 1)
        self.assertTrue(
            old_lower_order_keys.isdisjoint(
                intent.order_key for intent in strategy.desired_orders
            )
        )

        strategy.on_market(Decimal("62000"))
        self.assertEqual(strategy.layers[1].generation, 1)
        self.assertTrue(strategy.layers[1].waiting_for_reentry)

        strategy.on_market(Decimal("59999"))
        self.assertFalse(strategy.layers[1].waiting_for_reentry)

    def test_reset_keeps_exit_for_an_existing_position(self) -> None:
        strategy = build_strategy()
        strategy.initialize(Decimal("65000"))
        strategy.on_market(Decimal("60000"))
        lower_entry = next(
            intent
            for intent in strategy.desired_orders
            if (
                intent.role == GridOrderRole.ENTRY
                and strategy.order_context(intent.order_key)[
                    "layer_index"
                ]
                == "1"
            )
        )
        strategy.on_fills((fill_for(lower_entry),))
        old_exit = next(
            intent
            for intent in strategy.desired_orders
            if (
                intent.role == GridOrderRole.EXIT
                and strategy.order_context(intent.order_key)[
                    "layer_index"
                ]
                == "1"
            )
        )

        strategy.on_market(Decimal("61250"))

        self.assertEqual(strategy.retiring_grid_count, 1)
        self.assertIn(
            old_exit.order_key,
            {intent.order_key for intent in strategy.desired_orders},
        )
        self.assertEqual(
            strategy.order_context(old_exit.order_key)["grid_state"],
            "retiring",
        )
        self.assertFalse(
            any(
                intent.role == GridOrderRole.ENTRY
                and ":generation:0:" in intent.order_key
                and strategy.order_context(intent.order_key)[
                    "layer_index"
                ]
                == "1"
                for intent in strategy.desired_orders
            )
        )

        strategy.on_fills((fill_for(old_exit, sequence=2),))

        self.assertEqual(strategy.retiring_grid_count, 0)
        self.assertEqual(strategy.completed_cycles, 1)


if __name__ == "__main__":
    unittest.main()
