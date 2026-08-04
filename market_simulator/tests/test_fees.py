from __future__ import annotations

import unittest
from decimal import Decimal
from typing import Sequence

from market_protocol import MarketFrame
from market_simulator import FixedBarMarketSource
from simulation_runtime import (
    FixedRateFeeModel,
    LiquidityRole,
    OrderSide,
    SimFill,
    SimulationRunner,
    TradeInstruction,
    TradeIntentMode,
)


class _RoundTripPort:
    def initialize(self, frame: MarketFrame) -> None:
        return None

    def instructions_for(
        self,
        frame: MarketFrame,
    ) -> tuple[TradeInstruction, ...]:
        if frame.sequence == 1:
            return (
                TradeInstruction(
                    instruction_key="passive-entry@1",
                    source_intent_key="passive-entry",
                    instrument=frame.instrument,
                    side=OrderSide.BUY,
                    quantity=Decimal("2"),
                    price=Decimal("100"),
                    frame_sequence=frame.sequence,
                    intent_mode=TradeIntentMode.PASSIVE,
                ),
            )
        if frame.sequence == 2:
            return (
                TradeInstruction(
                    instruction_key="active-exit@2",
                    source_intent_key="active-exit",
                    instrument=frame.instrument,
                    side=OrderSide.SELL,
                    quantity=Decimal("2"),
                    price=Decimal("110"),
                    frame_sequence=frame.sequence,
                    intent_mode=TradeIntentMode.ACTIVE,
                    reduce_only=True,
                ),
            )
        return ()

    def on_fills(self, fills: Sequence[SimFill]) -> None:
        return None

    def on_market(self, frame: MarketFrame) -> None:
        return None


def _source() -> FixedBarMarketSource:
    return FixedBarMarketSource(
        "BTCUSDT",
        [
            ("100", "101", "99", "100"),
            ("100", "101", "99", "100"),
            ("110", "111", "109", "110"),
        ],
    )


class FixedRateFeeTests(unittest.TestCase):
    def test_passive_and_active_rates_flow_into_ledger(self) -> None:
        result = SimulationRunner(
            _source(),
            trade_port=_RoundTripPort(),
            initial_equity=Decimal("1000"),
            fee_model=FixedRateFeeModel(
                maker_fee_rate=Decimal("0.001"),
                taker_fee_rate=Decimal("0.002"),
            ),
        ).run()

        self.assertEqual(
            [fill.liquidity_role for fill in result.fills],
            [LiquidityRole.MAKER, LiquidityRole.TAKER],
        )
        self.assertEqual(
            [fill.fee_amount for fill in result.fills],
            [Decimal("0.2"), Decimal("0.44")],
        )
        self.assertEqual(
            [fill.fee_asset for fill in result.fills],
            ["USDT", "USDT"],
        )
        self.assertEqual(result.gross_realized_pnl, Decimal("20"))
        self.assertEqual(result.total_fees, Decimal("0.64"))
        self.assertEqual(result.net_realized_pnl, Decimal("19.36"))
        self.assertEqual(result.realized_pnl, Decimal("19.36"))
        self.assertEqual(result.final_cash, Decimal("1019.36"))
        self.assertEqual(result.final_equity, Decimal("1019.36"))
        self.assertEqual(
            result.equity_curve[-1].total_fees,
            Decimal("0.64"),
        )

    def test_default_zero_fee_model_preserves_gross_result(self) -> None:
        result = SimulationRunner(
            _source(),
            trade_port=_RoundTripPort(),
            initial_equity=Decimal("1000"),
        ).run()

        self.assertEqual(result.gross_realized_pnl, Decimal("20"))
        self.assertEqual(result.total_fees, Decimal("0"))
        self.assertEqual(result.net_realized_pnl, Decimal("20"))
        self.assertEqual(result.final_equity, Decimal("1020"))
        self.assertTrue(
            all(fill.fee_amount == 0 for fill in result.fills)
        )

    def test_negative_fee_rate_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "maker_fee_rate",
        ):
            FixedRateFeeModel(
                maker_fee_rate=Decimal("-0.001"),
                taker_fee_rate=Decimal("0"),
            )


if __name__ == "__main__":
    unittest.main()
