"""Run a small fixed-grid seed/top-up cycle on Binance USD-M testnet and clean up."""

from __future__ import annotations

import argparse
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

from grid_server.application.fixed_engine import FixedGridEngine
from grid_server.domain import FuturesMarket, GridKind, Mode, OrderSide, OrderStatus, StrategyConfig
from grid_server.domain.grid import build_cells, fixed_grid_ratio, round_down
from grid_server.infrastructure.binance import BinanceFuturesExchange
from grid_server.infrastructure.sqlite_store import SQLiteStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, required=True, help="testnet env file")
    parser.add_argument("--symbol", default="SOLUSDT")
    parser.add_argument("--budget", type=Decimal, default=Decimal("100"))
    parser.add_argument("--topup", type=Decimal, default=Decimal("20"))
    parser.add_argument("--execute", action="store_true", help="place and then clean up testnet orders")
    args = parser.parse_args()

    env = dotenv_values(args.env)
    url = str(env.get("BINANCE_BASE_URL") or "").rstrip("/")
    host = urlparse(url).hostname
    if host != "demo-fapi.binance.com":
        raise SystemExit("refusing to run: BINANCE_BASE_URL must be demo-fapi.binance.com")
    key = str(env.get("BINANCE_API_KEY") or "")
    secret = str(env.get("BINANCE_API_SECRET") or "")
    if not key or not secret:
        raise SystemExit("testnet API credentials are missing")
    exchange = BinanceFuturesExchange(key, secret, url)
    symbol = args.symbol.strip().upper()
    filters = exchange.get_symbol_filters(symbol)
    if filters.contract_type != "PERPETUAL":
        raise SystemExit("symbol is not a perpetual contract")
    mark = exchange.get_mark_price(symbol)
    lower = round_down(mark * Decimal("0.8"), filters.tick_size)
    upper = round_down(mark * Decimal("1.2"), filters.tick_size)
    strategy_id = f"verify-fixed-{uuid.uuid4().hex[:10]}"
    config = StrategyConfig(
        strategy_id=strategy_id,
        symbol=symbol,
        mode=Mode.LONG,
        anchor_price=upper,
        grid_ratio=fixed_grid_ratio(lower, upper, 4),
        grid_count=4,
        order_usdt=args.budget / 4,
        market_type=FuturesMarket.USDM,
        leverage=1,
        move_grid=False,
        grid_kind=GridKind.FIXED_EQUAL_USDT,
        lower_price=lower,
        upper_price=upper,
        grid_budget_usdt=args.budget,
    )
    cells = build_cells(config, filters.tick_size)
    print(f"target={host} symbol={symbol} mark={mark} range={lower}..{upper}")
    print(f"grid_count={len(cells)} per_cell_budget={cells[0].budget_usdt}")
    positions = [position for position in exchange.get_positions() if position.symbol == symbol]
    open_orders = exchange.get_open_orders(symbol)
    print(f"existing_positions={len(positions)} existing_open_orders={len(open_orders)}")
    if not args.execute:
        print("read-only preview complete; pass --execute for a small testnet cycle")
        return

    if positions:
        raise SystemExit("refusing to run: existing position on the symbol")
    if open_orders:
        raise SystemExit("refusing to run: existing orders on the symbol")
    if exchange.get_available_balance("USDT") < args.budget + args.topup:
        raise SystemExit("refusing to run: insufficient testnet USDT")

    with tempfile.TemporaryDirectory(prefix="fixed-grid-testnet-") as directory:
        store = SQLiteStore(Path(directory) / "grid.sqlite3")
        store.create_strategy(config)
        store.replace_cells(strategy_id, cells)
        store.mark_started(strategy_id)
        engine = FixedGridEngine(store, exchange, strategy_id)
        try:
            engine.tick()
            seed = store.get_capital_action(f"seed:{strategy_id}")
            if seed["status"] != "applied":
                raise RuntimeError(f"seed action is {seed['status']}")
            print(f"seed_filled_qty={seed['executed_qty']} open_orders={len(exchange.get_open_orders(symbol))}")

            topup_id = f"verify-topup-{uuid.uuid4().hex[:12]}"
            store.create_capital_action(topup_id, strategy_id, "topup", args.topup)
            engine.tick()
            topup = store.get_capital_action(topup_id)
            if topup["status"] != "applied":
                raise RuntimeError(f"top-up action is {topup['status']}")
            logical_qty = sum((cell.open_qty for cell in store.list_cells(strategy_id)), Decimal("0"))
            actual_qty = sum(
                (position.quantity for position in exchange.get_positions()
                 if position.symbol == symbol and position.position_side == "LONG"),
                Decimal("0"),
            )
            if abs(actual_qty - logical_qty) > filters.step_size:
                raise RuntimeError(f"position mismatch: actual={actual_qty}, logical={logical_qty}")
            print(
                f"topup_filled_qty={topup['executed_qty']} "
                f"actual_qty={actual_qty} logical_qty={logical_qty} "
                f"open_orders={len(exchange.get_open_orders(symbol))}"
            )
        finally:
            prefix = engine._client_prefix()
            for order in exchange.get_open_orders(symbol):
                if order.client_order_id.startswith(prefix):
                    exchange.cancel_order(symbol, order.order_id)
            actual_qty = sum(
                (position.quantity for position in exchange.get_positions()
                 if position.symbol == symbol and position.position_side == "LONG"),
                Decimal("0"),
            )
            if actual_qty > 0:
                cleanup_id = f"fg-clean-{uuid.uuid4().hex[:16]}"
                exchange.place_market_order(symbol, OrderSide.SELL, "LONG", actual_qty, cleanup_id)
                closed = exchange.get_order_by_client_id(symbol, cleanup_id)
                if closed.status != OrderStatus.FILLED:
                    raise RuntimeError(f"testnet cleanup close is {closed.status.value}")
            remaining = sum(
                (position.quantity for position in exchange.get_positions()
                 if position.symbol == symbol and position.position_side == "LONG"),
                Decimal("0"),
            )
            managed_orders = [
                order for order in exchange.get_open_orders(symbol)
                if order.client_order_id.startswith(prefix)
            ]
            print(f"cleanup_remaining_position={remaining} cleanup_managed_orders={len(managed_orders)}")
            if remaining != 0 or managed_orders:
                raise RuntimeError("testnet cleanup left a position or managed order")


if __name__ == "__main__":
    main()
