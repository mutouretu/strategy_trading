#!/usr/bin/env python3
"""Create and start a guarded COIN-M long-grid batch through the local API."""

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
from gridtrader.infrastructure.binance import BinanceCoinMExchange
from gridtrader.shared.config import (
    api_base_url,
    binance_coinm_base_url,
    binance_credentials,
    load_environment,
)


ALLOWED_COINM_TEST_HOSTS = {"testnet.binancefuture.com", "demo-dapi.binance.com"}
ALLOWED_LOCAL_API_HOSTS = {"127.0.0.1", "localhost", "::1"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path("test.env"))
    parser.add_argument("--api-url")
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--target-contracts", type=int, default=2)
    parser.add_argument("--grid-ratio", type=Decimal, default=Decimal("0.02"))
    parser.add_argument("--grid-count", type=int, default=5)
    parser.add_argument("--leverage", type=int, default=3)
    parser.add_argument("--poll-interval-sec", type=float, default=50.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    if args.target_contracts < 1:
        raise ValueError("target-contracts must be at least 1")

    load_environment(args.env_file, override=True)
    exchange_url = binance_coinm_base_url()
    exchange_host = (urlparse(exchange_url).hostname or "").lower()
    if exchange_host not in ALLOWED_COINM_TEST_HOSTS:
        raise RuntimeError(f"refusing COIN-M batch outside test environment: {exchange_host}")

    local_api = (args.api_url or api_base_url()).rstrip("/")
    api_host = (urlparse(local_api).hostname or "").lower()
    if api_host not in ALLOWED_LOCAL_API_HOSTS:
        raise RuntimeError(f"refusing non-local Grid API: {api_host}")

    api_key, api_secret = binance_credentials(required=True)
    exchange = BinanceCoinMExchange(api_key, api_secret, exchange_url)
    session = requests.Session()
    existing_response = session.get(f"{local_api}/strategies", timeout=10)
    existing_response.raise_for_status()
    existing_symbols = {
        str(item["symbol"]).upper()
        for item in existing_response.json()
        if item.get("market_type") == "coinm" and not item.get("archived")
    }

    symbols = list(dict.fromkeys(symbol.strip().upper() for symbol in args.symbol))
    duplicates = sorted(set(symbols) & existing_symbols)
    if duplicates:
        raise RuntimeError("active COIN-M strategies already exist: " + ", ".join(duplicates))

    plans: list[dict] = []
    for symbol in symbols:
        mark = exchange.get_mark_price(symbol)
        filters = exchange.get_symbol_filters(symbol)
        if filters.contract_type != "PERPETUAL" or filters.contract_size <= 0:
            raise RuntimeError(f"unsupported COIN-M perpetual contract: {symbol}")
        anchor = round_down(mark, filters.tick_size)
        order_coin_qty = (
            Decimal(args.target_contracts) * filters.contract_size / anchor
        )
        config = {
            "symbol": symbol,
            "market_type": "coinm",
            "mode": "long",
            "anchor_price": str(anchor),
            "grid_ratio": str(args.grid_ratio),
            "grid_count": args.grid_count,
            "order_coin_qty": str(order_coin_qty),
            "leverage": args.leverage,
            "poll_interval_sec": args.poll_interval_sec,
            "move_grid": True,
        }
        preview_response = session.post(
            f"{local_api}/strategies/preview", json=config, timeout=20
        )
        preview_response.raise_for_status()
        preview = preview_response.json()
        plans.append(
            {
                "symbol": symbol,
                "mark_at_create": str(mark),
                "target_contracts": args.target_contracts,
                "contract_size": str(filters.contract_size),
                "config": config,
                "preview_range": {
                    "lower": preview["config"]["lower_price"],
                    "upper": preview["config"]["upper_price"],
                },
            }
        )

    print(
        f"coinm_test_host={exchange_host} api={local_api} "
        f"planned_strategies={len(plans)} execute={args.execute}"
    )
    for plan in plans:
        print(
            f"plan symbol={plan['symbol']} mark={plan['mark_at_create']} "
            f"range={plan['preview_range']['lower']}..{plan['preview_range']['upper']} "
            f"target_contracts={plan['target_contracts']}"
        )
    if not args.execute:
        return 0

    created: list[dict] = []
    for plan in plans:
        response = session.post(f"{local_api}/strategies", json=plan["config"], timeout=20)
        response.raise_for_status()
        strategy = response.json()
        strategy_id = str(strategy["strategy_id"])
        started = session.post(f"{local_api}/strategies/{strategy_id}/start", timeout=30)
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

    if args.output:
        output = {
            "batch": args.output.stem,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "environment": "Binance COIN-M Demo/Testnet",
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
