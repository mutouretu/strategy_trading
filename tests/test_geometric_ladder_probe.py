from __future__ import annotations

import unittest
from decimal import Decimal

from examples.geometric_ladder_probe import (
    LEVEL_COUNT,
    ORDER_QUANTITY,
    run_ladder_probe,
)
from simulation_runtime import OrderSide, OrderStatus


class GeometricLadderProbeTests(unittest.TestCase):
    def test_long_run_preserves_account_and_order_invariants(self) -> None:
        result = run_ladder_probe()

        self.assertEqual(len(result.frames), 1097)
        self.assertEqual(len(result.equity_curve), len(result.frames))
        self.assertEqual(min(frame.low for frame in result.frames), Decimal("40000"))
        self.assertEqual(
            max(frame.high for frame in result.frames),
            Decimal("200000"),
        )
        self.assertEqual(len(result.fills), 84)
        self.assertEqual(
            sum(fill.side == OrderSide.SELL for fill in result.fills),
            42,
        )
        self.assertEqual(result.final_positions, {})
        self.assertEqual(result.realized_pnl, Decimal("972.8853"))
        self.assertEqual(result.final_equity, Decimal("10972.8853"))
        self.assertEqual(
            len({record.order.order_key for record in result.orders}),
            len(result.orders),
        )
        records = {record.order.order_key: record for record in result.orders}
        for fill in result.fills:
            record = records[fill.order_key]
            self.assertEqual(record.status, OrderStatus.FILLED)
            self.assertGreater(fill.sequence, record.active_from_sequence)
        for snapshot in result.equity_curve:
            self.assertGreaterEqual(snapshot.positions.get("BTCUSD", Decimal("0")), 0)
            self.assertEqual(
                snapshot.equity,
                snapshot.cash
                + snapshot.positions.get("BTCUSD", Decimal("0"))
                * snapshot.marks["BTCUSD"],
            )

        bought = sum(
            (
                fill.quantity
                for fill in result.fills
                if fill.side == OrderSide.BUY
            ),
            Decimal("0"),
        )
        sold = sum(
            (
                fill.quantity
                for fill in result.fills
                if fill.side == OrderSide.SELL
            ),
            Decimal("0"),
        )
        self.assertEqual(
            result.final_positions.get("BTCUSD", Decimal("0")),
            bought - sold,
        )
        self.assertLessEqual(
            result.final_positions.get("BTCUSD", Decimal("0")),
            ORDER_QUANTITY * LEVEL_COUNT,
        )


if __name__ == "__main__":
    unittest.main()
