"""OKX USDT-settled perpetual adapter for the grid exchange port.

The grid uses base-asset quantities. OKX's swap API uses contracts, so all
quantities crossing this boundary are converted with the instrument's ctVal.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlencode

import requests

from ..domain import FuturesMarket, OrderSide, OrderSnapshot, OrderStatus, PositionSnapshot, SymbolFilters
from ..ports.exchange import ExchangeExecutionUnknownError, OrderNotFoundError


class OkxAPIError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = str(code)
        super().__init__(f"OKX {self.code}: {message}")


class OkxRateLimitError(OkxAPIError):
    def __init__(self, retry_at: float) -> None:
        self.retry_at = retry_at
        super().__init__("50011", "rate limit")


def _number(value: Decimal) -> str:
    return format(value.normalize(), "f")


class OkxSwapExchange:
    market_type = FuturesMarket.USDM

    def __init__(
        self, api_key: str, api_secret: str, passphrase: str,
        base_url: str = "https://openapi.okx.com", *, simulated: bool = False,
        session: requests.Session | None = None,
        confirmation_delays: tuple[float, ...] = (0.0, 0.25, 1.0),
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.base_url = base_url.rstrip("/")
        self.simulated = simulated
        self.session = session or requests.Session()
        self.confirmation_delays = confirmation_delays
        self._filters: dict[str, SymbolFilters] = {}
        self._fee_groups: dict[str, str] = {}

    def _request(self, method: str, path: str, params: dict | None = None, body: dict | None = None, *, auth: bool = True) -> list[dict]:
        target = path + ("?" + urlencode(params) if params else "")
        payload = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = {"x-simulated-trading": "1" if self.simulated else "0", "Content-Type": "application/json"}
        if auth:
            timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            prehash = timestamp + method + target + payload
            signature = base64.b64encode(hmac.new(self.api_secret.encode(), prehash.encode(), hashlib.sha256).digest()).decode()
            headers.update({
                "OK-ACCESS-KEY": self.api_key,
                "OK-ACCESS-SIGN": signature,
                "OK-ACCESS-TIMESTAMP": timestamp,
                "OK-ACCESS-PASSPHRASE": self.passphrase,
            })
        try:
            response = self.session.request(method, self.base_url + target, headers=headers, data=payload or None, timeout=10)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if method == "POST":
                raise ExchangeExecutionUnknownError(f"OKX {method} {path}: result unknown") from exc
            raise OkxAPIError("transport", f"{method} {path}: {exc}") from exc
        try:
            result = response.json()
        except ValueError as exc:
            if method == "POST" or response.status_code >= 500:
                raise ExchangeExecutionUnknownError(f"OKX {method} {path}: invalid response") from exc
            raise OkxAPIError(str(response.status_code), "invalid JSON response") from exc
        code = str(result.get("code", ""))
        if response.status_code in {408, 429} or response.status_code >= 500:
            if response.status_code == 429 or code == "50011":
                raise OkxRateLimitError(time.time() + 2)
            raise ExchangeExecutionUnknownError(f"OKX {method} {path}: HTTP {response.status_code}, code {code}")
        if code != "0":
            if code == "50011":
                raise OkxRateLimitError(time.time() + 2)
            rejected = result.get("data", [])
            if isinstance(rejected, list) and rejected and str(rejected[0].get("sCode", "0")) != "0":
                raise OkxAPIError(str(rejected[0]["sCode"]), str(rejected[0].get("sMsg") or result.get("msg", "")))
            raise OkxAPIError(code or str(response.status_code), str(result.get("msg", "")))
        data = result.get("data", [])
        if not isinstance(data, list):
            raise OkxAPIError("response", "expected data array")
        if method == "POST" and data and str(data[0].get("sCode", "0")) != "0":
            raise OkxAPIError(str(data[0]["sCode"]), str(data[0].get("sMsg", "")))
        return data

    @staticmethod
    def _require_symbol(symbol: str) -> None:
        if not symbol.endswith("-USDT-SWAP"):
            raise ValueError("OKX grid supports USDT-settled perpetuals only")

    @staticmethod
    def _wire_client_id(client_order_id: str) -> str:
        # The shared grid uses hyphens for readable IDs; OKX accepts only
        # letters and digits. The mapping is deterministic for recovery.
        wire_id = client_order_id.replace("-", "")
        if not re.fullmatch(r"[A-Za-z0-9]{1,32}", wire_id):
            raise ValueError("OKX client order ID must be 1–32 letters or digits")
        return wire_id

    @staticmethod
    def _grid_client_id(wire_id: str) -> str:
        if re.fullmatch(r"fg[0-9a-f]{18}", wire_id):
            return f"fg-{wire_id[2:]}"
        if re.fullmatch(r"wg[0-9a-f]{16}[ex](?:[0-9a-f]{6})?", wire_id):
            return f"wg-{wire_id[2:10]}-{wire_id[10:18]}-{wire_id[18:]}"
        return wire_id

    def get_mark_price(self, symbol: str) -> Decimal:
        self._require_symbol(symbol)
        rows = self._request("GET", "/api/v5/public/mark-price", {"instType": "SWAP", "instId": symbol}, auth=False)
        if not rows:
            raise OkxAPIError("instrument", f"mark price missing for {symbol}")
        return Decimal(str(rows[0]["markPx"]))

    def get_price_limits(self, symbol: str) -> tuple[Decimal | None, Decimal | None]:
        """Return the exchange's current highest buy and lowest sell prices."""
        self._require_symbol(symbol)
        rows = self._request("GET", "/api/v5/public/price-limit", {"instId": symbol}, auth=False)
        if not rows or not rows[0].get("enabled"):
            return None, None
        return Decimal(str(rows[0]["buyLmt"])), Decimal(str(rows[0]["sellLmt"]))

    def get_symbol_filters(self, symbol: str) -> SymbolFilters:
        self._require_symbol(symbol)
        if symbol not in self._filters:
            rows = self._request("GET", "/api/v5/account/instruments", {"instType": "SWAP", "instId": symbol})
            if not rows or rows[0].get("state") != "live":
                raise OkxAPIError("instrument", f"{symbol} is not live for this account")
            row = rows[0]
            self._fee_groups[symbol] = str(row.get("groupId", ""))
            base = symbol.split("-", 1)[0]
            ct_val = Decimal(str(row["ctVal"]))
            if row.get("ctType") != "linear" or row.get("ctValCcy") != base or row.get("settleCcy") != "USDT" or ct_val <= 0:
                raise OkxAPIError("instrument", f"unsupported swap contract specification: {symbol}")
            self._filters[symbol] = SymbolFilters(
                tick_size=Decimal(str(row["tickSz"])),
                step_size=Decimal(str(row["lotSz"])) * ct_val,
                min_qty=Decimal(str(row["minSz"])) * ct_val,
                contract_size=ct_val,
                base_asset=base,
                margin_asset="USDT",
                contract_type="PERPETUAL",
            )
        return self._filters[symbol]

    def _contracts(self, symbol: str, quantity: Decimal) -> str:
        filters = self.get_symbol_filters(symbol)
        contracts = quantity / filters.contract_size
        lot = filters.step_size / filters.contract_size
        if quantity <= 0 or contracts < filters.min_qty / filters.contract_size or contracts % lot:
            raise ValueError(f"{symbol} quantity is not an allowed contract multiple")
        return _number(contracts)

    def set_hedge_mode(self, enabled: bool) -> None:
        rows = self._request("GET", "/api/v5/account/config")
        expected = "long_short_mode" if enabled else "net_mode"
        if not rows or rows[0].get("posMode") != expected:
            raise ValueError(f"OKX account position mode must already be {expected}")

    def set_leverage(self, symbol: str, leverage: int) -> None:
        self._require_symbol(symbol)
        if leverage != 1:
            raise ValueError("OKX fixed grid requires 1x leverage")
        rows = self._request("GET", "/api/v5/account/leverage-info", {"instId": symbol, "mgnMode": "cross"})
        if not any(row.get("posSide") == "long" and Decimal(str(row.get("lever", "0"))) == 1 for row in rows):
            raise ValueError("OKX cross long leverage must be set to 1x before starting")

    def _place(self, symbol: str, side: OrderSide, position_side: str, quantity: Decimal, client_order_id: str, *, order_type: str, price: Decimal | None = None) -> int:
        self._require_symbol(symbol)
        if position_side not in {"LONG", "SHORT"}:
            raise ValueError("OKX hedge position side must be LONG or SHORT")
        body = {
            "instId": symbol, "tdMode": "cross", "clOrdId": self._wire_client_id(client_order_id),
            "side": side.value.lower(), "posSide": position_side.lower(),
            "ordType": order_type, "sz": self._contracts(symbol, quantity),
        }
        if price is not None:
            body["px"] = _number(price)
        try:
            rows = self._request("POST", "/api/v5/trade/order", body=body)
        except ExchangeExecutionUnknownError as exc:
            for delay in self.confirmation_delays:
                if delay:
                    time.sleep(delay)
                try:
                    return self.get_order_by_client_id(symbol, client_order_id).order_id
                except (OrderNotFoundError, OkxAPIError):
                    continue
            raise exc
        if not rows or not rows[0].get("ordId"):
            raise ExchangeExecutionUnknownError("OKX place order: missing order id")
        return int(rows[0]["ordId"])

    def place_limit_order(self, symbol: str, side: OrderSide, position_side: str, quantity: Decimal, price: Decimal, client_order_id: str, post_only: bool = False) -> int:
        return self._place(symbol, side, position_side, quantity, client_order_id, order_type="post_only" if post_only else "limit", price=price)

    def place_market_order(self, symbol: str, side: OrderSide, position_side: str, quantity: Decimal, client_order_id: str) -> int:
        return self._place(symbol, side, position_side, quantity, client_order_id, order_type="market")

    def _order(self, symbol: str, row: dict) -> OrderSnapshot:
        status = {
            "live": OrderStatus.NEW, "partially_filled": OrderStatus.PARTIALLY_FILLED,
            "filled": OrderStatus.FILLED, "canceled": OrderStatus.CANCELED,
            "mmp_canceled": OrderStatus.CANCELED,
        }.get(str(row.get("state")))
        if status is None:
            raise OkxAPIError("order", f"unrecognized order state {row.get('state')}")
        factor = self.get_symbol_filters(symbol).contract_size
        return OrderSnapshot(
            order_id=int(row["ordId"]), client_order_id=self._grid_client_id(str(row.get("clOrdId", ""))),
            status=status, side=OrderSide(str(row["side"]).upper()),
            price=Decimal(str(row.get("px") or "0")),
            original_qty=Decimal(str(row.get("sz") or "0")) * factor,
            executed_qty=Decimal(str(row.get("accFillSz") or "0")) * factor,
            average_price=Decimal(str(row.get("avgPx") or "0")),
            position_side=str(row.get("posSide", "")).upper(),
        )

    def get_order(self, symbol: str, order_id: int) -> OrderSnapshot:
        self._require_symbol(symbol)
        try:
            rows = self._request("GET", "/api/v5/trade/order", {"instId": symbol, "ordId": str(order_id)})
        except OkxAPIError as exc:
            if exc.code == "51603":
                raise OrderNotFoundError(f"{symbol} order {order_id} not found") from exc
            raise
        if not rows:
            raise OrderNotFoundError(f"{symbol} order {order_id} not found")
        return self._order(symbol, rows[0])

    def get_order_by_client_id(self, symbol: str, client_order_id: str) -> OrderSnapshot:
        self._require_symbol(symbol)
        try:
            rows = self._request("GET", "/api/v5/trade/order", {"instId": symbol, "clOrdId": self._wire_client_id(client_order_id)})
        except OkxAPIError as exc:
            if exc.code == "51603":
                raise OrderNotFoundError(f"{symbol} client order {client_order_id} not found") from exc
            raise
        if not rows:
            raise OrderNotFoundError(f"{symbol} client order {client_order_id} not found")
        return self._order(symbol, rows[0])

    def get_open_orders(self, symbol: str) -> list[OrderSnapshot]:
        self._require_symbol(symbol)
        rows = self._pending_orders({"instType": "SWAP", "instId": symbol})
        return [self._order(symbol, row) for row in rows]

    def get_open_orders_by_symbol(self, symbols: set[str] | None = None) -> dict[str, list[OrderSnapshot]]:
        rows = self._pending_orders({"instType": "SWAP"})
        grouped: dict[str, list[OrderSnapshot]] = {}
        for row in rows:
            symbol = str(row.get("instId", ""))
            if not symbol.endswith("-USDT-SWAP") or (symbols is not None and symbol not in symbols):
                continue
            grouped.setdefault(symbol, []).append(self._order(symbol, row))
        return grouped

    def _pending_orders(self, filters: dict[str, str]) -> list[dict]:
        """Fetch every page so old open orders cannot disappear from reconciliation."""
        rows: list[dict] = []
        cursor = ""
        for _ in range(100):
            params = {**filters, "limit": "100"}
            if cursor:
                params["after"] = cursor
            page = self._request("GET", "/api/v5/trade/orders-pending", params)
            rows.extend(page)
            if len(page) < 100:
                return rows
            next_cursor = str(page[-1].get("ordId", ""))
            if not next_cursor or next_cursor == cursor:
                raise OkxAPIError("orders", "pending-order cursor did not advance")
            cursor = next_cursor
        raise OkxAPIError("orders", "pending-order scan exceeded 100 pages")

    def get_positions(self) -> list[PositionSnapshot]:
        rows = self._request("GET", "/api/v5/account/positions", {"instType": "SWAP"})
        result = []
        for row in rows:
            symbol = str(row.get("instId", ""))
            if not symbol.endswith("-USDT-SWAP"):
                continue
            contracts = Decimal(str(row.get("pos") or "0"))
            if contracts:
                result.append(PositionSnapshot(symbol, str(row.get("posSide", "")).upper(), abs(contracts) * self.get_symbol_filters(symbol).contract_size))
        return result

    def get_position_prices(self, symbol: str, position_side: str = "LONG") -> tuple[Decimal | None, Decimal | None]:
        self._require_symbol(symbol)
        rows = self._request("GET", "/api/v5/account/positions", {"instId": symbol})
        row = next((item for item in rows if str(item.get("posSide", "")).upper() == position_side and Decimal(str(item.get("pos") or "0")) != 0), None)
        if row is None:
            return None, None
        return Decimal(str(row["avgPx"])), Decimal(str(row["bePx"])) if row.get("bePx") else None

    def get_position_entry_price(self, symbol: str, position_side: str = "LONG") -> Decimal | None:
        return self.get_position_prices(symbol, position_side)[0]

    def get_available_balance(self, asset: str = "USDT") -> Decimal:
        if asset != "USDT":
            raise ValueError("OKX swap grid requires USDT balance")
        rows = self._request("GET", "/api/v5/account/balance", {"ccy": asset})
        details = rows[0].get("details", []) if rows else []
        row = next((item for item in details if item.get("ccy") == asset), None)
        if row is None:
            raise OkxAPIError("balance", "USDT trading balance unavailable")
        value = row.get("availBal") or row.get("availEq")
        if value in (None, ""):
            raise OkxAPIError("balance", "USDT available balance unavailable")
        return Decimal(str(value))

    def get_commission_rates(self, symbol: str) -> tuple[Decimal, Decimal]:
        self._require_symbol(symbol)
        self.get_symbol_filters(symbol)
        rows = self._request("GET", "/api/v5/account/trade-fee", {
            "instType": "SWAP", "instFamily": symbol.removesuffix("-SWAP"),
        })
        if not rows:
            raise OkxAPIError("fees", f"fee rate missing for {symbol}")
        group = next((item for item in rows[0].get("feeGroup", [])
                      if str(item.get("groupId")) == self._fee_groups[symbol]), None)
        if group is None:
            raise OkxAPIError("fees", f"fee group missing for {symbol}")
        return abs(Decimal(str(group["maker"]))), abs(Decimal(str(group["taker"])))

    def cancel_order(self, symbol: str, order_id: int) -> OrderSnapshot:
        self._require_symbol(symbol)
        try:
            self._request("POST", "/api/v5/trade/cancel-order", body={"instId": symbol, "ordId": str(order_id)})
        except (ExchangeExecutionUnknownError, OkxAPIError):
            # The exchange may have accepted a cancellation before replying.
            pass
        for delay in self.confirmation_delays:
            if delay:
                time.sleep(delay)
            try:
                order = self.get_order(symbol, order_id)
            except (OrderNotFoundError, OkxAPIError):
                continue
            if order.status not in (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED):
                return order
        raise ExchangeExecutionUnknownError(f"OKX cancellation of {symbol} order {order_id} is unconfirmed")

    def get_user_trades(self, symbol: str, *, start_time_ms: int | None = None, end_time_ms: int | None = None, from_id: int | None = None, limit: int = 1000) -> list[dict]:
        self._require_symbol(symbol)
        if not 1 <= limit <= 1000 or (from_id is not None and (start_time_ms is not None or end_time_ms is not None)):
            raise ValueError("invalid OKX trade history pagination")
        params: dict[str, str] = {"instType": "SWAP", "instId": symbol, "limit": "100"}
        if start_time_ms is not None:
            params["begin"] = str(start_time_ms)
        if end_time_ms is not None:
            params["end"] = str(end_time_ms)
        collected: dict[int, dict] = {}
        cursor = ""
        while True:
            if cursor:
                params["after"] = cursor
            rows = self._request("GET", "/api/v5/trade/fills-history", params)
            if not rows:
                break
            for row in rows:
                trade_id = int(row["tradeId"])
                if (from_id is None or trade_id >= from_id) and (start_time_ms is None or int(row["fillTime"]) >= start_time_ms) and (end_time_ms is None or int(row["fillTime"]) <= end_time_ms):
                    factor = self.get_symbol_filters(symbol).contract_size
                    collected[trade_id] = {
                        "id": trade_id, "orderId": row["ordId"],
                        "side": str(row["side"]).upper(),
                        "qty": _number(Decimal(str(row["fillSz"])) * factor),
                        "price": row["fillPx"], "realizedPnl": row.get("fillPnl") or "0",
                        "commission": _number(-Decimal(str(row.get("fee") or "0"))),
                        "commissionAsset": row.get("feeCcy") or "USDT",
                        "time": row["fillTime"],
                    }
            if len(collected) > limit:
                raise OkxAPIError("history", "more fills than one safe sync window can hold")
            if len(rows) < 100:
                break
            next_cursor = str(rows[-1]["billId"])
            if next_cursor == cursor:
                raise OkxAPIError("history", "fill cursor did not advance")
            cursor = next_cursor
            if from_id is not None and all(int(row["tradeId"]) < from_id for row in rows):
                break
        return [collected[key] for key in sorted(collected)]
