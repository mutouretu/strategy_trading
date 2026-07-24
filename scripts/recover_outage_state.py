#!/usr/bin/env python3
"""Reconcile a paused SQLite database after an exchange API outage.

The command is dry-run by default.  It never cancels an order or opens a new
position.  With ``--apply`` it may place only protective exit orders for entry
orders that Binance already reports as executed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gridtrader.application.engine import TradingEngine
from gridtrader.domain import CellStage, GridCell, OrderStatus, StrategyStatus
from gridtrader.infrastructure.binance import BinanceFuturesExchange
from gridtrader.infrastructure.sqlite_store import SQLiteStore, utc_now
from gridtrader.shared.config import (
    binance_base_url,
    binance_credentials,
    load_environment,
)


ACTIVE_STATUSES = {
    StrategyStatus.STARTING,
    StrategyStatus.RUNNING,
    StrategyStatus.ERROR,
}
OPEN_STATUSES = {OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED}


@dataclass(frozen=True)
class RecoveryAction:
    kind: str
    strategy_id: str
    symbol: str
    cell_id: str
    cell_index: int
    role: str
    order_id: int
    status: str
    executed_qty: str
    logical_delta: str
    client_order_id: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def stable_prefix(strategy_id: str) -> str:
    tag = hashlib.sha1(strategy_id.encode("utf-8")).hexdigest()[:8]
    return f"wg-{tag}-"


def submission_identity(
    strategy_id: str,
    cell: GridCell,
    role: str,
    client_order_id: str,
) -> bool:
    prefix = stable_prefix(strategy_id)
    if not client_order_id.startswith(prefix):
        return False
    tail = client_order_id[len(prefix):]
    token = f"{cell.cell_id[:8]}-{role}"
    if tail == token:
        return True
    return (
        len(tail) == len(token) + 6
        and tail.startswith(token)
        and all(character in "0123456789abcdef" for character in tail[len(token):].lower())
    )


def historical_entry_ids(db_path: Path) -> dict[tuple[str, str], list[int]]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT strategy_id, cell_id,
                   CAST(json_extract(payload_json, '$.order_id') AS INTEGER) AS order_id
            FROM events
            WHERE event_type='ENTRY_MISSING'
              AND json_extract(payload_json, '$.order_id') IS NOT NULL
            ORDER BY id DESC
            """
        ).fetchall()
    finally:
        connection.close()
    result: dict[tuple[str, str], list[int]] = {}
    for row in rows:
        key = (str(row["strategy_id"]), str(row["cell_id"]))
        order_id = int(row["order_id"])
        if order_id not in result.setdefault(key, []):
            result[key].append(order_id)
    return result


def main() -> int:
    args = parse_args()
    load_environment(args.env_file, override=True)
    db_path = Path(args.db).expanduser().resolve()
    store = SQLiteStore(db_path)
    configs = {
        config.strategy_id: config
        for config in store.list_strategies(include_archived=True)
    }
    active = [
        config.strategy_id
        for config in configs.values()
        if not config.archived and config.status in ACTIVE_STATUSES
    ]
    if active:
        raise RuntimeError(
            "recovery requires every strategy to be paused; active="
            + ",".join(sorted(active))
        )

    api_key, api_secret = binance_credentials(required=True)
    exchange = BinanceFuturesExchange(api_key, api_secret, binance_base_url())
    cells = store.list_all_cells()
    cells_by_key = {(cell.strategy_id, cell.cell_id): cell for cell in cells}
    engines = {
        strategy_id: TradingEngine(
            store,
            exchange,
            strategy_id,
            run_id="outage-recovery",
        )
        for strategy_id in configs
    }
    for strategy_id, engine in engines.items():
        engine.filters = exchange.get_symbol_filters(configs[strategy_id].symbol)
        engine.initialized = True

    symbols = {config.symbol for config in configs.values() if not config.archived}
    grouped_open = exchange.get_open_orders_by_symbol(symbols)
    open_by_id = {
        order.order_id: order
        for orders in grouped_open.values()
        for order in orders
    }

    prefix_to_strategy: dict[str, str] = {}
    for strategy_id in configs:
        prefix = stable_prefix(strategy_id)
        if prefix in prefix_to_strategy:
            raise RuntimeError(f"strategy client prefix collision: {prefix}")
        prefix_to_strategy[prefix] = strategy_id

    actions: list[RecoveryAction] = []
    terminal_keys: set[tuple[str, str, str, int]] = set()

    for symbol, orders in grouped_open.items():
        for order in orders:
            strategy_id = next(
                (
                    candidate
                    for prefix, candidate in prefix_to_strategy.items()
                    if order.client_order_id.startswith(prefix)
                ),
                None,
            )
            if strategy_id is None:
                if order.client_order_id.startswith("wg-"):
                    raise RuntimeError(
                        f"managed open order has no strategy: {symbol} {order.order_id}"
                    )
                continue
            matches = [
                (cell, role)
                for cell in cells
                if cell.strategy_id == strategy_id
                for role in ("e", "x")
                if submission_identity(strategy_id, cell, role, order.client_order_id)
            ]
            if len(matches) != 1:
                raise RuntimeError(
                    f"ambiguous managed order identity: {symbol} {order.order_id} "
                    f"matches={len(matches)}"
                )
            cell, role_code = matches[0]
            role = "entry" if role_code == "e" else "exit"
            mismatches = engines[strategy_id]._recovery_mismatches(cell, role, order)
            if mismatches:
                raise RuntimeError(
                    f"order attributes mismatch: {symbol} {order.order_id}: {mismatches}"
                )
            current_id = cell.entry_order_id if role == "entry" else cell.exit_order_id
            current_client_id = cell.entry_client_id if role == "entry" else cell.exit_client_id
            expected_stage = (
                CellStage.PENDING_ENTRY if role == "entry" else CellStage.PENDING_EXIT
            )
            if (
                current_id != order.order_id
                or current_client_id != order.client_order_id
                or cell.stage != expected_stage
            ):
                actions.append(
                    RecoveryAction(
                        "recover_open_reference",
                        strategy_id,
                        symbol,
                        cell.cell_id,
                        cell.index,
                        role,
                        order.order_id,
                        order.status.value,
                        str(order.executed_qty),
                        "0",
                        order.client_order_id,
                    )
                )

    candidate_refs: list[tuple[GridCell, str, int]] = []
    for cell in cells:
        if cell.entry_order_id is not None:
            candidate_refs.append((cell, "entry", cell.entry_order_id))
        if cell.exit_order_id is not None:
            candidate_refs.append((cell, "exit", cell.exit_order_id))
    history = historical_entry_ids(db_path)
    for (strategy_id, cell_id), order_ids in history.items():
        cell = cells_by_key.get((strategy_id, cell_id))
        if cell is None or cell.open_qty > 0:
            continue
        for order_id in order_ids:
            candidate_refs.append((cell, "entry", order_id))

    seen_refs: set[tuple[str, str, str, int]] = set()
    for cell, role, order_id in candidate_refs:
        key = (cell.strategy_id, cell.cell_id, role, order_id)
        if key in seen_refs or order_id in open_by_id:
            continue
        seen_refs.add(key)
        config = configs[cell.strategy_id]
        order = exchange.get_order(config.symbol, order_id)
        if order.status in OPEN_STATUSES:
            raise RuntimeError(
                f"order snapshot race: {config.symbol} {order_id} is open but absent from openOrders"
            )
        if not submission_identity(
            cell.strategy_id,
            cell,
            "e" if role == "entry" else "x",
            order.client_order_id,
        ):
            raise RuntimeError(
                f"terminal order does not identify the Cell: {config.symbol} {order_id}"
            )
        mismatches = engines[cell.strategy_id]._recovery_mismatches(cell, role, order)
        if mismatches:
            raise RuntimeError(
                f"terminal order attributes mismatch: {config.symbol} {order_id}: {mismatches}"
            )
        if role == "entry":
            if cell.open_qty > 0:
                continue
            delta = order.executed_qty
        else:
            if cell.open_qty <= 0:
                continue
            delta = -min(
                cell.open_qty,
                max(Decimal("0"), order.executed_qty - cell.exit_executed_qty),
            )
        # A zero-fill ended entry still needs to leave manual review so the
        # normal scheduler can safely re-arm it after restart.
        if delta == 0 and not (
            role == "entry" and cell.stage == CellStage.MANUAL_REVIEW
        ):
            continue
        terminal_keys.add(key)
        actions.append(
            RecoveryAction(
                "settle_terminal_order",
                cell.strategy_id,
                config.symbol,
                cell.cell_id,
                cell.index,
                role,
                order.order_id,
                order.status.value,
                str(order.executed_qty),
                str(delta),
                order.client_order_id,
            )
        )

    positions: dict[tuple[str, str], Decimal] = {}
    for position in exchange.get_positions():
        key = (position.symbol, position.position_side)
        positions[key] = positions.get(key, Decimal("0")) + abs(position.quantity)
    logical: dict[tuple[str, str], Decimal] = {}
    for cell in cells:
        config = configs[cell.strategy_id]
        side = "LONG" if config.mode.value == "long" else "SHORT"
        key = (config.symbol, side)
        logical[key] = logical.get(key, Decimal("0")) + cell.open_qty
    for action in actions:
        if action.kind != "settle_terminal_order":
            continue
        config = configs[action.strategy_id]
        side = "LONG" if config.mode.value == "long" else "SHORT"
        key = (config.symbol, side)
        logical[key] = logical.get(key, Decimal("0")) + Decimal(action.logical_delta)

    mismatched_pools = []
    for key in sorted(set(positions) | set(logical)):
        actual = positions.get(key, Decimal("0"))
        projected = logical.get(key, Decimal("0"))
        if actual != projected:
            mismatched_pools.append(
                {"symbol": key[0], "side": key[1], "actual": str(actual), "projected": str(projected)}
            )
    report = {
        "mode": "apply" if args.apply else "dry_run",
        "base_url": binance_base_url(),
        "database": str(db_path),
        "strategy_count": len(configs),
        "open_reference_recoveries": sum(
            action.kind == "recover_open_reference" for action in actions
        ),
        "terminal_settlements": sum(
            action.kind == "settle_terminal_order" for action in actions
        ),
        "mismatched_pools": mismatched_pools,
        "actions": [asdict(action) for action in actions],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if mismatched_pools:
        raise RuntimeError("projected logical positions do not equal live positions")
    if not args.apply:
        return 0

    for action in actions:
        if action.kind != "recover_open_reference":
            continue
        cell = cells_by_key[(action.strategy_id, action.cell_id)]
        if action.role == "entry":
            cell.stage = CellStage.PENDING_ENTRY
            cell.entry_order_id = action.order_id
            cell.entry_client_id = action.client_order_id
        else:
            cell.stage = CellStage.PENDING_EXIT
            cell.exit_order_id = action.order_id
            cell.exit_client_id = action.client_order_id
        store.save_cell(cell)
        store.append_event(
            action.strategy_id,
            "OUTAGE_OPEN_ORDER_RECOVERED",
            {"role": action.role, "order_id": action.order_id},
            action.cell_id,
            "outage-recovery",
        )

    for action in actions:
        if action.kind != "settle_terminal_order":
            continue
        cell = next(
            item
            for item in store.list_cells(action.strategy_id)
            if item.cell_id == action.cell_id
        )
        if action.role == "entry":
            cell.stage = CellStage.PENDING_ENTRY
            cell.entry_order_id = action.order_id
            cell.entry_client_id = action.client_order_id
        else:
            cell.stage = CellStage.PENDING_EXIT
            cell.exit_order_id = action.order_id
            cell.exit_client_id = action.client_order_id
        store.save_cell(cell)
        store.append_event(
            action.strategy_id,
            "OUTAGE_TERMINAL_ORDER_REPLAYED",
            {
                "role": action.role,
                "order_id": action.order_id,
                "status": action.status,
                "executed_qty": action.executed_qty,
                "logical_delta": action.logical_delta,
                "replayed_at": utc_now(),
            },
            action.cell_id,
            "outage-recovery",
        )
        engines[action.strategy_id].sync_cell(cell)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
