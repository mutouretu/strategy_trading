from __future__ import annotations

import hashlib
import hmac
import re
import threading
import time
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import requests

from ..domain import (
    FuturesMarket,
    OrderSide,
    OrderSnapshot,
    OrderStatus,
    PositionSnapshot,
    SymbolFilters,
)
from ..ports.exchange import ExchangeExecutionUnknownError, OrderNotFoundError


class BinanceAPIError(RuntimeError):
    pass


class BinanceRateLimitError(BinanceAPIError):
    def __init__(self, retry_at: float) -> None:
        self.retry_at = retry_at
        super().__init__(f"Binance rate limit active until {int(retry_at * 1000)}")


def decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


class BinanceFuturesExchange:
    _rate_limit_until: dict[str, float] = {}
    _rate_limit_lock = threading.Lock()

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://fapi.binance.com",
        *,
        market_type: FuturesMarket = FuturesMarket.USDM,
        confirmation_delays: tuple[float, ...] = (0.0, 0.25, 1.0),
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.market_type = FuturesMarket(market_type)
        self.confirmation_delays = confirmation_delays
        self.session = requests.Session()
        self.session.headers.update({"X-MBX-APIKEY": api_key})

    @property
    def api_prefix(self) -> str:
        return "dapi" if self.market_type == FuturesMarket.COINM else "fapi"

    def _path(self, suffix: str, *, version: str = "v1") -> str:
        return f"/{self.api_prefix}/{version}/{suffix.lstrip('/')}"

    def _request(self, method: str, path: str, params: dict[str, str] | None = None, signed: bool = False) -> Any:
        with self._rate_limit_lock:
            retry_at = self._rate_limit_until.get(self.base_url, 0.0)
        if time.time() < retry_at:
            raise BinanceRateLimitError(retry_at)
        payload = dict(params or {})
        if signed:
            payload["timestamp"] = str(int(time.time() * 1000))
            query = urlencode(payload)
            payload["signature"] = hmac.new(
                self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
            ).hexdigest()
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                params=payload,
                timeout=10,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise ExchangeExecutionUnknownError(
                f"{method} {path}: transport result unknown: {exc}"
            ) from exc
        if response.status_code in {418, 429}:
            retry_after = response.headers.get("Retry-After", "")
            try:
                delay = max(0.0, float(retry_after))
            except ValueError:
                delay = 120.0 if response.status_code == 418 else 60.0
            ban_match = re.search(r"banned until (\d{13})", response.text)
            retry_at = max(
                time.time() + delay,
                int(ban_match.group(1)) / 1000 if ban_match else 0.0,
            )
            with self._rate_limit_lock:
                self._rate_limit_until[self.base_url] = max(
                    retry_at, self._rate_limit_until.get(self.base_url, 0.0)
                )
                retry_at = self._rate_limit_until[self.base_url]
            raise BinanceRateLimitError(retry_at)
        if response.status_code >= 400:
            if response.status_code == 408 or response.status_code >= 500:
                raise ExchangeExecutionUnknownError(
                    f"HTTP {response.status_code} {path}: {response.text}"
                )
            raise BinanceAPIError(f"HTTP {response.status_code} {path}: {response.text}")
        data = response.json()
        if isinstance(data, dict) and data.get("code", 0) not in (0, 200, None):
            if int(data.get("code", 0)) in {-1006, -1007}:
                raise ExchangeExecutionUnknownError(f"Binance {path}: {data}")
            raise BinanceAPIError(f"Binance {path}: {data}")
        return data

    def get_mark_price(self, symbol: str) -> Decimal:
        data = self._request("GET", self._path("premiumIndex"), {"symbol": symbol})
        if isinstance(data, list):
            data = next(
                (item for item in data if item.get("symbol") == symbol),
                None,
            )
            if data is None:
                raise BinanceAPIError(f"mark price not found: {symbol}")
        return Decimal(str(data["markPrice"]))

    def get_symbol_filters(self, symbol: str) -> SymbolFilters:
        data = self._request("GET", self._path("exchangeInfo"))
        entry = next((item for item in data.get("symbols", []) if item.get("symbol") == symbol), None)
        if entry is None:
            raise BinanceAPIError(f"symbol not found: {symbol}")
        values = {item["filterType"]: item for item in entry.get("filters", [])}
        price_filter = values.get("PRICE_FILTER", {})
        lot_filter = values.get("LOT_SIZE", {})
        notional_filter = values.get("MIN_NOTIONAL", values.get("NOTIONAL", {}))
        percent_price_filter = values.get("PERCENT_PRICE", {})
        return SymbolFilters(
            tick_size=Decimal(str(price_filter.get("tickSize", "0"))),
            step_size=Decimal(str(lot_filter.get("stepSize", "0"))),
            min_qty=Decimal(str(lot_filter.get("minQty", "0"))),
            min_notional=Decimal(str(notional_filter.get("notional", notional_filter.get("minNotional", "0")))),
            contract_size=Decimal(str(entry.get("contractSize", "0"))),
            base_asset=str(entry.get("baseAsset", "")),
            margin_asset=str(entry.get("marginAsset", "")),
            contract_type=str(entry.get("contractType", "")),
            percent_price_up=Decimal(str(percent_price_filter.get("multiplierUp", "0"))),
            percent_price_down=Decimal(str(percent_price_filter.get("multiplierDown", "0"))),
        )

    def set_hedge_mode(self, enabled: bool) -> None:
        try:
            self._request(
                "POST",
                self._path("positionSide/dual"),
                {"dualSidePosition": "true" if enabled else "false"},
                signed=True,
            )
        except BinanceAPIError as exc:
            if "-4059" not in str(exc):
                raise

    def set_leverage(self, symbol: str, leverage: int) -> None:
        self._request(
            "POST",
            self._path("leverage"),
            {"symbol": symbol, "leverage": str(leverage)},
            signed=True,
        )

    def place_limit_order(
        self,
        symbol: str,
        side: OrderSide,
        position_side: str,
        quantity: Decimal,
        price: Decimal,
        client_order_id: str,
        post_only: bool = False,
    ) -> int:
        try:
            data = self._request(
                "POST",
                self._path("order"),
                {
                    "symbol": symbol,
                    "side": side.value,
                    "positionSide": position_side,
                    "type": "LIMIT",
                    "timeInForce": "GTX" if post_only else "GTC",
                    "quantity": decimal_text(quantity),
                    "price": decimal_text(price),
                    "newClientOrderId": client_order_id,
                },
                signed=True,
            )
        except ExchangeExecutionUnknownError as exc:
            return self._confirm_order_by_client_id(
                symbol,
                client_order_id,
                exc,
            ).order_id
        return int(data["orderId"])

    def place_market_order(
        self,
        symbol: str,
        side: OrderSide,
        position_side: str,
        quantity: Decimal,
        client_order_id: str,
    ) -> int:
        if self.market_type != FuturesMarket.USDM:
            raise ValueError("aggregate market orders currently support USD-M only")
        try:
            data = self._request(
                "POST",
                self._path("order"),
                {
                    "symbol": symbol,
                    "side": side.value,
                    "positionSide": position_side,
                    "type": "MARKET",
                    "quantity": decimal_text(quantity),
                    "newClientOrderId": client_order_id,
                },
                signed=True,
            )
        except ExchangeExecutionUnknownError as exc:
            return self._confirm_order_by_client_id(symbol, client_order_id, exc).order_id
        return int(data["orderId"])

    def get_available_balance(self, asset: str = "USDT") -> Decimal:
        if self.market_type != FuturesMarket.USDM:
            raise ValueError("available USDT balance currently supports USD-M only")
        data = self._request("GET", self._path("balance", version="v3"), signed=True)
        row = next((item for item in data if item.get("asset") == asset), None)
        if row is None:
            raise BinanceAPIError(f"balance asset not found: {asset}")
        return Decimal(str(row["availableBalance"]))

    def get_commission_rates(self, symbol: str) -> tuple[Decimal, Decimal]:
        if self.market_type != FuturesMarket.USDM:
            raise ValueError("commission rates currently support USD-M only")
        data = self._request(
            "GET", self._path("commissionRate"), {"symbol": symbol}, signed=True
        )
        return (
            Decimal(str(data["makerCommissionRate"])),
            Decimal(str(data["takerCommissionRate"])),
        )

    def get_user_trades(
        self,
        symbol: str,
        *,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        from_id: int | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        if self.market_type != FuturesMarket.USDM:
            raise ValueError("trade history currently supports USD-M only")
        if from_id is not None and (start_time_ms is not None or end_time_ms is not None):
            raise ValueError("fromId cannot be combined with startTime or endTime")
        if not 1 <= limit <= 1000:
            raise ValueError("trade history limit must be between 1 and 1000")
        params = {"symbol": symbol, "limit": str(limit)}
        if start_time_ms is not None:
            params["startTime"] = str(start_time_ms)
        if end_time_ms is not None:
            params["endTime"] = str(end_time_ms)
        if from_id is not None:
            params["fromId"] = str(from_id)
        return self._request("GET", self._path("userTrades"), params, signed=True)

    def get_order(self, symbol: str, order_id: int) -> OrderSnapshot:
        return self._query_order(
            {"symbol": symbol, "orderId": str(order_id)},
            f"{symbol} order {order_id}",
        )

    def get_order_by_client_id(
        self,
        symbol: str,
        client_order_id: str,
    ) -> OrderSnapshot:
        return self._query_order(
            {"symbol": symbol, "origClientOrderId": client_order_id},
            f"{symbol} client order {client_order_id}",
        )

    def _query_order(
        self,
        params: dict[str, str],
        description: str,
    ) -> OrderSnapshot:
        try:
            data = self._request("GET", self._path("order"), params, signed=True)
        except BinanceAPIError as exc:
            message = str(exc)
            if (
                "-2013" in message
                or "Order does not exist" in message
                or "-2011" in message
                or "Unknown order" in message
            ):
                raise OrderNotFoundError(f"{description} not found") from exc
            raise
        return self._order(data)

    def get_open_orders(self, symbol: str) -> list[OrderSnapshot]:
        data = self._request(
            "GET", self._path("openOrders"), {"symbol": symbol}, signed=True
        )
        return [self._order(item) for item in data]

    def get_open_orders_by_symbol(
        self, symbols: set[str] | None = None
    ) -> dict[str, list[OrderSnapshot]]:
        """Fetch every open order with one signed GET and group it by symbol."""

        data = self._request("GET", self._path("openOrders"), signed=True)
        grouped: dict[str, list[OrderSnapshot]] = {}
        for item in data:
            symbol = str(item.get("symbol", ""))
            if not symbol or (symbols is not None and symbol not in symbols):
                continue
            grouped.setdefault(symbol, []).append(self._order(item))
        return grouped

    def get_positions(self) -> list[PositionSnapshot]:
        version = "v1" if self.market_type == FuturesMarket.COINM else "v3"
        data = self._request("GET", self._path("positionRisk", version=version), signed=True)
        return [
            PositionSnapshot(
                symbol=str(item["symbol"]),
                position_side=str(item.get("positionSide", "BOTH")),
                quantity=abs(Decimal(str(item.get("positionAmt", "0")))),
            )
            for item in data
            if Decimal(str(item.get("positionAmt", "0"))) != 0
        ]

    def get_position_prices(
        self, symbol: str, position_side: str = "LONG"
    ) -> tuple[Decimal | None, Decimal | None]:
        version = "v1" if self.market_type == FuturesMarket.COINM else "v3"
        data = self._request(
            "GET", self._path("positionRisk", version=version),
            {"symbol": symbol}, signed=True,
        )
        row = next(
            (
                item for item in data
                if (
                    item.get("positionSide") == position_side
                    and Decimal(str(item.get("positionAmt", "0"))) != 0
                ) or (
                    item.get("positionSide") == "BOTH"
                    and (
                        Decimal(str(item.get("positionAmt", "0"))) > 0
                        if position_side == "LONG" else
                        Decimal(str(item.get("positionAmt", "0"))) < 0
                    )
                )
            ),
            None,
        )
        if row is None:
            return None, None
        break_even_price = row.get("breakEvenPrice")
        return (
            Decimal(str(row.get("entryPrice", "0"))),
            None if break_even_price is None else Decimal(str(break_even_price)),
        )

    def get_position_entry_price(self, symbol: str, position_side: str = "LONG") -> Decimal | None:
        return self.get_position_prices(symbol, position_side)[0]

    def cancel_order(self, symbol: str, order_id: int) -> OrderSnapshot:
        try:
            data = self._request(
                "DELETE",
                self._path("order"),
                {"symbol": symbol, "orderId": str(order_id)},
                signed=True,
            )
            return self._order(data)
        except (ExchangeExecutionUnknownError, BinanceAPIError) as exc:
            if isinstance(exc, BinanceAPIError) and not self._is_unknown_order(exc):
                raise
            return self._confirm_cancellation(symbol, order_id, exc)

    def _confirm_order_by_client_id(
        self,
        symbol: str,
        client_order_id: str,
        original_error: Exception,
    ) -> OrderSnapshot:
        for delay in self.confirmation_delays:
            if delay > 0:
                time.sleep(delay)
            try:
                return self.get_order_by_client_id(symbol, client_order_id)
            except (OrderNotFoundError, ExchangeExecutionUnknownError):
                continue
        raise original_error

    def _confirm_cancellation(
        self,
        symbol: str,
        order_id: int,
        original_error: Exception,
    ) -> OrderSnapshot:
        last_snapshot: OrderSnapshot | None = None
        for delay in self.confirmation_delays:
            if delay > 0:
                time.sleep(delay)
            try:
                last_snapshot = self.get_order(symbol, order_id)
            except (OrderNotFoundError, ExchangeExecutionUnknownError):
                continue
            if last_snapshot.status not in {
                OrderStatus.NEW,
                OrderStatus.PARTIALLY_FILLED,
            }:
                return last_snapshot
        raise original_error

    @staticmethod
    def _is_unknown_order(exc: Exception) -> bool:
        message = str(exc)
        return "-2011" in message or "Unknown order" in message

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


class BinanceCoinMExchange(BinanceFuturesExchange):
    """COIN-M adapter using DAPI and contract-count quantities."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://dapi.binance.com",
        *,
        confirmation_delays: tuple[float, ...] = (0.0, 0.25, 1.0),
    ) -> None:
        super().__init__(
            api_key,
            api_secret,
            base_url,
            market_type=FuturesMarket.COINM,
            confirmation_delays=confirmation_delays,
        )
