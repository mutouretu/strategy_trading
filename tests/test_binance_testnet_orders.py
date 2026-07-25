from __future__ import annotations

import os
import time
import unittest
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from pathlib import Path
from urllib.parse import urlparse

from grid_server.binance import BinanceFuturesExchange
from grid_server.config import binance_base_url, binance_credentials, load_environment
from grid_server.domain import OrderSide, OrderSnapshot, OrderStatus
from grid_server.exchange import OrderNotFoundError


SYMBOL = "UNIUSDT"
TARGET_NOTIONAL = Decimal("15")
MIN_NOTIONAL = Decimal("10")
MAX_NOTIONAL = Decimal("20")


def round_to_step(value: Decimal, step: Decimal, rounding: str) -> Decimal:
    if step <= 0:
        raise ValueError("exchange returned an invalid step size")
    return (value / step).to_integral_value(rounding=rounding) * step


def wait_for_status(
    exchange: BinanceFuturesExchange,
    symbol: str,
    order_id: int,
    expected: OrderStatus,
    attempts: int = 30,
) -> OrderSnapshot:
    snapshot: OrderSnapshot | None = None
    for _ in range(attempts):
        try:
            snapshot = exchange.get_order(symbol, order_id)
        except OrderNotFoundError:
            # Futures Testnet can briefly return -2013 immediately after a
            # successful create response while order history propagates.
            time.sleep(0.1)
            continue
        if snapshot.status == expected:
            return snapshot
        time.sleep(0.1)
    if snapshot is None:
        raise OrderNotFoundError(
            f"{symbol} order {order_id} did not become queryable in time"
        )
    return snapshot


@unittest.skipUnless(
    os.getenv("RUN_BINANCE_TESTNET_ORDER_TEST") == "1",
    "set RUN_BINANCE_TESTNET_ORDER_TEST=1 to place testnet orders",
)
class BinanceTestnetLimitOrderTests(unittest.TestCase):
    """Place, query and cancel isolated USD-M orders on Binance Futures Testnet."""

    @classmethod
    def setUpClass(cls) -> None:
        env_file = Path(os.getenv("GRID_ENV_FILE", "test.env"))
        load_environment(env_file, override=True)

        base_url = binance_base_url()
        hostname = (urlparse(base_url).hostname or "").lower()
        if hostname != "testnet.binancefuture.com":
            raise RuntimeError(
                "refusing to place orders: BINANCE_BASE_URL must be "
                "https://testnet.binancefuture.com"
            )

        api_key, api_secret = binance_credentials(required=True)
        cls.exchange = BinanceFuturesExchange(api_key, api_secret, base_url)
        cls.exchange.set_hedge_mode(True)

    def test_buy_and_sell_limit_orders_can_be_placed_queried_and_canceled(self) -> None:
        filters = self.exchange.get_symbol_filters(SYMBOL)
        mark_price = self.exchange.get_mark_price(SYMBOL)
        required_notional = max(TARGET_NOTIONAL, filters.min_notional, MIN_NOTIONAL)
        quantity = max(
            filters.min_qty,
            round_to_step(required_notional / mark_price, filters.step_size, ROUND_UP),
        )
        actual_notional = quantity * mark_price

        self.assertGreaterEqual(actual_notional, MIN_NOTIONAL)
        self.assertLessEqual(
            actual_notional,
            MAX_NOTIONAL,
            f"refusing order outside the 10-20 USDT test budget: {actual_notional}",
        )

        cases = (
            (
                OrderSide.BUY,
                "LONG",
                round_to_step(mark_price * Decimal("0.96"), filters.tick_size, ROUND_DOWN),
            ),
            (
                OrderSide.SELL,
                "SHORT",
                round_to_step(mark_price * Decimal("1.04"), filters.tick_size, ROUND_UP),
            ),
        )

        for index, (side, position_side, price) in enumerate(cases):
            with self.subTest(side=side):
                order_id: int | None = None
                client_order_id = f"gt-test-{side.value.lower()}-{int(time.time() * 1000)}-{index}"
                try:
                    order_id = self.exchange.place_limit_order(
                        SYMBOL,
                        side,
                        position_side,
                        quantity,
                        price,
                        client_order_id,
                    )
                    snapshot = self.exchange.get_order(SYMBOL, order_id)

                    self.assertEqual(snapshot.status, OrderStatus.NEW)
                    self.assertEqual(snapshot.side, side)
                    self.assertEqual(snapshot.position_side, position_side)
                    self.assertEqual(snapshot.original_qty, quantity)
                    self.assertEqual(snapshot.price, price)
                    self.assertEqual(snapshot.executed_qty, Decimal("0"))
                finally:
                    if order_id is not None:
                        self.exchange.cancel_order(SYMBOL, order_id)

                canceled = wait_for_status(
                    self.exchange,
                    SYMBOL,
                    order_id,
                    OrderStatus.CANCELED,
                )
                self.assertEqual(canceled.status, OrderStatus.CANCELED)
                self.assertEqual(canceled.executed_qty, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
