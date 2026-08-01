from __future__ import annotations

import ast
import unittest
from decimal import Decimal
from pathlib import Path

from trading_strategies.baselines import HoldBtcConfig, HoldBtcStrategy
from trading_strategies.btc_accumulation import (
    LadderState,
    PositionPlan,
    StrategyFill,
    StrategyOrderSide,
    StrategyRole,
    TargetLiquidationLadderConfig,
    TargetLiquidationLadderStrategy,
    build_take_profit_schedule,
)


class StaticSizer:
    def size_long(self, **_kwargs) -> PositionPlan:
        return PositionPlan(
            quantity=Decimal("12"),
            quantity_unit="CONTRACT",
            estimated_liquidation_price=Decimal("19999"),
            initial_margin=Decimal("0.01"),
            maintenance_margin=Decimal("0.001"),
            margin_buffer=Decimal("1"),
            model_version="test/v1",
        )

    def evaluate_long(self, **_kwargs) -> PositionPlan:
        return self.size_long()


def config() -> TargetLiquidationLadderConfig:
    return TargetLiquidationLadderConfig(
        strategy_id="ladder",
        instrument="BTCUSD_PERP",
        target_liquidation_price=Decimal("20000"),
        first_take_profit_ratio=Decimal("1.10"),
        take_profit_end_price=Decimal("120000"),
        take_profit_count=3,
        tick_size=Decimal("0.1"),
        quantity_step=Decimal("1"),
    )


class StrategyCoreTests(unittest.TestCase):
    def test_hold_baseline_has_no_trading_dependency(self) -> None:
        strategy = HoldBtcStrategy(HoldBtcConfig("hold", "BTCUSD_PERP"))
        strategy.initialize()
        strategy.on_market()
        self.assertEqual(strategy.market_observation_count, 1)

    def test_geometric_schedule_is_increasing_and_fully_allocated(self) -> None:
        levels = build_take_profit_schedule(
            strategy_id="ladder",
            entry_price=Decimal("60000"),
            position_quantity=Decimal("13"),
            first_take_profit_ratio=Decimal("1.1"),
            end_price=Decimal("120000"),
            level_count=3,
            tick_size=Decimal("0.1"),
            quantity_step=Decimal("1"),
        )
        self.assertEqual(levels[0].target_price, Decimal("66000.0"))
        self.assertEqual(levels[-1].target_price, Decimal("120000.0"))
        self.assertEqual(sum((item.quantity for item in levels)), Decimal("13"))
        self.assertEqual([item.quantity for item in levels], [Decimal("4"), Decimal("4"), Decimal("5")])

    def test_state_changes_only_from_fills_and_duplicate_is_idempotent(self) -> None:
        strategy = TargetLiquidationLadderStrategy(config(), StaticSizer())
        strategy.initialize()
        plan = strategy.plan_entry(Decimal("60000"))
        self.assertEqual(strategy.state, LadderState.ENTRY_PENDING)
        fill = StrategyFill(
            fill_id="entry-fill",
            intent_key=plan.intent_key,
            role=StrategyRole.ENTRY,
            side=StrategyOrderSide.BUY,
            price=Decimal("61000"),
            quantity=Decimal("12"),
        )
        self.assertTrue(strategy.on_fill(fill, actual_position_plan=plan.position))
        self.assertFalse(strategy.on_fill(fill, actual_position_plan=plan.position))
        self.assertEqual(strategy.state, LadderState.POSITION_OPEN)
        for level in strategy.take_profit_levels:
            strategy.on_fill(
                StrategyFill(
                    fill_id=f"fill-{level.level}",
                    intent_key=level.intent_key,
                    role=StrategyRole.TAKE_PROFIT,
                    side=StrategyOrderSide.SELL,
                    price=level.target_price,
                    quantity=level.quantity,
                )
            )
        self.assertEqual(strategy.state, LadderState.COMPLETED)
        self.assertEqual(strategy.remaining_quantity, Decimal("0"))

    def test_pure_strategy_package_has_no_runtime_or_web_imports(self) -> None:
        root = Path(__file__).parents[1] / "src" / "trading_strategies"
        forbidden = {
            "strategy_simulation",
            "simulation_runtime",
            "market_simulator",
            "experiment_system",
            "grid_server",
            "binance",
        }
        violations = []
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if name.split(".")[0] in forbidden:
                        violations.append(f"{path.name}:{name}")
        self.assertEqual(violations, [])

    def test_grid_strategies_do_not_import_the_concrete_rule_engine(self) -> None:
        root = (
            Path(__file__).parents[1]
            / "src"
            / "trading_strategies"
            / "grid_following"
        )
        forbidden_names = {"GridRuleEngine", "build_grid_cells"}
        violations = []
        for path in root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                imported = {alias.name for alias in node.names}
                matched = imported & forbidden_names
                if matched:
                    violations.append(
                        f"{path.name}:{','.join(sorted(matched))}"
                    )
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
