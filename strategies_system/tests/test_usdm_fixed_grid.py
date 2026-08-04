from __future__ import annotations

import unittest
from decimal import Decimal

import strategy_simulation  # noqa: F401 - activates sibling checkouts

from experiment_system import ComponentSpec
from market_simulator import FixedBarMarketSource
from simulation_runtime import SimulationRunner
from strategy_simulation.components import (
    build_account_runtime,
    build_execution_runtime,
    resolve_account_component,
    resolve_execution_component,
)
from strategy_simulation.plugins import FixedGridSimulationPlugin
from strategy_simulation.registry import SimulationStrategyBuildContext


class UsdmFixedGridTests(unittest.TestCase):
    def account(self, wallet: str = "1000"):
        return build_account_runtime(
            resolve_account_component(
                ComponentSpec(
                    key="usdm",
                    type="usdm-linear/v1",
                    parameters={
                        "instrument": "BTCUSDT",
                        "futures_wallet_usdt": wallet,
                        "margin_model": "flat-maintenance/v1",
                        "leverage": "3",
                        "maintenance_margin_rate": "0.005",
                        "mark_price_sampling": "ADVERSE_EXTREME",
                    },
                )
            )
        )

    @staticmethod
    def execution(account):
        return build_execution_runtime(
            resolve_execution_component(
                ComponentSpec(
                    key="passive",
                    type="daily-bar-execution/v1",
                    parameters={
                        "maker_fee_rate": "0",
                        "taker_fee_rate": "0",
                        "fee_asset": "USDT",
                        "funding_model": "none",
                    },
                )
            ),
            contract_size=account.contract_size,
            settlement_asset=account.settlement_asset,
            market_type=account.market_type,
        )

    @staticmethod
    def strategy(account, execution, *, mode: str = "short"):
        plugin = FixedGridSimulationPlugin()
        component = plugin.resolve(
            ComponentSpec(
                key="short-grid",
                type="fixed-grid/v1",
                parameters={
                    "strategy_id": "short-grid",
                    "rule": {
                        "grid_id": "short-grid-rule",
                        "instrument": "BTCUSDT",
                        "mode": mode,
                        "anchor_price": "110" if mode == "long" else "100",
                        "grid_ratio": "0.10",
                        "grid_count": 1,
                        "order_notional": "100",
                        "tick_size": "0.01",
                        "quantity_step": "0.001",
                        "move_grid": False,
                        "market_type": "usdm",
                    },
                },
            )
        )
        return plugin.build(
            component,
            SimulationStrategyBuildContext(
                instrument=account.instrument,
                contract_size=account.contract_size,
                settlement_asset=account.settlement_asset,
                ledger_factory=account.ledger_factory,
                margin_model=account.margin_model,
                fee_model=execution.fee_model,
                market_type=account.market_type,
            ),
        )

    def simulate(
        self,
        bars,
        wallet: str = "1000",
        *,
        mode: str = "short",
    ):
        account = self.account(wallet)
        execution = self.execution(account)
        binding = self.strategy(account, execution, mode=mode)
        return SimulationRunner(
            FixedBarMarketSource("BTCUSDT", bars),
            trade_port=binding.trade_port,
            fee_model=execution.fee_model,
            ledger_factory=account.ledger_factory,
            margin_model=account.margin_model,
            mark_price_sampling=account.mark_price_sampling,
        ).run()

    def test_short_cycle_settles_profit_in_usdt(self) -> None:
        result = self.simulate(
            [
                ("105", "106", "104", "105"),
                ("105", "111", "104", "109"),
                ("108", "109", "99", "101"),
            ]
        )

        self.assertEqual(result.equity_asset, "USDT")
        self.assertEqual(
            [(fill.side.value, fill.price) for fill in result.fills],
            [("SELL", Decimal("110")), ("BUY", Decimal("100"))],
        )
        self.assertEqual(result.final_positions, {})
        self.assertEqual(result.final_equity, Decimal("1009.090"))
        self.assertFalse(result.liquidated)

    def test_adverse_high_liquidates_a_usdm_short(self) -> None:
        result = self.simulate(
            [
                ("105", "106", "104", "105"),
                ("105", "111", "104", "109"),
                ("109", "250", "108", "110"),
            ],
            wallet="100",
        )

        self.assertTrue(result.liquidated)
        self.assertEqual(result.termination_sequence, 2)
        event = result.account_events[-1]
        self.assertEqual(event.snapshot.mark_price, Decimal("250"))
        self.assertLessEqual(event.snapshot.margin_buffer, Decimal("0"))
        self.assertGreater(
            result.margin_snapshots[1].estimated_liquidation_price,
            Decimal("200"),
        )

    def test_adverse_low_liquidates_a_usdm_long(self) -> None:
        result = self.simulate(
            [
                ("105", "106", "104", "105"),
                ("105", "106", "99", "101"),
                ("101", "102", "50", "100"),
            ],
            wallet="40",
            mode="long",
        )

        self.assertTrue(result.liquidated)
        self.assertEqual(result.account_events[-1].snapshot.mark_price, Decimal("50"))
        entry_snapshot = next(
            snapshot
            for snapshot in result.margin_snapshots
            if snapshot.position_quantity == Decimal("1")
            and not snapshot.liquidation_triggered
        )
        self.assertGreater(
            entry_snapshot.estimated_liquidation_price,
            Decimal("60"),
        )
        self.assertLess(
            entry_snapshot.estimated_liquidation_price,
            Decimal("61"),
        )


if __name__ == "__main__":
    unittest.main()
