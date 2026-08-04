from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import Mock

from grid_server.binance import BinanceAPIError, BinanceFuturesExchange
from grid_server.domain import OrderSide
from grid_server.exchange import ExchangeExecutionUnknownError, OrderNotFoundError


class BinanceFuturesExchangeTests(unittest.TestCase):
    @staticmethod
    def order_payload(status: str = "NEW") -> dict:
        return {
            "symbol": "BTCUSDT",
            "orderId": 123,
            "clientOrderId": "client-123",
            "status": status,
            "side": "BUY",
            "price": "100",
            "origQty": "1",
            "executedQty": "0",
            "positionSide": "LONG",
        }

    def test_http_success_body_with_code_200_is_accepted(self):
        exchange = BinanceFuturesExchange("key", "secret")
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"code": 200, "msg": "success"}
        exchange.session.request = Mock(return_value=response)

        result = exchange._request("POST", "/fapi/v1/positionSide/dual", signed=True)

        self.assertEqual(result, {"code": 200, "msg": "success"})

    def test_query_order_no_such_order_is_mapped_to_consistency_signal(self):
        exchange = BinanceFuturesExchange("key", "secret")
        exchange._request = Mock(  # type: ignore[method-assign]
            side_effect=BinanceAPIError(
                'HTTP 400 /fapi/v1/order: {"code":-2013,"msg":"Order does not exist."}'
            )
        )

        with self.assertRaises(OrderNotFoundError):
            exchange.get_order("BTCUSDT", 123)

    def test_http_408_is_classified_as_unknown_execution(self):
        exchange = BinanceFuturesExchange(
            "key",
            "secret",
            confirmation_delays=(0.0,),
        )
        response = Mock()
        response.status_code = 408
        response.text = '{"code":-1007,"msg":"execution status unknown"}'
        exchange.session.request = Mock(return_value=response)

        with self.assertRaises(ExchangeExecutionUnknownError):
            exchange._request("POST", "/fapi/v1/order", signed=True)

    def test_place_timeout_confirms_order_by_client_id_without_duplicate(self):
        exchange = BinanceFuturesExchange(
            "key",
            "secret",
            confirmation_delays=(0.0,),
        )
        exchange._request = Mock(  # type: ignore[method-assign]
            side_effect=[
                ExchangeExecutionUnknownError("response lost"),
                self.order_payload(),
            ]
        )

        order_id = exchange.place_limit_order(
            "BTCUSDT",
            OrderSide.BUY,
            "LONG",
            Decimal("1"),
            Decimal("100"),
            "client-123",
        )

        self.assertEqual(order_id, 123)
        self.assertEqual(exchange._request.call_count, 2)

    def test_cancel_timeout_accepts_later_canceled_status(self):
        exchange = BinanceFuturesExchange(
            "key",
            "secret",
            confirmation_delays=(0.0,),
        )
        exchange._request = Mock(  # type: ignore[method-assign]
            side_effect=[
                ExchangeExecutionUnknownError("response lost"),
                self.order_payload("CANCELED"),
            ]
        )

        canceled = exchange.cancel_order("BTCUSDT", 123)

        self.assertEqual(canceled.status.value, "CANCELED")
        self.assertEqual(exchange._request.call_count, 2)

    def test_cancel_unknown_order_accepts_queryable_canceled_status(self):
        exchange = BinanceFuturesExchange(
            "key",
            "secret",
            confirmation_delays=(0.0,),
        )
        exchange._request = Mock(  # type: ignore[method-assign]
            side_effect=[
                BinanceAPIError(
                    'HTTP 400 /fapi/v1/order: {"code":-2011,"msg":"Unknown order sent."}'
                ),
                self.order_payload("CANCELED"),
            ]
        )

        canceled = exchange.cancel_order("BTCUSDT", 123)

        self.assertEqual(canceled.status.value, "CANCELED")

    def test_position_risk_is_normalized_to_positive_resource_quantities(self):
        exchange = BinanceFuturesExchange("key", "secret")
        exchange._request = Mock(  # type: ignore[method-assign]
            return_value=[
                {"symbol": "BTCUSDT", "positionSide": "LONG", "positionAmt": "0.25"},
                {"symbol": "BTCUSDT", "positionSide": "SHORT", "positionAmt": "-0.10"},
                {"symbol": "ETHUSDT", "positionSide": "LONG", "positionAmt": "0"},
            ]
        )

        positions = exchange.get_positions()
        self.assertEqual(
            [(item.symbol, item.position_side, item.quantity) for item in positions],
            [
                ("BTCUSDT", "LONG", Decimal("0.25")),
                ("BTCUSDT", "SHORT", Decimal("0.10")),
            ],
        )

    def test_all_open_orders_are_fetched_once_and_grouped_by_symbol(self):
        exchange = BinanceFuturesExchange("key", "secret")
        exchange._request = Mock(  # type: ignore[method-assign]
            return_value=[
                {
                    "symbol": "BTCUSDT",
                    "orderId": 1,
                    "clientOrderId": "btc-1",
                    "status": "NEW",
                    "side": "BUY",
                    "price": "100",
                    "origQty": "1",
                    "positionSide": "LONG",
                },
                {
                    "symbol": "ETHUSDT",
                    "orderId": 2,
                    "clientOrderId": "eth-1",
                    "status": "NEW",
                    "side": "SELL",
                    "price": "10",
                    "origQty": "2",
                    "positionSide": "SHORT",
                },
            ]
        )

        grouped = exchange.get_open_orders_by_symbol({"BTCUSDT"})

        self.assertEqual(list(grouped), ["BTCUSDT"])
        self.assertEqual(grouped["BTCUSDT"][0].order_id, 1)
        exchange._request.assert_called_once_with(
            "GET", "/fapi/v1/openOrders", signed=True
        )


if __name__ == "__main__":
    unittest.main()
