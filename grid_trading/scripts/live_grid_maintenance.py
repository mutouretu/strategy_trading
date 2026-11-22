#!/usr/bin/env python3
"""Check the two live grids' orders; let their running schedulers refill gaps."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from dotenv import dotenv_values


PROJECT = Path(__file__).resolve().parents[2]
GRID_PACKAGE = PROJECT / "grid_trading"
GRID_RUNTIME = GRID_PACKAGE / "runtime"
if str(GRID_PACKAGE) not in sys.path:
    sys.path.insert(0, str(GRID_PACKAGE))

from grid_server.application.reliability import append_jsonl, read_database  # noqa: E402
from grid_server.infrastructure.binance import BinanceFuturesExchange  # noqa: E402


@dataclass(frozen=True)
class Target:
    venue: str
    strategy_id: str
    db: Path
    env_file: Path


VENUE_FILES = (
    ("binance", GRID_RUNTIME / "binance/grid.sqlite3", GRID_PACKAGE / ".env"),
    ("okx", GRID_RUNTIME / "okx/grid.sqlite3", GRID_PACKAGE / "okx.env"),
)
RUNTIME = GRID_RUNTIME / "live-maintenance"
ACTIVE_STATUSES = {"starting", "running", "error"}


def active_targets() -> tuple[Target, ...]:
    """Patrol the strategies that currently need order maintenance."""
    targets = []
    for venue, db, env_file in VENUE_FILES:
        database = read_database(db)
        targets.extend(
            Target(venue, str(item["strategy_id"]), db, env_file)
            for item in database["strategies"]
            if item["status"] in ACTIVE_STATUSES
        )
    return tuple(targets)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def exchange_for(target: Target):
    values = dotenv_values(target.env_file)
    if target.venue == "binance":
        key, secret = values.get("BINANCE_API_KEY"), values.get("BINANCE_API_SECRET")
        base_url = str(values.get("BINANCE_BASE_URL") or "https://fapi.binance.com")
        if not key or not secret or base_url.rstrip("/") != "https://fapi.binance.com":
            raise RuntimeError("Binance live order read is not configured")
        return BinanceFuturesExchange(key, secret, base_url)
    key, secret = values.get("OKX_API_KEY"), values.get("OKX_API_SECRET")
    passphrase = values.get("OKX_API_PASSPHRASE")
    base_url = str(values.get("OKX_BASE_URL") or "https://openapi.okx.com")
    if (not key or not secret or not passphrase
            or str(values.get("OKX_SIMULATED") or "0") == "1"
            or base_url.rstrip("/") != "https://openapi.okx.com"):
        raise RuntimeError("OKX live order read is not configured")
    from grid_server.infrastructure.okx import OkxSwapExchange
    return OkxSwapExchange(key, secret, passphrase, base_url, simulated=False)


def order_issues(
    strategy_id: str, cells: list[dict], open_orders: list, mark: Decimal
) -> list[dict]:
    """Find clear order gaps without treating the limited window as a gap."""
    by_id = {order.order_id: order for order in open_orders}
    by_client = {order.client_order_id: order for order in open_orders
                 if order.client_order_id}
    tracked_ids: set[int] = set()
    tracked_clients: set[str] = set()
    issues: list[dict] = []
    crossed_entries: list[dict] = []
    crossed_exits: list[dict] = []

    for cell in cells:
        stage = cell["stage"]
        role = "entry" if stage == "pending_entry" else "exit" if stage == "pending_exit" else None
        if role is not None:
            order_id = cell[f"{role}_order_id"]
            client_id = str(cell.get(f"{role}_client_id") or "")
            if order_id is not None:
                tracked_ids.add(int(order_id))
            if client_id:
                tracked_clients.add(client_id)
            matched = by_id.get(order_id) if order_id is not None else None
            if matched is None and client_id:
                matched = by_client.get(client_id)
            if order_id is not None and matched is None:
                issues.append({
                    "kind": "recorded_order_missing", "cell_index": cell["cell_index"],
                    "role": role, "order_id": order_id,
                })
            if matched is not None:
                expected_side = "BUY" if role == "entry" else "SELL"
                if matched.side.value != expected_side or matched.position_side != "LONG":
                    issues.append({
                        "kind": "order_side_mismatch", "cell_index": cell["cell_index"],
                        "role": role, "order_id": matched.order_id,
                    })
            if order_id is None and not client_id:
                if role == "entry" and Decimal(cell["buy_price"]) >= mark:
                    crossed_entries.append(cell)
                if (role == "exit" and Decimal(cell["open_qty"]) > 0
                        and Decimal(cell["sell_price"]) <= mark):
                    crossed_exits.append(cell)
        if stage == "manual_review" and Decimal(cell["open_qty"]) > 0:
            issues.append({
                "kind": "held_cell_needs_order_review", "cell_index": cell["cell_index"],
            })

    # A crossed line should be handled immediately by the grid engine. Limit
    # this check to its ten-order window so distant logical cells stay virtual.
    for cell in sorted(crossed_entries,
                       key=lambda item: abs(Decimal(item["buy_price"]) - mark))[:10]:
        issues.append({"kind": "crossed_buy_unarmed", "cell_index": cell["cell_index"]})
    for cell in sorted(crossed_exits,
                       key=lambda item: abs(Decimal(item["sell_price"]) - mark))[:10]:
        issues.append({"kind": "crossed_sell_unarmed", "cell_index": cell["cell_index"]})

    prefix = f"wg-{hashlib.sha1(strategy_id.encode()).hexdigest()[:8]}-"
    for order in open_orders:
        if (order.client_order_id.startswith(prefix)
                and order.order_id not in tracked_ids
                and order.client_order_id not in tracked_clients):
            issues.append({"kind": "untracked_managed_order", "order_id": order.order_id})
    return sorted(issues, key=lambda item: (item["kind"], item.get("cell_index", 0),
                                           item.get("order_id", 0)))


def scan(target: Target, exchange) -> dict:
    database = read_database(target.db)
    strategy = next(
        (item for item in database["strategies"]
         if item["strategy_id"] == target.strategy_id),
        None,
    )
    if strategy is None:
        raise RuntimeError(f"{target.venue} strategy identity changed")
    symbol = strategy["symbol"]
    cells = [cell for cell in database["cells"] if cell["strategy_id"] == target.strategy_id]
    open_orders = exchange.get_open_orders(symbol)
    mark = exchange.get_mark_price(symbol)
    return {
        "checked_at": utc_now(), "venue": target.venue,
        "strategy_id": target.strategy_id, "symbol": symbol,
        "open_order_count": len(open_orders),
        "issues": order_issues(target.strategy_id, cells, open_orders, mark),
    }


def run_target(target: Target, *, recheck_delay: float) -> dict:
    try:
        exchange = exchange_for(target)
        first = scan(target, exchange)
        if not first["issues"]:
            return {**first, "resolved_after_sync": 0, "observations": 1}
        second = first
        observations = 1
        for multiplier in (1, 2, 3):
            time.sleep(recheck_delay * multiplier)
            second = scan(target, exchange)
            observations += 1
            if not second["issues"]:
                break
        first_keys = {json.dumps(item, sort_keys=True) for item in first["issues"]}
        second_keys = {json.dumps(item, sort_keys=True) for item in second["issues"]}
        return {**second, "resolved_after_sync": len(first_keys - second_keys),
                "observations": observations}
    except Exception as exc:
        return {"checked_at": utc_now(), "venue": target.venue,
                "strategy_id": target.strategy_id,
                "issues": [{"kind": "order_check_error", "detail": f"{type(exc).__name__}: {exc}"}],
                "resolved_after_sync": 0, "observations": 0}


def notify(venue: str, issues: list[dict]) -> None:
    message = f"{venue.upper()}: {len(issues)} 个订单问题待处理"
    script = f'display notification {json.dumps(message)} with title "网格订单巡检"'
    try:
        subprocess.run(["/usr/bin/osascript", "-e", script],
                       timeout=5, check=False, capture_output=True)
    except (FileNotFoundError, subprocess.SubprocessError):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--recheck-delay", type=float, default=10.0)
    args = parser.parse_args()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(RUNTIME / "maintenance.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(json.dumps({"status": "already_running"}))
        return 0
    results = [run_target(target, recheck_delay=args.recheck_delay)
               for target in active_targets()]
    state_path = RUNTIME / "last-issues.json"
    try:
        previous = json.loads(state_path.read_text())
    except (FileNotFoundError, ValueError):
        previous = {}
    current = {item["venue"]: item["issues"] for item in results}
    if not args.dry_run:
        for item in results:
            if item["issues"] and item["issues"] != previous.get(item["venue"]):
                notify(item["venue"], item["issues"])
            append_jsonl(RUNTIME / "checks.jsonl", item)
        state_path.write_text(json.dumps(current, ensure_ascii=False))
        os.chmod(state_path, 0o600)
    print(json.dumps({"checked": len(results), "dry_run": args.dry_run,
                      "results": results}, ensure_ascii=False))
    return 1 if any(item["issues"] for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
