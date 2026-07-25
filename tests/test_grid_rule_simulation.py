from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SIMULATOR_ROOT = PROJECT_ROOT.parent / "market_simulator"
PACKAGE_PATHS = (
    PROJECT_ROOT,
    SIMULATOR_ROOT / "packages" / "market_protocol" / "src",
    SIMULATOR_ROOT / "packages" / "market_simulator" / "src",
    SIMULATOR_ROOT / "packages" / "simulation_runtime" / "src",
)
if not SIMULATOR_ROOT.exists():
    raise unittest.SkipTest("sibling market_simulator project is not available")
for package_path in PACKAGE_PATHS:
    sys.path.insert(0, str(package_path))

from grid_rule import (  # noqa: E402
    GridRuleConfig,
    GridMarketType,
    GridMode,
)
from grid_rule.adapters import (  # noqa: E402
    GridRuleSimulationAdapter,
    InverseContractLedger,
)
from market_simulator import FixedBarMarketSource  # noqa: E402
from scripts.run_single_following_grid_simulation import (  # noqa: E402
    build_run,
)
from simulation_runtime import OrderStatus, SimulationRunner  # noqa: E402


class GridRuleSimulationTests(unittest.TestCase):
    def test_three_year_grid_rule_run_is_reproducible(self) -> None:
        document = build_run()

        self.assertEqual(
            document["manifest"]["decision_component"],
            "single_following_grid_strategy",
        )
        self.assertEqual(
            document["manifest"]["strategy_id"],
            "single-following-grid-coinm-long-3y",
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
        self.assertEqual(document["summary"]["completed_cycles"], 75)
        self.assertEqual(document["summary"]["fill_count"], 155)
        self.assertEqual(document["summary"]["cells_added"], 29)
        self.assertEqual(document["summary"]["cells_reclaimed"], 29)
        self.assertEqual(
            document["summary"]["final_positions"],
            {"BTCUSD_PERP": "90"},
        )
        self.assertEqual(document["summary"]["equity_asset"], "BTC")
        self.assertEqual(
            document["summary"]["final_account_metrics"][
                "total_equity_btc"
            ],
            "1.122667821737918014344094411",
        )
        self.assertEqual(
            document["summary"]["final_account_metrics"][
                "total_equity_usdt"
            ],
            "179626.8514780668822950551058",
        )
        self.assertTrue(
            document["summary"]["futures_equity_nonpositive"]
        )
        self.assertEqual(
            document["summary"][
                "first_nonpositive_futures_equity_date"
            ],
            "2028-04-24",
        )

    def test_static_comparison_remains_a_direct_rule_baseline(self) -> None:
        document = build_run(move_grid=False)

        self.assertEqual(
            document["manifest"]["decision_component"],
            "grid_rule_baseline",
        )
        self.assertIsNone(document["manifest"]["strategy_id"])

    def test_adapter_runs_one_long_grid_cycle(self) -> None:
        adapter = GridRuleSimulationAdapter(
            GridRuleConfig(
                grid_id="adapter-long",
                instrument="BTCUSDT",
                mode=GridMode.LONG,
                anchor_price=Decimal("110"),
                grid_ratio=Decimal("0.10"),
                grid_count=1,
                order_notional=Decimal("100"),
                tick_size=Decimal("0.01"),
                quantity_step=Decimal("0.001"),
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

        self.assertEqual(
            [(fill.side.value, fill.price) for fill in result.fills],
            [("BUY", Decimal("100")), ("SELL", Decimal("110"))],
        )
        self.assertEqual(result.final_positions, {})
        self.assertEqual(result.final_equity, Decimal("1010"))
        self.assertEqual(adapter.engine.completed_cycles, 1)
        self.assertEqual(
            [record.status for record in result.orders],
            [OrderStatus.FILLED, OrderStatus.FILLED, OrderStatus.ACTIVE],
        )
        self.assertEqual(result.fills[0].tags["role"], "entry")
        self.assertEqual(result.fills[1].tags["role"], "exit")

    def test_adapter_runs_one_short_grid_cycle(self) -> None:
        adapter = GridRuleSimulationAdapter(
            GridRuleConfig(
                grid_id="adapter-short",
                instrument="BTCUSDT",
                mode=GridMode.SHORT,
                anchor_price=Decimal("100"),
                grid_ratio=Decimal("0.10"),
                grid_count=1,
                order_notional=Decimal("100"),
                tick_size=Decimal("0.01"),
                quantity_step=Decimal("0.001"),
            )
        )
        source = FixedBarMarketSource(
            "BTCUSDT",
            [
                ("105", "106", "104", "105"),
                ("105", "111", "104", "109"),
                ("108", "109", "99", "101"),
            ],
        )

        result = SimulationRunner(
            source,
            adapter,
            initial_equity=Decimal("1000"),
        ).run()

        self.assertEqual(
            [(fill.side.value, fill.price) for fill in result.fills],
            [("SELL", Decimal("110")), ("BUY", Decimal("100"))],
        )
        self.assertEqual(result.final_positions, {})
        self.assertEqual(result.final_equity, Decimal("1009.090"))
        self.assertEqual(adapter.engine.completed_cycles, 1)

    def test_coinm_grid_keeps_spot_btc_and_settles_contract_pnl_in_btc(
        self,
    ) -> None:
        config = GridRuleConfig(
            grid_id="adapter-coinm-long",
            instrument="BTCUSD_PERP",
            mode=GridMode.LONG,
            anchor_price=Decimal("110000"),
            grid_ratio=Decimal("0.10"),
            grid_count=1,
            order_notional=Decimal("0"),
            tick_size=Decimal("0.1"),
            quantity_step=Decimal("1"),
            market_type=GridMarketType.COINM,
            order_coin_qty=Decimal("0.002"),
            contract_size=Decimal("100"),
        )
        adapter = GridRuleSimulationAdapter(config)
        source = FixedBarMarketSource(
            config.instrument,
            [
                ("105000", "106000", "104000", "105000"),
                ("105000", "106000", "99000", "101000"),
                ("101000", "111000", "100000", "110000"),
            ],
        )

        result = SimulationRunner(
            source,
            adapter,
            ledger_factory=lambda: InverseContractLedger(
                instrument=config.instrument,
                contract_size=config.contract_size,
                spot_base_balance=Decimal("1"),
                futures_wallet_balance=Decimal("0.1"),
            ),
        ).run()

        expected_pnl = Decimal("200") * (
            Decimal("1") / Decimal("100000")
            - Decimal("1") / Decimal("110000")
        )
        self.assertEqual(result.realized_pnl, expected_pnl)
        self.assertEqual(result.final_cash, Decimal("0.1") + expected_pnl)
        self.assertEqual(
            result.final_equity,
            Decimal("1.1") + expected_pnl,
        )
        self.assertEqual(
            result.final_account_metrics["spot_btc"],
            Decimal("1"),
        )
        self.assertEqual(
            result.final_account_metrics["total_equity_usdt"],
            Decimal("121020"),
        )


if __name__ == "__main__":
    unittest.main()
