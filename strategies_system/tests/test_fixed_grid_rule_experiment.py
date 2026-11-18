"""The generic Strategy adapter routes fixed-grid Rule events end to end."""

from __future__ import annotations

import unittest
from decimal import Decimal

import strategy_simulation  # noqa: F401 - activates sibling packages

from experiment_system import ComponentSpec
from market_simulator import FixedBarMarketSource
from simulation_runtime import SimulationRunner
from strategy_simulation.components.accounts import (
    build_account_runtime,
    resolve_account_component,
)
from strategy_simulation.components.executions import (
    build_execution_runtime,
    resolve_execution_component,
)
from strategy_simulation.plugins.fixed_equal_usdt_grid_rule import (
    FixedEqualUsdtGridSimulationPlugin,
)
from strategy_simulation.registry import SimulationStrategyBuildContext


class FixedGridRuleExperimentTests(unittest.TestCase):
    @staticmethod
    def simulate(mode: str, bars: list[tuple[str, str, str, str]]):
        account = build_account_runtime(resolve_account_component(ComponentSpec(
            "account", "usdm-linear/v1", {
                "instrument": "SOXLUSDT",
                "futures_wallet_usdt": "200",
                "margin_model": "none",
            },
        )))
        execution = build_execution_runtime(resolve_execution_component(ComponentSpec(
            "execution", "daily-bar-execution/v1", {
                "maker_fee_rate": "0",
                "taker_fee_rate": "0",
                "fee_asset": "USDT",
                "funding_model": "none",
            },
        )), contract_size=account.contract_size,
            settlement_asset=account.settlement_asset,
            market_type=account.market_type,
        )
        plugin = FixedEqualUsdtGridSimulationPlugin()
        component = plugin.resolve(ComponentSpec(
            "grid", plugin.strategy_type, {
                "strategy_definition_type": "fixed-range-equal-usdt-grid/v1",
                "strategy_id": "grid-mini",
                "instrument": "SOXLUSDT",
                "lower_price": "40",
                "upper_price": "60",
                "grid_count": 4,
                "grid_budget_usdt": "200",
                "tick_size": "0.01",
                "step_size": "0.01",
                "min_qty": "0.01",
                "min_notional": "5",
                "entry_mode": mode,
                "orders_per_side": 4,
                "rebalance_below": 1,
                "rebalance_above": 6,
            },
        ))
        binding = plugin.build(component, SimulationStrategyBuildContext(
            instrument="SOXLUSDT",
            contract_size=account.contract_size,
            settlement_asset=account.settlement_asset,
            ledger_factory=account.ledger_factory,
            margin_model=account.margin_model,
            fee_model=execution.fee_model,
            market_type=account.market_type,
        ))
        result = SimulationRunner(
            FixedBarMarketSource("SOXLUSDT", bars),
            binding.trade_port,
            fee_model=execution.fee_model,
            ledger_factory=account.ledger_factory,
        ).run()
        return binding, result

    def test_seed_rule_fills_before_grid_rule_and_reconciles_profit(self) -> None:
        binding, result = self.simulate("seeded", [
            ("50", "50", "50", "50"),
            ("50", "50", "50", "50"),
            ("50", "50", "48", "48"),
            ("48", "51", "48", "51"),
            ("51", "55", "51", "55"),
            ("55", "55", "55", "55"),
        ])
        summary = binding.summarize(result)
        self.assertEqual(result.fills[0].tags["rule_key"], "seed")
        self.assertTrue(any(fill.tags["rule_key"] == "grid" for fill in result.fills))
        self.assertEqual(Decimal(summary["pnl_reconciliation_difference"]), Decimal("0"))
        self.assertEqual(result.total_fees, Decimal("0"))
        self.assertEqual(result.total_funding, Decimal("0"))
        self.assertEqual(
            {slot["rule_type"] for slot in summary["application"]["strategies"][0]["rules"]},
            {"initial-entry/v1", "fixed-equal-usdt-grid/v1"},
        )

    def test_triggered_mode_has_no_seed_fill_or_prebuilt_position(self) -> None:
        binding, result = self.simulate("price_triggered", [
            ("50", "50", "50", "50"),
            ("50", "50", "49", "49"),
            ("49", "51", "49", "51"),
        ])
        summary = binding.summarize(result)
        self.assertEqual(summary["seed_quantity"], "0")
        self.assertFalse(any(fill.tags["rule_key"] == "seed" for fill in result.fills))

    def test_same_minute_multiple_exits_are_accounted_before_replanning(self) -> None:
        binding, result = self.simulate("seeded", [
            ("50", "50", "50", "50"),
            ("50", "50", "50", "50"),
            ("50", "60", "50", "60"),
        ])
        exits = [
            fill for fill in result.fills
            if fill.side.value == "SELL" and fill.sequence == 2
        ]
        self.assertGreaterEqual(len(exits), 2)
        self.assertEqual(
            Decimal(binding.summarize(result)["pnl_reconciliation_difference"]),
            Decimal("0"),
        )


if __name__ == "__main__":
    unittest.main()
