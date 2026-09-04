from __future__ import annotations

import unittest
from decimal import Decimal

from trading_ledger.domain import (
    TradeSide,
    calculate_buy,
    calculate_fees,
    calculate_sell,
)


class AccountingTests(unittest.TestCase):
    def test_buy_includes_minimum_commission_in_percentage_budget(self) -> None:
        with self.assertRaisesRegex(ValueError, "手续费"):
            calculate_buy(
                Decimal("10000.00"), Decimal("0.10"), Decimal("9.99")
            )

    def test_buy_rounds_down_to_whole_lots_after_fees(self) -> None:
        result = calculate_buy(
            Decimal("100000.00"), Decimal("0.50"), Decimal("10.00")
        )
        self.assertEqual(result.quantity, Decimal("4900"))
        self.assertEqual(result.commission_amount, Decimal("5.00"))
        self.assertEqual(result.net_cash_amount, Decimal("-49005.00"))

    def test_sell_uses_sellable_position_and_full_sell_keeps_odd_lot(self) -> None:
        partial = calculate_sell(
            Decimal("1050"), Decimal("0.50"), Decimal("10.00")
        )
        full = calculate_sell(Decimal("1050"), Decimal("1"), Decimal("10.00"))
        self.assertEqual(partial.quantity, Decimal("500"))
        self.assertEqual(full.quantity, Decimal("1050"))

    def test_sell_charges_all_three_fees(self) -> None:
        commission, stamp_tax, transfer_fee = calculate_fees(
            Decimal("100000.00"), TradeSide.SELL
        )
        self.assertEqual(commission, Decimal("8.50"))
        self.assertEqual(stamp_tax, Decimal("100.00"))
        self.assertEqual(transfer_fee, Decimal("1.00"))

if __name__ == "__main__":
    unittest.main()
