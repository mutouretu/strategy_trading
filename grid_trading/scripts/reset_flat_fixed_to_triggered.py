"""Rebuild a stopped, externally flattened Binance grid in triggered mode."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

GRID_ROOT = Path(__file__).resolve().parents[1]
if str(GRID_ROOT) not in sys.path:
    sys.path.insert(0, str(GRID_ROOT))

from grid_server.application.grid_performance import strategy_performance
from grid_server.application.service import GridService
from grid_server.domain import CellStage, ExchangeVenue, FixedEntryMode
from grid_server.infrastructure.binance import BinanceFuturesExchange
from grid_server.infrastructure.sqlite_store import SQLiteStore
from grid_server.runtime.supervisor import StrategySupervisor
from grid_server.shared.config import (
    binance_base_url,
    binance_credentials,
    fixed_grid_endpoint_allowed,
    load_environment,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--lower", type=Decimal, required=True)
    parser.add_argument("--upper", type=Decimal, required=True)
    parser.add_argument("--grid-count", type=int, required=True)
    parser.add_argument("--budget", type=Decimal, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    load_environment(args.env_file)
    base_url = binance_base_url()
    if base_url != "https://fapi.binance.com" or not fixed_grid_endpoint_allowed(base_url):
        raise SystemExit("explicit Binance production fixed-grid opt-in is required")
    key, secret = binance_credentials(required=True)
    exchange = BinanceFuturesExchange(key, secret, base_url)
    store = SQLiteStore(args.db, venue=ExchangeVenue.BINANCE)
    service = GridService(
        store, StrategySupervisor(store, Path("/private/tmp/binance-grid-reset-preview-logs")),
    )
    plan = service.preview_flat_triggered_reset(
        args.strategy_id, args.lower, args.upper,
        args.grid_count, args.budget, exchange,
    )
    print(json.dumps({
        "action": "apply" if args.apply else "preview",
        "strategy_id": args.strategy_id,
        "old_generation": plan["config"].grid_generation - 1,
        "new_generation": plan["config"].grid_generation,
        "old_cell_count": plan["old_cell_count"],
        "old_logical_qty_to_discard": str(plan["old_logical_qty"]),
        "old_order_references_to_discard": plan["old_order_references"],
        "new_cell_count": len(plan["cells"]),
        "new_budget_usdt": str(sum(
            (cell.budget_usdt for cell in plan["cells"]), Decimal("0"),
        )),
        "mark": str(plan["mark"]),
        "available_balance_usdt": str(plan["available_balance"]),
    }, ensure_ascii=False, sort_keys=True))
    if not args.apply:
        return 0

    result = service.reset_flat_fixed_to_triggered(
        args.strategy_id, args.lower, args.upper,
        args.grid_count, args.budget, exchange,
    )
    config = store.get_strategy(args.strategy_id)
    assert config is not None and config.fixed_entry_mode == FixedEntryMode.PRICE_TRIGGERED
    cells = store.list_cells(args.strategy_id)
    if len(cells) != args.grid_count or any(
        cell.stage != CellStage.UNTRIGGERED or cell.open_qty != 0
        or cell.entry_order_id is not None or cell.exit_order_id is not None
        for cell in cells
    ):
        raise RuntimeError("post-reset cell verification failed")
    performance = strategy_performance(store, config, coverage_complete=True)
    if (not performance["accounting_complete"]
            or performance["net_grid_profit_usdt"]
            != result["historical_net_grid_profit_usdt"]):
        raise RuntimeError("historical grid profit changed after reset")
    print(json.dumps({
        **result,
        "status": config.status.value,
        "started_at": performance["started_at"],
        "completed_cycles": performance["completed_cycles"],
        "accounting_complete": performance["accounting_complete"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
