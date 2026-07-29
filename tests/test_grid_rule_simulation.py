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
    InverseContractFeeModel,
    InverseContractLedger,
)
from market_simulator import FixedBarMarketSource  # noqa: E402
from scripts.run_single_following_grid_simulation import (  # noqa: E402
    build_run,
)
from simulation_runtime import IntentStatus, SimulationRunner  # noqa: E402


class GridRuleSimulationTests(unittest.TestCase):
    def test_three_year_grid_rule_run_is_reproducible(self) -> None:
        document = build_run()

        self.assertEqual(
            document["manifest"]["simulation_adapter"],
            "single_following_grid_simulation_adapter",
        )
        self.assertEqual(
            document["manifest"]["strategy_id"],
            "single-following-grid-coinm-long-3y",
        )
        self.assertEqual(
            document["manifest"]["maker_fee_rate"],
            "0.0002",
        )
        self.assertEqual(
            document["manifest"]["taker_fee_rate"],
            "0.0005",
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
            "1.122361740844490370041605073",
        )
        self.assertEqual(
            document["summary"]["final_account_metrics"][
                "total_equity_usdt"
            ],
            "179577.8785351184592066568117",
        )
        self.assertEqual(
            document["summary"]["total_fees"],
            "0.0003060808934276443024893379629",
        )
        self.assertEqual(
            sum(
                (
                    Decimal(fill["fee_amount"])
                    for fill in document["fills"]
                ),
                Decimal("0"),
            ),
            Decimal(document["summary"]["total_fees"]),
        )
        self.assertEqual(
            {
                fill["liquidity_role"]
                for fill in document["fills"]
            },
            {"MAKER"},
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
            document["manifest"]["simulation_adapter"],
            "grid_rule_simulation_adapter",
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
            trade_port=adapter,
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
            [record.status for record in result.intents],
            [
                IntentStatus.FILLED,
                IntentStatus.FILLED,
                IntentStatus.WAITING,
            ],
        )
        self.assertEqual(
            [record.intent.reduce_only for record in result.intents],
            [False, True, False],
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
            trade_port=adapter,
            initial_equity=Decimal("1000"),
        ).run()

        self.assertEqual(
            [(fill.side.value, fill.price) for fill in result.fills],
            [("SELL", Decimal("110")), ("BUY", Decimal("100"))],
        )
        self.assertEqual(result.final_positions, {})
        self.assertEqual(result.final_equity, Decimal("1009.090"))
        self.assertEqual(adapter.engine.completed_cycles, 1)
        self.assertEqual(
            [record.intent.reduce_only for record in result.intents],
            [False, True, False],
        )

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
            trade_port=adapter,
            fee_model=InverseContractFeeModel(
                contract_size=config.contract_size,
                maker_fee_rate=Decimal("0.0002"),
                taker_fee_rate=Decimal("0.0005"),
            ),
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
        entry_fee = (
            Decimal("200")
            / Decimal("100000")
            * Decimal("0.0002")
        )
        exit_fee = (
            Decimal("200")
            / Decimal("110000")
            * Decimal("0.0002")
        )
        expected_fees = entry_fee + exit_fee
        expected_net = expected_pnl - expected_fees
        expected_cash = Decimal("0.1") + expected_net
        expected_equity = Decimal("1") + expected_cash
        self.assertEqual(result.gross_realized_pnl, expected_pnl)
        self.assertEqual(result.total_fees, expected_fees)
        self.assertEqual(result.realized_pnl, expected_net)
        self.assertEqual(result.final_cash, expected_cash)
        self.assertEqual(
            result.final_equity,
            expected_equity,
        )
        self.assertEqual(
            result.final_account_metrics["spot_btc"],
            Decimal("1"),
        )
        self.assertEqual(
            result.final_account_metrics["total_equity_usdt"],
            expected_equity * Decimal("110000"),
        )
        self.assertEqual(
            [fill.fee_asset for fill in result.fills],
            ["BTC", "BTC"],
        )


if __name__ == "__main__":
    unittest.main()
