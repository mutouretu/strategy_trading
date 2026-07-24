#!/usr/bin/env python3
"""Create and start a guarded short-grid batch through the local Web API."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gridtrader.domain.grid import round_down
from gridtrader.infrastructure.binance import BinanceFuturesExchange
from gridtrader.shared.config import (
    binance_base_url,
    binance_credentials,
    load_environment,
)


TESTNET_HOST = "testnet.binancefuture.com"


def source_symbols(paths: list[Path]) -> list[str]:
    symbols: set[str] = set()
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        symbols.update(str(item["symbol"]).upper() for item in payload["items"])
    return sorted(symbols)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create one five-cell short grid per symbol")
    parser.add_argument("--env-file", type=Path, default=Path("test.env"))
    parser.add_argument("--api-url", default="http://127.0.0.1:8110")
    parser.add_argument("--source-manifest", type=Path, action="append", default=[])
    parser.add_argument("--symbol", action="append", default=[])
    parser.add_argument(
        "--allow-existing",
        action="store_true",
        help="add to a non-empty runtime database instead of requiring a clean reset",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--grid-ratio", type=Decimal, default=Decimal("0.01"))
    parser.add_argument("--grid-count", type=int, default=5)
    parser.add_argument("--order-usdt", type=Decimal, default=Decimal("10"))
    parser.add_argument("--leverage", type=int, default=3)
    parser.add_argument("--poll-interval-sec", type=float, default=50.0)
    parser.add_argument("--anchor-steps-below", type=int, default=3)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    load_environment(args.env_file, override=True)
    base_url = binance_base_url()
    host = (urlparse(base_url).hostname or "").lower()
    if host != TESTNET_HOST:
        raise RuntimeError(f"refusing batch start outside {TESTNET_HOST}: {host}")
    api_key, api_secret = binance_credentials(required=True)
    exchange = BinanceFuturesExchange(api_key, api_secret, base_url)

    symbols = sorted(
        set(source_symbols(args.source_manifest))
        | {str(symbol).strip().upper() for symbol in args.symbol if str(symbol).strip()}
    )
    if not symbols:
        raise RuntimeError("provide at least one source manifest or --symbol")
    if (
        requests.get(f"{args.api_url.rstrip('/')}/strategies", timeout=10).json()
        and not args.allow_existing
    ):
        raise RuntimeError("local runtime database is not empty")

    growth = Decimal("1") + args.grid_ratio
    plans = []
    for symbol in symbols:
        mark = exchange.get_mark_price(symbol)
        filters = exchange.get_symbol_filters(symbol)
        anchor = round_down(
            mark / (growth ** args.anchor_steps_below),
            filters.tick_size,
        )
        plans.append(
            {
                "symbol": symbol,
                "mark_at_create": str(mark),
                "config": {
                    "symbol": symbol,
                    "mode": "short",
                    "anchor_price": str(anchor),
                    "grid_ratio": str(args.grid_ratio),
                    "grid_count": args.grid_count,
                    "order_usdt": str(args.order_usdt),
                    "leverage": args.leverage,
                    "poll_interval_sec": args.poll_interval_sec,
                    "move_grid": True,
                },
            }
        )
    print(f"testnet_host={host} planned_strategies={len(plans)}")
    if not args.execute:
        print("dry_run=true")
        return 0

    session = requests.Session()
    created = []
    for plan in plans:
        response = session.post(
            f"{args.api_url.rstrip('/')}/strategies",
            json=plan["config"],
            timeout=20,
        )
        response.raise_for_status()
        strategy = response.json()
        strategy_id = str(strategy["strategy_id"])
        started = session.post(
            f"{args.api_url.rstrip('/')}/strategies/{strategy_id}/start",
            timeout=20,
        )
        started.raise_for_status()
        item = {
            **plan,
            "strategy_id": strategy_id,
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "scheduler_pid": int(started.json()["pid"]),
        }
        created.append(item)
        print(
            f"started symbol={plan['symbol']} strategy_id={strategy_id} "
            f"scheduler_pid={item['scheduler_pid']}"
        )
        time.sleep(0.1)

    output = {
        "batch": args.output.stem,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "environment": "Binance USD-M Testnet",
        "source_manifests": [str(path) for path in args.source_manifest],
        "items": created,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"manifest={args.output} created={len(created)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
