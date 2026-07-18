from __future__ import annotations

import hashlib
import hmac
import time
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import requests

from .domain import OrderSide, OrderSnapshot, OrderStatus, PositionSnapshot, SymbolFilters
from .exchange import OrderNotFoundError


class BinanceAPIError(RuntimeError):
    pass


def decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


class BinanceFuturesExchange:
    def __init__(self, api_key: str, api_secret: str, base_url: str = "https://fapi.binance.com") -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"X-MBX-APIKEY": api_key})

    def _request(self, method: str, path: str, params: dict[str, str] | None = None, signed: bool = False) -> Any:
        payload = dict(params or {})
        if signed:
            payload["timestamp"] = str(int(time.time() * 1000))
            query = urlencode(payload)
            payload["signature"] = hmac.new(
                self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
            ).hexdigest()
        response = self.session.request(method, f"{self.base_url}{path}", params=payload, timeout=10)
        if response.status_code >= 400:
            raise BinanceAPIError(f"HTTP {response.status_code} {path}: {response.text}")
        data = response.json()
        if isinstance(data, dict) and data.get("code", 0) not in (0, 200, None):
            raise BinanceAPIError(f"Binance {path}: {data}")
        return data

    def get_mark_price(self, symbol: str) -> Decimal:
        data = self._request("GET", "/fapi/v1/premiumIndex", {"symbol": symbol})
        return Decimal(str(data["markPrice"]))

    def get_symbol_filters(self, symbol: str) -> SymbolFilters:
        data = self._request("GET", "/fapi/v1/exchangeInfo")
        entry = next((item for item in data.get("symbols", []) if item.get("symbol") == symbol), None)
        if entry is None:
            raise BinanceAPIError(f"symbol not found: {symbol}")
        values = {item["filterType"]: item for item in entry.get("filters", [])}
        price_filter = values.get("PRICE_FILTER", {})
        lot_filter = values.get("LOT_SIZE", {})
        notional_filter = values.get("MIN_NOTIONAL", values.get("NOTIONAL", {}))
        return SymbolFilters(
            tick_size=Decimal(str(price_filter.get("tickSize", "0"))),
            step_size=Decimal(str(lot_filter.get("stepSize", "0"))),
            min_qty=Decimal(str(lot_filter.get("minQty", "0"))),
            min_notional=Decimal(str(notional_filter.get("notional", notional_filter.get("minNotional", "0")))),
        )

    def set_hedge_mode(self, enabled: bool) -> None:
        try:
            self._request(
                "POST",
                "/fapi/v1/positionSide/dual",
                {"dualSidePosition": "true" if enabled else "false"},
                signed=True,
            )
        except BinanceAPIError as exc:
            if "-4059" not in str(exc):
                raise

    def set_leverage(self, symbol: str, leverage: int) -> None:
        self._request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": str(leverage)}, signed=True)

    def place_limit_order(
        self,
        symbol: str,
        side: OrderSide,
        position_side: str,
        quantity: Decimal,
        price: Decimal,
        client_order_id: str,
    ) -> int:
        data = self._request(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": symbol,
                "side": side.value,
                "positionSide": position_side,
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": decimal_text(quantity),
                "price": decimal_text(price),
                "newClientOrderId": client_order_id,
            },
            signed=True,
        )
        return int(data["orderId"])

    def get_order(self, symbol: str, order_id: int) -> OrderSnapshot:
        try:
            data = self._request(
                "GET", "/fapi/v1/order", {"symbol": symbol, "orderId": str(order_id)}, signed=True
            )
        except BinanceAPIError as exc:
            message = str(exc)
            if (
                "-2013" in message
                or "Order does not exist" in message
                or "-2011" in message
                or "Unknown order" in message
            ):
                raise OrderNotFoundError(f"{symbol} order {order_id} not found") from exc
            raise
        return self._order(data)

    def get_open_orders(self, symbol: str) -> list[OrderSnapshot]:
        data = self._request("GET", "/fapi/v1/openOrders", {"symbol": symbol}, signed=True)
        return [self._order(item) for item in data]

    def get_positions(self) -> list[PositionSnapshot]:
        data = self._request("GET", "/fapi/v3/positionRisk", signed=True)
        return [
            PositionSnapshot(
                symbol=str(item["symbol"]),
                position_side=str(item.get("positionSide", "BOTH")),
                quantity=abs(Decimal(str(item.get("positionAmt", "0")))),
            )
            for item in data
            if Decimal(str(item.get("positionAmt", "0"))) != 0
        ]

    def cancel_order(self, symbol: str, order_id: int) -> OrderSnapshot:
        data = self._request(
            "DELETE",
            "/fapi/v1/order",
            {"symbol": symbol, "orderId": str(order_id)},
            signed=True,
        )
        return self._order(data)

    @staticmethod
    def _order(data: dict) -> OrderSnapshot:
        return OrderSnapshot(
            order_id=int(data["orderId"]),
            client_order_id=str(data.get("clientOrderId", "")),
            status=OrderStatus(str(data["status"])),
            side=OrderSide(str(data["side"])),
            price=Decimal(str(data.get("price", "0"))),
            original_qty=Decimal(str(data.get("origQty", "0"))),
            executed_qty=Decimal(str(data.get("executedQty", "0"))),
            average_price=Decimal(str(data.get("avgPrice", "0"))),
            position_side=str(data.get("positionSide", "")),
        )
