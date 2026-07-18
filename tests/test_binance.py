from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import Mock

from gridtrader.binance import BinanceAPIError, BinanceFuturesExchange
from gridtrader.exchange import OrderNotFoundError


class BinanceFuturesExchangeTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
