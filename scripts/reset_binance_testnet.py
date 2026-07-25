#!/usr/bin/env python3
"""Destructively reset the Binance USD-M testnet account and runtime database.

The command is dry-run by default and refuses every host except Binance Futures
Testnet. Database rows are deleted only after the platform has no open orders
and no non-zero positions.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from grid_server.infrastructure.binance import BinanceFuturesExchange, decimal_text
from grid_server.shared.config import (
    binance_base_url,
    binance_credentials,
    load_environment,
)


TESTNET_HOST = "testnet.binancefuture.com"


def raw_positions(exchange: BinanceFuturesExchange) -> list[dict]:
    rows = exchange._request("GET", "/fapi/v3/positionRisk", signed=True)
    return [
        row
        for row in rows
        if Decimal(str(row.get("positionAmt", "0"))) != 0
    ]


def close_side(position: dict) -> tuple[str, str, Decimal]:
    quantity = Decimal(str(position["positionAmt"]))
    position_side = str(position.get("positionSide", "BOTH")).upper()
    if position_side == "LONG":
        return "SELL", position_side, abs(quantity)
    if position_side == "SHORT":
        return "BUY", position_side, abs(quantity)
    return ("SELL" if quantity > 0 else "BUY"), "BOTH", abs(quantity)


def clear_database(db_path: Path) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        for table in (
            "cell_actions",
            "events",
            "runtime",
            "cells",
            "position_pools",
            "strategies",
        ):
            connection.execute(f"DELETE FROM {table}")
        connection.execute("DELETE FROM sqlite_sequence")
        connection.commit()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cancel every order, close every position and clear one testnet DB"
    )
    parser.add_argument("--env-file", type=Path, default=Path("test.env"))
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="perform the destructive reset; otherwise only print the plan",
    )
    args = parser.parse_args()

    load_environment(args.env_file, override=True)
    base_url = binance_base_url()
    host = (urlparse(base_url).hostname or "").lower()
    if host != TESTNET_HOST:
        raise RuntimeError(
            f"refusing destructive reset: expected {TESTNET_HOST}, got {host or 'empty host'}"
        )
    if not args.db.exists():
        raise FileNotFoundError(args.db)

    api_key, api_secret = binance_credentials(required=True)
    exchange = BinanceFuturesExchange(api_key, api_secret, base_url)
    orders_by_symbol = exchange.get_open_orders_by_symbol()
    positions = raw_positions(exchange)
    print(
        f"testnet_host={host} open_orders={sum(map(len, orders_by_symbol.values()))} "
        f"order_symbols={len(orders_by_symbol)} positions={len(positions)}"
    )
    for position in sorted(
        positions,
        key=lambda item: (str(item.get("symbol", "")), str(item.get("positionSide", ""))),
    ):
        side, position_side, quantity = close_side(position)
        print(
            f"plan_close symbol={position['symbol']} position_side={position_side} "
            f"side={side} quantity={decimal_text(quantity)}"
        )

    if not args.execute:
        print("dry_run=true platform_unchanged=true database_unchanged=true")
        return 0

    for symbol in sorted(orders_by_symbol):
        exchange._request(
            "DELETE",
            "/fapi/v1/allOpenOrders",
            {"symbol": symbol},
            signed=True,
        )
        print(f"orders_canceled symbol={symbol} count={len(orders_by_symbol[symbol])}")

    remaining_orders = exchange.get_open_orders_by_symbol()
    if remaining_orders:
        raise RuntimeError(
            "open orders remain after cancellation; database was not cleared: "
            + ", ".join(
                f"{symbol}={len(orders)}"
                for symbol, orders in sorted(remaining_orders.items())
            )
        )

    timestamp = int(time.time() * 1000)
    for index, position in enumerate(positions):
        symbol = str(position["symbol"])
        side, position_side, quantity = close_side(position)
        params = {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": "MARKET",
            "quantity": decimal_text(quantity),
            "newClientOrderId": f"gtrst-{symbol[:12]}-{index}-{timestamp % 100000000}",
            "newOrderRespType": "RESULT",
        }
        result = exchange._request(
            "POST",
            "/fapi/v1/order",
            params,
            signed=True,
        )
        print(
            f"position_closed symbol={symbol} position_side={position_side} "
            f"order_id={result.get('orderId')} status={result.get('status')} "
            f"executed_qty={result.get('executedQty')}"
        )

    remaining_positions = raw_positions(exchange)
    for _ in range(20):
        if not remaining_positions:
            break
        time.sleep(0.25)
        remaining_positions = raw_positions(exchange)
    if remaining_positions:
        details = ", ".join(
            f"{row.get('symbol')}:{row.get('positionSide')}={row.get('positionAmt')}"
            for row in remaining_positions
        )
        raise RuntimeError(
            f"positions remain after close; database was not cleared: {details}"
        )

    clear_database(args.db)
    print("platform_open_orders=0 platform_positions=0 database_rows_cleared=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
