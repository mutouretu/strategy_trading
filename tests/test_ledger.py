from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

from simulation_runtime import (
    LinearLedger,
    LiquidityRole,
    OrderSide,
    SimFill,
    TradeIntentMode,
)


def fill(
    fill_id: str,
    side: OrderSide,
    price: str,
    quantity: str,
) -> SimFill:
    return SimFill(
        fill_id=fill_id,
        instruction_key=f"instruction:{fill_id}",
        source_intent_key=f"intent:{fill_id}",
        intent_mode=TradeIntentMode.ACTIVE,
        instrument="BTCUSD",
        side=side,
        price=Decimal(price),
        quantity=Decimal(quantity),
        sequence=0,
        timestamp=0,
        liquidity_role=LiquidityRole.TAKER,
        fee_rate=Decimal("0"),
        fee_amount=Decimal("0"),
        fee_asset="USDT",
        reduce_only=False,
    )


class LinearLedgerTests(unittest.TestCase):
    def test_average_cost_and_realized_pnl_for_long_position(self) -> None:
        ledger = LinearLedger(Decimal("1000"))

        ledger.apply(fill("buy-1", OrderSide.BUY, "100", "1"))
        ledger.apply(fill("buy-2", OrderSide.BUY, "110", "1"))
        ledger.apply(fill("sell-1", OrderSide.SELL, "120", "1"))

        self.assertEqual(ledger.positions, {"BTCUSD": Decimal("1")})
        self.assertEqual(ledger.average_costs, {"BTCUSD": Decimal("105")})
        self.assertEqual(ledger.realized_pnl, Decimal("15"))
        self.assertEqual(
            ledger.equity({"BTCUSD": Decimal("115")}),
            Decimal("1025"),
        )

    def test_crossing_from_long_to_short_resets_average_cost(self) -> None:
        ledger = LinearLedger()

        ledger.apply(fill("buy", OrderSide.BUY, "100", "1"))
        ledger.apply(fill("sell", OrderSide.SELL, "110", "2"))

        self.assertEqual(ledger.positions, {"BTCUSD": Decimal("-1")})
        self.assertEqual(ledger.average_costs, {"BTCUSD": Decimal("110")})
        self.assertEqual(ledger.realized_pnl, Decimal("10"))

    def test_multiple_fills_accumulate_fees_without_double_counting(
        self,
    ) -> None:
        ledger = LinearLedger(Decimal("1000"))

        ledger.apply(
            replace(
                fill("buy-1", OrderSide.BUY, "100", "1"),
                fee_rate=Decimal("0.001"),
                fee_amount=Decimal("0.1"),
            )
        )
        ledger.apply(
            replace(
                fill("buy-2", OrderSide.BUY, "110", "1"),
                fee_rate=Decimal("0.001"),
                fee_amount=Decimal("0.11"),
            )
        )
        ledger.apply(
            replace(
                fill("sell-1", OrderSide.SELL, "120", "1"),
                fee_rate=Decimal("0.001"),
                fee_amount=Decimal("0.12"),
            )
        )

        self.assertEqual(ledger.gross_realized_pnl, Decimal("15"))
        self.assertEqual(ledger.total_fees, Decimal("0.33"))
        self.assertEqual(ledger.net_realized_pnl, Decimal("14.67"))
        self.assertEqual(ledger.realized_pnl, Decimal("14.67"))
        self.assertEqual(ledger.cash, Decimal("909.67"))
        self.assertEqual(
            ledger.equity({"BTCUSD": Decimal("115")}),
            Decimal("1024.67"),
        )
