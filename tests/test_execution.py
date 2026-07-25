from __future__ import annotations

import unittest
from decimal import Decimal

from market_protocol import MarketFrame
from simulation_runtime import (
    ActiveOrder,
    BarTouchExecutionModel,
    OrderSide,
    OrderType,
    SimOrder,
)


def daily_frame(sequence: int = 1) -> MarketFrame:
    return MarketFrame(
        sequence=sequence,
        timestamp=sequence * 86_400_000,
        instrument="BTCUSD",
        open=Decimal("105"),
        high=Decimal("111"),
        low=Decimal("85"),
        close=Decimal("95"),
    )


class BarTouchExecutionModelTests(unittest.TestCase):
    def test_bar_range_fills_buy_and_sell_limits_without_path_assumption(self) -> None:
        orders = [
            ActiveOrder(
                SimOrder(
                    order_key="buy-90",
                    instrument="BTCUSD",
                    side=OrderSide.BUY,
                    order_type=OrderType.LIMIT,
                    limit_price=Decimal("90"),
                    quantity=Decimal("1"),
                ),
                0,
            ),
            ActiveOrder(
                SimOrder(
                    order_key="sell-110",
                    instrument="BTCUSD",
                    side=OrderSide.SELL,
                    order_type=OrderType.LIMIT,
                    limit_price=Decimal("110"),
                    quantity=Decimal("1"),
                ),
                0,
            ),
        ]

        fills = BarTouchExecutionModel().match(daily_frame(), orders)

        self.assertEqual(
            [(fill.order_key, fill.price) for fill in fills],
            [("buy-90", Decimal("90")), ("sell-110", Decimal("110"))],
        )

    def test_limit_outside_bar_range_does_not_fill(self) -> None:
        order = ActiveOrder(
            SimOrder(
                order_key="buy-80",
                instrument="BTCUSD",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                limit_price=Decimal("80"),
                quantity=Decimal("1"),
            ),
            0,
        )

        self.assertEqual(BarTouchExecutionModel().match(daily_frame(), [order]), ())

    def test_market_order_fills_at_next_bar_open(self) -> None:
        order = ActiveOrder(
            SimOrder(
                order_key="signal-buy",
                instrument="BTCUSD",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=Decimal("1"),
            ),
            0,
        )

        fill = BarTouchExecutionModel().match(daily_frame(), [order])[0]

        self.assertEqual(fill.price, Decimal("105"))

    def test_order_created_on_current_bar_cannot_use_current_range(self) -> None:
        order = ActiveOrder(
            SimOrder(
                order_key="late-buy",
                instrument="BTCUSD",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                limit_price=Decimal("100"),
                quantity=Decimal("1"),
            ),
            1,
        )

        self.assertEqual(BarTouchExecutionModel().match(daily_frame(), [order]), ())


if __name__ == "__main__":
    unittest.main()
