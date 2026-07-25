#!/usr/bin/env python3
"""Safely smoke-test one Binance USD-M futures market order.

The script is dry-run by default.  With ``--real-order`` it opens a small
hedge-mode LONG or SHORT position and immediately closes only the quantity
created by the test.  It refuses to run when the symbol already has a position
on that side or open orders so cleanup cannot interfere with another strategy.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from grid_server.binance import BinanceAPIError, BinanceFuturesExchange, decimal_text
from grid_server.config import binance_base_url, binance_credentials, load_environment


TERMINAL_ORDER_STATUSES = {"FILLED", "CANCELED", "EXPIRED", "REJECTED"}


def round_step(value: Decimal, step: Decimal, rounding: str) -> Decimal:
    if step <= 0:
        raise ValueError("invalid quantity step size")
    return (value / step).to_integral_value(rounding=rounding) * step


def symbol_info(client: BinanceFuturesExchange, symbol: str) -> dict[str, Any]:
    data = client._request("GET", "/fapi/v1/exchangeInfo")
    entry = next((item for item in data.get("symbols", []) if item.get("symbol") == symbol), None)
    if entry is None:
        raise ValueError(f"symbol not found: {symbol}")
    if entry.get("status") != "TRADING":
        raise ValueError(f"symbol is not trading: {symbol} ({entry.get('status')})")
    return entry


def market_quantity(info: dict[str, Any], mark_price: Decimal, target_notional: Decimal) -> Decimal:
    filters = {item.get("filterType"): item for item in info.get("filters", [])}
    lot = filters.get("MARKET_LOT_SIZE") or filters.get("LOT_SIZE") or {}
    notional_filter = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}
    step = Decimal(str(lot.get("stepSize", "0")))
    minimum_qty = Decimal(str(lot.get("minQty", "0")))
    minimum_notional = Decimal(
        str(notional_filter.get("notional", notional_filter.get("minNotional", "0")))
    )
    required_notional = max(target_notional, minimum_notional)
    quantity = round_step(required_notional / mark_price, step, ROUND_UP)
    return max(quantity, minimum_qty)


def quantity_step(info: dict[str, Any]) -> Decimal:
    filters = {item.get("filterType"): item for item in info.get("filters", [])}
    lot = filters.get("MARKET_LOT_SIZE") or filters.get("LOT_SIZE") or {}
    return Decimal(str(lot.get("stepSize", "0")))


def position_quantity(
    client: BinanceFuturesExchange,
    symbol: str,
    position_side: str,
) -> Decimal:
    rows = client._request("GET", "/fapi/v3/positionRisk", {"symbol": symbol}, signed=True)
    row = next((item for item in rows if item.get("positionSide") == position_side), None)
    if row is None:
        return Decimal("0")
    return abs(Decimal(str(row.get("positionAmt", "0"))))


def wait_for_order(
    client: BinanceFuturesExchange,
    symbol: str,
    client_order_id: str,
    attempts: int = 20,
) -> dict[str, Any]:
    last: dict[str, Any] | None = None
    for _ in range(attempts):
        last = client._request(
            "GET",
            "/fapi/v1/order",
            {"symbol": symbol, "origClientOrderId": client_order_id},
            signed=True,
        )
        if str(last.get("status")) in TERMINAL_ORDER_STATUSES:
            return last
        time.sleep(0.25)
    if last is None:
        raise RuntimeError(f"order query returned no result: {client_order_id}")
    return last


def place_market(
    client: BinanceFuturesExchange,
    symbol: str,
    side: str,
    position_side: str,
    quantity: Decimal,
    client_order_id: str,
) -> dict[str, Any]:
    return client._request(
        "POST",
        "/fapi/v1/order",
        {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": "MARKET",
            "quantity": decimal_text(quantity),
            "newClientOrderId": client_order_id,
            "newOrderRespType": "RESULT",
        },
        signed=True,
    )


def order_trades(
    client: BinanceFuturesExchange,
    symbol: str,
    order_id: int,
) -> list[dict[str, Any]]:
    for _ in range(10):
        trades = client._request(
            "GET",
            "/fapi/v1/userTrades",
            {"symbol": symbol, "orderId": str(order_id)},
            signed=True,
        )
        if trades:
            return trades
        time.sleep(0.25)
    return []


def trade_summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
    quantity = sum((Decimal(str(item.get("qty", "0"))) for item in trades), Decimal("0"))
    quote_quantity = sum(
        (
            Decimal(str(item.get("price", "0"))) * Decimal(str(item.get("qty", "0")))
            for item in trades
        ),
        Decimal("0"),
    )
    commissions: dict[str, Decimal] = {}
    for item in trades:
        asset = str(item.get("commissionAsset", "UNKNOWN"))
        commissions[asset] = commissions.get(asset, Decimal("0")) + Decimal(
            str(item.get("commission", "0"))
        )
    return {
        "fills": len(trades),
        "quantity": quantity,
        "average_price": quote_quantity / quantity if quantity > 0 else Decimal("0"),
        "realized_pnl": sum(
            (Decimal(str(item.get("realizedPnl", "0"))) for item in trades),
            Decimal("0"),
        ),
        "commissions": commissions,
    }


def commission_text(commissions: dict[str, Decimal]) -> str:
    return ",".join(
        f"{asset}:{decimal_text(value)}" for asset, value in sorted(commissions.items())
    ) or "none"


def main() -> int:
    parser = argparse.ArgumentParser(description="Binance USD-M real-order smoke test")
    parser.add_argument("--symbol", default="1000LUNCUSDT")
    parser.add_argument("--notional", type=Decimal, default=Decimal("10"))
    parser.add_argument("--direction", choices=("LONG", "SHORT"), default="SHORT")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--real-order", action="store_true")
    args = parser.parse_args()

    load_environment(args.env_file)
    api_key, api_secret = binance_credentials(required=True)

    symbol = args.symbol.strip().upper()
    position_side = args.direction
    open_side = "BUY" if position_side == "LONG" else "SELL"
    close_side = "SELL" if position_side == "LONG" else "BUY"
    client = BinanceFuturesExchange(
        api_key,
        api_secret,
        binance_base_url(),
    )

    mode = client._request("GET", "/fapi/v1/positionSide/dual", signed=True)
    if str(mode.get("dualSidePosition")).lower() != "true":
        raise RuntimeError("this smoke test requires Binance hedge mode")

    info = symbol_info(client, symbol)
    mark_price = client.get_mark_price(symbol)
    quantity = market_quantity(info, mark_price, args.notional)
    step = quantity_step(info)
    estimated_notional = quantity * mark_price
    print(
        f"prepared symbol={symbol} side={position_side} quantity={decimal_text(quantity)} "
        f"estimated_notional={decimal_text(estimated_notional)}"
    )

    initial_position = position_quantity(client, symbol, position_side)
    open_orders = client.get_open_orders(symbol)
    if initial_position != 0 or open_orders:
        raise RuntimeError(
            f"refusing smoke test: existing_{position_side.lower()}_position="
            f"{decimal_text(initial_position)}, "
            f"open_orders={len(open_orders)}"
        )

    test_tag = f"{position_side[0].lower()}{int(time.time() * 1000)}"
    test_id = f"gt-smoke-test-{test_tag}"
    client._request(
        "POST",
        "/fapi/v1/order/test",
        {
            "symbol": symbol,
            "side": open_side,
            "positionSide": position_side,
            "type": "MARKET",
            "quantity": decimal_text(quantity),
            "newClientOrderId": test_id,
        },
        signed=True,
    )
    print("order_test=ok")
    if not args.real_order:
        print("real_order=skipped")
        return 0

    opened_quantity = Decimal("0")
    open_id = f"gt-smoke-open-{test_tag}"
    close_id = f"gt-smoke-close-{test_tag}"
    open_order_id: int | None = None
    close_order_id: int | None = None
    try:
        open_order = place_market(
            client, symbol, open_side, position_side, quantity, open_id
        )
        if str(open_order.get("status")) not in TERMINAL_ORDER_STATUSES:
            open_order = wait_for_order(client, symbol, open_id)
        open_order_id = int(open_order["orderId"])
        opened_quantity = Decimal(str(open_order.get("executedQty", "0")))
        if opened_quantity <= 0:
            raise RuntimeError(f"open order did not execute: status={open_order.get('status')}")
        print(
            f"open_order_id={open_order.get('orderId')} status={open_order.get('status')} "
            f"executed_qty={decimal_text(opened_quantity)}"
        )

        close_order = place_market(
            client, symbol, close_side, position_side, opened_quantity, close_id
        )
        if str(close_order.get("status")) not in TERMINAL_ORDER_STATUSES:
            close_order = wait_for_order(client, symbol, close_id)
        close_order_id = int(close_order["orderId"])
        print(
            f"close_order_id={close_order.get('orderId')} status={close_order.get('status')} "
            f"executed_qty={close_order.get('executedQty')}"
        )
    finally:
        remaining = position_quantity(client, symbol, position_side)
        cleanup_quantity = round_step(remaining, step, ROUND_DOWN) if remaining > 0 else Decimal("0")
        if cleanup_quantity > 0:
            cleanup_id = f"gt-smoke-clean-{test_tag}"
            print(f"cleanup_required={decimal_text(cleanup_quantity)}")
            cleanup = place_market(
                client, symbol, close_side, position_side, cleanup_quantity, cleanup_id
            )
            if str(cleanup.get("status")) not in TERMINAL_ORDER_STATUSES:
                cleanup = wait_for_order(client, symbol, cleanup_id)
            print(f"cleanup_order_id={cleanup.get('orderId')} status={cleanup.get('status')}")

    final_position = position_quantity(client, symbol, position_side)
    if final_position != initial_position:
        raise RuntimeError(
            f"position not restored: before={decimal_text(initial_position)}, "
            f"after={decimal_text(final_position)}"
        )
    print("position_restored=true")

    if open_order_id is None or close_order_id is None:
        raise RuntimeError("missing order id after completed real-order test")
    open_summary = trade_summary(order_trades(client, symbol, open_order_id))
    close_summary = trade_summary(order_trades(client, symbol, close_order_id))
    all_commissions = dict(open_summary["commissions"])
    for asset, value in close_summary["commissions"].items():
        all_commissions[asset] = all_commissions.get(asset, Decimal("0")) + value
    realized_pnl = open_summary["realized_pnl"] + close_summary["realized_pnl"]
    print(
        f"open_fills={open_summary['fills']} average_price="
        f"{decimal_text(open_summary['average_price'])} "
        f"commission={commission_text(open_summary['commissions'])}"
    )
    print(
        f"close_fills={close_summary['fills']} average_price="
        f"{decimal_text(close_summary['average_price'])} "
        f"commission={commission_text(close_summary['commissions'])}"
    )
    print(
        f"realized_pnl={decimal_text(realized_pnl)} "
        f"total_commission={commission_text(all_commissions)}"
    )
    if set(all_commissions) <= {"USDT"}:
        print(
            f"net_after_commission_usdt="
            f"{decimal_text(realized_pnl - all_commissions.get('USDT', Decimal('0')))}"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BinanceAPIError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(2)
