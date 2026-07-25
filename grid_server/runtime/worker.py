from __future__ import annotations

import argparse
import os
import signal

from ..infrastructure.binance import BinanceFuturesExchange
from ..shared.config import binance_base_url, binance_credentials, load_environment
from ..domain import StrategyStatus
from ..application.engine import TradingEngine, run_loop
from ..infrastructure.sqlite_store import SQLiteStore


def main() -> int:
    load_environment()
    parser = argparse.ArgumentParser(description="Run one grid strategy worker")
    parser.add_argument("--db", required=True)
    parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--base-url", default=binance_base_url())
    args = parser.parse_args()

    api_key, api_secret = binance_credentials(required=True)

    stopping = False

    def request_stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    store = SQLiteStore(args.db)
    exchange = BinanceFuturesExchange(api_key, api_secret, args.base_url)
    engine = TradingEngine(store, exchange, args.strategy_id)
    try:
        run_loop(engine, lambda: stopping)
    finally:
        store.mark_runtime_stopped(args.strategy_id)
        store.set_status(args.strategy_id, StrategyStatus.STOPPED)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
