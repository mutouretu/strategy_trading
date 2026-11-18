from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import Mock

import requests

from grid_server.binance import BinanceAPIError, BinanceFuturesExchange
from grid_server.infrastructure.binance import BinanceRateLimitError
from grid_server.domain import OrderSide, OrderStatus
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

    def test_self_trade_prevention_expiry_is_terminal_and_keeps_fill_quantity(self):
        payload = self.order_payload("EXPIRED_IN_MATCH")
        payload["executedQty"] = "0.25"
        payload["avgPrice"] = "100"

        order = BinanceFuturesExchange._order(payload)

        self.assertEqual(order.status, OrderStatus.EXPIRED)
        self.assertEqual(order.executed_qty, Decimal("0.25"))

    def test_http_success_body_with_code_200_is_accepted(self):
        exchange = BinanceFuturesExchange("key", "secret")
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"code": 200, "msg": "success"}
        exchange.session.request = Mock(return_value=response)

        result = exchange._request("POST", "/fapi/v1/positionSide/dual", signed=True)

        self.assertEqual(result, {"code": 200, "msg": "success"})

    def test_tradifi_price_band_is_read_from_exchange_info(self):
        exchange = BinanceFuturesExchange("key", "secret")
        exchange._request = Mock(return_value={
            "symbols": [{
                "symbol": "SOXLUSDT", "contractType": "TRADIFI_PERPETUAL",
                "baseAsset": "SOXL", "marginAsset": "USDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.01", "minQty": "0.01"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                    {"filterType": "PERCENT_PRICE", "multiplierUp": "1.03", "multiplierDown": "0.97"},
                ],
            }],
        })

        filters = exchange.get_symbol_filters("SOXLUSDT")

        self.assertEqual(filters.contract_type, "TRADIFI_PERPETUAL")
        self.assertEqual(filters.percent_price_up, Decimal("1.03"))
        self.assertEqual(filters.percent_price_down, Decimal("0.97"))

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

    def test_truncated_order_response_is_unknown_execution(self):
        exchange = BinanceFuturesExchange("key", "secret")
        exchange.session.request = Mock(
            side_effect=requests.exceptions.ChunkedEncodingError(
                "response ended prematurely"
            )
        )

        with self.assertRaises(ExchangeExecutionUnknownError):
            exchange._request("POST", "/fapi/v1/order", signed=True)

    def test_http_418_blocks_further_requests_until_ban_expires(self):
        exchange = BinanceFuturesExchange(
            "key", "secret", base_url="https://rate-limit-test.example"
        )
        response = Mock()
        response.status_code = 418
        response.text = '{"code":-1003,"msg":"IP banned until 4102444800000"}'
        response.headers = {"Retry-After": "60"}
        exchange.session.request = Mock(return_value=response)

        with self.assertRaises(BinanceRateLimitError) as first:
            exchange._request("GET", "/fapi/v1/openOrders", signed=True)
        self.assertGreaterEqual(first.exception.retry_at, 4102444800)
        with self.assertRaises(BinanceRateLimitError):
            exchange._request("GET", "/fapi/v1/premiumIndex")
        self.assertEqual(exchange.session.request.call_count, 1)

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

    def test_price_protected_catchup_uses_gtc_limit(self):
        exchange = BinanceFuturesExchange("key", "secret")
        exchange._request = Mock(return_value=self.order_payload())  # type: ignore[method-assign]

        exchange.place_limit_order(
            "SOXLUSDT", OrderSide.SELL, "LONG", Decimal("0.79"),
            Decimal("63.13"), "catchup-sell", post_only=False,
        )

        params = exchange._request.call_args.args[2]
        self.assertEqual(params["type"], "LIMIT")
        self.assertEqual(params["timeInForce"], "GTC")
        self.assertEqual(params["price"], "63.13")

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

    def test_position_prices_include_exchange_break_even_price(self):
        exchange = BinanceFuturesExchange("key", "secret")
        exchange._request = Mock(  # type: ignore[method-assign]
            return_value=[
                {
                    "symbol": "SOXLUSDT", "positionSide": "LONG", "positionAmt": "26.37",
                    "entryPrice": "62.43520", "breakEvenPrice": "62.55837",
                },
                {
                    "symbol": "SOXLUSDT", "positionSide": "SHORT", "positionAmt": "-2",
                    "entryPrice": "62.30", "breakEvenPrice": "62.20",
                },
            ]
        )

        self.assertEqual(
            exchange.get_position_prices("SOXLUSDT"),
            (Decimal("62.43520"), Decimal("62.55837")),
        )
        exchange._request.assert_called_once_with(
            "GET", "/fapi/v3/positionRisk", {"symbol": "SOXLUSDT"}, signed=True
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
