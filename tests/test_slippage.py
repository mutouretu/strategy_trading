from __future__ import annotations

import unittest
from decimal import Decimal
from typing import Sequence

from market_protocol import MarketFrame
from market_simulator import FixedBarMarketSource
from simulation_runtime import (
    FixedBpsSlippageModel,
    FixedRateFeeModel,
    NoSlippageModel,
    OrderSide,
    SimFill,
    SimulationRunner,
    TradeInstruction,
    TradeIntentMode,
)


INSTRUMENT = "BTCUSDT"


def frame() -> MarketFrame:
    return MarketFrame(
        sequence=1,
        timestamp=86_400_000,
        instrument=INSTRUMENT,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
    )


def instruction(
    *,
    side: OrderSide,
    intent_mode: TradeIntentMode = TradeIntentMode.ACTIVE,
) -> TradeInstruction:
    return TradeInstruction(
        instruction_key=f"{intent_mode.value}:{side.value}",
        source_intent_key=f"intent:{intent_mode.value}:{side.value}",
        instrument=INSTRUMENT,
        side=side,
        quantity=Decimal("1"),
        price=Decimal("100"),
        frame_sequence=1,
        intent_mode=intent_mode,
    )


class SlippageModelTests(unittest.TestCase):
    def test_no_slippage_preserves_reference_price(self) -> None:
        price = NoSlippageModel().apply(
            instruction(side=OrderSide.BUY),
            Decimal("100"),
            frame(),
        )

        self.assertEqual(price, Decimal("100"))

    def test_fixed_bps_moves_active_buy_and_sell_adversely(self) -> None:
        model = FixedBpsSlippageModel(Decimal("25"))

        self.assertEqual(
            model.apply(
                instruction(side=OrderSide.BUY),
                Decimal("100"),
                frame(),
            ),
            Decimal("100.25"),
        )
        self.assertEqual(
            model.apply(
                instruction(side=OrderSide.SELL),
                Decimal("100"),
                frame(),
            ),
            Decimal("99.75"),
        )

    def test_fixed_bps_keeps_passive_touch_price(self) -> None:
        price = FixedBpsSlippageModel(Decimal("25")).apply(
            instruction(
                side=OrderSide.BUY,
                intent_mode=TradeIntentMode.PASSIVE,
            ),
            Decimal("100"),
            frame(),
        )

        self.assertEqual(price, Decimal("100"))

    def test_invalid_fixed_bps_is_rejected(self) -> None:
        for value in ("-0.01", "10000", "Infinity"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    FixedBpsSlippageModel(Decimal(value))


class _RoundTripPort:
    def initialize(self, current: MarketFrame) -> None:
        return None

    def instructions_for(
        self,
        current: MarketFrame,
    ) -> tuple[TradeInstruction, ...]:
        if current.sequence == 1:
            return (
                TradeInstruction(
                    instruction_key="active-buy@1",
                    source_intent_key="active-buy",
                    instrument=INSTRUMENT,
                    side=OrderSide.BUY,
                    quantity=Decimal("1"),
                    price=Decimal("100"),
                    frame_sequence=1,
                    intent_mode=TradeIntentMode.ACTIVE,
                ),
            )
        if current.sequence == 2:
            return (
                TradeInstruction(
                    instruction_key="active-sell@2",
                    source_intent_key="active-sell",
                    instrument=INSTRUMENT,
                    side=OrderSide.SELL,
                    quantity=Decimal("1"),
                    price=Decimal("100"),
                    frame_sequence=2,
                    intent_mode=TradeIntentMode.ACTIVE,
                    reduce_only=True,
                ),
            )
        return ()

    def on_fills(self, fills: Sequence[SimFill]) -> None:
        return None

    def on_market(self, current: MarketFrame) -> None:
        return None


def source() -> FixedBarMarketSource:
    return FixedBarMarketSource(
        INSTRUMENT,
        [
            ("100", "101", "99", "100"),
            ("100", "101", "99", "100"),
            ("100", "101", "99", "100"),
        ],
    )


class SlippageRuntimeTests(unittest.TestCase):
    def test_effective_price_precedes_fee_and_ledger_accounting(self) -> None:
        result = SimulationRunner(
            source(),
            trade_port=_RoundTripPort(),
            initial_equity=Decimal("1000"),
            slippage_model=FixedBpsSlippageModel(Decimal("100")),
            fee_model=FixedRateFeeModel(
                maker_fee_rate=Decimal("0.01"),
                taker_fee_rate=Decimal("0.01"),
            ),
        ).run()

        buy, sell = result.fills
        self.assertEqual(
            [buy.reference_price, sell.reference_price],
            [Decimal("100"), Decimal("100")],
        )
        self.assertEqual(
            [buy.price, sell.price],
            [Decimal("101"), Decimal("99")],
        )
        self.assertEqual(
            [buy.slippage_amount, sell.slippage_amount],
            [Decimal("1"), Decimal("-1")],
        )
        self.assertEqual(
            [buy.slippage_bps, sell.slippage_bps],
            [Decimal("100"), Decimal("-100")],
        )
        self.assertEqual(
            [buy.fee_amount, sell.fee_amount],
            [Decimal("1.01"), Decimal("0.99")],
        )
        self.assertEqual(result.gross_realized_pnl, Decimal("-2"))
        self.assertEqual(result.total_fees, Decimal("2.00"))
        self.assertEqual(result.net_realized_pnl, Decimal("-4.00"))
        self.assertEqual(result.final_cash, Decimal("996.00"))
        self.assertTrue(result.slippage_enabled)
        self.assertEqual(result.slippage_source, "FIXED_BPS")

    def test_default_zero_slippage_is_explicit_and_compatible(self) -> None:
        result = SimulationRunner(
            source(),
            trade_port=_RoundTripPort(),
            initial_equity=Decimal("1000"),
        ).run()

        self.assertFalse(result.slippage_enabled)
        self.assertEqual(result.slippage_source, "ZERO")
        self.assertEqual(result.gross_realized_pnl, Decimal("0"))
        self.assertEqual(result.final_cash, Decimal("1000"))
        self.assertTrue(
            all(
                fill.reference_price == fill.price
                and fill.slippage_amount == 0
                and fill.slippage_bps == 0
                for fill in result.fills
            )
        )


if __name__ == "__main__":
    unittest.main()
