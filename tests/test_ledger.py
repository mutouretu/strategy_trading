from __future__ import annotations

import unittest
from decimal import Decimal

from simulation_runtime import LinearLedger, OrderSide, SimFill


def fill(
    fill_id: str,
    side: OrderSide,
    price: str,
    quantity: str,
) -> SimFill:
    return SimFill(
        fill_id=fill_id,
        order_key=fill_id,
        instrument="BTCUSD",
        side=side,
        price=Decimal(price),
        quantity=Decimal(quantity),
        sequence=0,
        timestamp=0,
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
