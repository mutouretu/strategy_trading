"""Read-only, strategy-scoped realized performance for fixed USD-M grids."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from datetime import datetime, timezone
from decimal import Decimal

from ..domain import StrategyConfig
from ..infrastructure.sqlite_store import SQLiteStore


TRADE_PAGE_SIZE = 1000
MAX_PAGES_PER_SYNC = 20
TRADE_WINDOW_MS = 7 * 24 * 60 * 60 * 1000 - 1
HISTORY_LOOKBACK_MS = 90 * 24 * 60 * 60 * 1000
YEAR_SECONDS = Decimal(365 * 24 * 60 * 60)


def _sell_order_cycles(strategy_events: list[dict]) -> dict[tuple[str, int], int]:
    """Assign each closing order to the cell cycle active when it was placed."""

    next_cycle: dict[str, int] = defaultdict(lambda: 1)
    orders: dict[tuple[str, int], int] = {}
    exit_events = {
        "EXIT_PLACED", "EXIT_PARTIALLY_FILLED", "EXIT_TARGET_FILLED",
        "EXIT_ENDED", "CYCLE_CLOSED", "FAR_EXIT_DEFERRED",
    }
    recovery_events = {
        "OPEN_ORDER_RECOVERED", "ORDER_SUBMISSION_CONFIRMED",
        "MANUAL_REVIEW_ORDER_RECOVERED",
    }
    for event in strategy_events:
        cell_id = event["cell_id"]
        if cell_id is None:
            continue
        kind = event["event_type"]
        payload = event["payload"]
        order_id = payload.get("order_id")
        if order_id is not None and (
            kind in exit_events
            or (kind in recovery_events and payload.get("role") == "exit")
        ):
            orders.setdefault((cell_id, int(order_id)), next_cycle[cell_id])
        if kind == "CYCLE_CLOSED":
            next_cycle[cell_id] = max(
                next_cycle[cell_id] + 1,
                int(payload.get("cycle_count", next_cycle[cell_id])) + 1,
            )
        elif kind == "EXIT_ENDED" and payload.get("action") == "cycle_closed":
            next_cycle[cell_id] = max(
                next_cycle[cell_id] + 1,
                int(payload.get("cycle_count", next_cycle[cell_id])) + 1,
            )
    return orders


def _started_at(store: SQLiteStore, strategy_id: str) -> datetime | None:
    value = store.get_first_started_at(strategy_id)
    return datetime.fromisoformat(value).astimezone(timezone.utc) if value else None


def sync_strategy_trades(
    store: SQLiteStore,
    exchange,
    config: StrategyConfig,
    *,
    now: datetime | None = None,
) -> bool:
    """Persist all symbol fills so delayed order attribution remains possible."""

    current = now or datetime.now(timezone.utc)
    now_ms = int(current.timestamp() * 1000)
    started = _started_at(store, config.strategy_id)
    if started is None:
        return False
    started_ms = int(started.timestamp() * 1000)
    sync_state = store.get_strategy_trade_sync(config.strategy_id)
    history_complete = (
        bool(sync_state["history_complete"])
        and sync_state["scanned_through_ms"] >= now_ms - HISTORY_LOOKBACK_MS
        if sync_state is not None else started_ms >= now_ms - HISTORY_LOOKBACK_MS
    )
    latest = store.latest_strategy_trade_id(config.strategy_id)
    pages = 0
    if latest is None:
        window_start = max(
            started_ms, now_ms - HISTORY_LOOKBACK_MS,
            sync_state["scanned_through_ms"] + 1 if sync_state is not None else started_ms,
        )
        while window_start <= now_ms and pages < MAX_PAGES_PER_SYNC:
            window_end = min(window_start + TRADE_WINDOW_MS, now_ms)
            page = exchange.get_user_trades(
                config.symbol, start_time_ms=window_start,
                end_time_ms=window_end, limit=TRADE_PAGE_SIZE,
            )
            pages += 1
            if page:
                store.save_strategy_trades(config.strategy_id, page)
                latest = max(int(trade["id"]) for trade in page)
                break
            window_start = window_end + 1
        if latest is None:
            store.save_strategy_trade_sync(
                config.strategy_id, history_complete, min(window_start - 1, now_ms)
            )
            return history_complete and window_start > now_ms

    while pages < MAX_PAGES_PER_SYNC:
        page = exchange.get_user_trades(
            config.symbol, from_id=latest + 1, limit=TRADE_PAGE_SIZE,
        )
        pages += 1
        if not page:
            store.save_strategy_trade_sync(config.strategy_id, history_complete, now_ms)
            return history_complete
        store.save_strategy_trades(config.strategy_id, page)
        newest = max(int(trade["id"]) for trade in page)
        if newest <= latest:
            raise ValueError("trade history cursor did not advance")
        latest = newest
        if len(page) < TRADE_PAGE_SIZE:
            store.save_strategy_trade_sync(config.strategy_id, history_complete, now_ms)
            return history_complete
    store.save_strategy_trade_sync(config.strategy_id, history_complete, now_ms)
    return False


def strategy_performance(
    store: SQLiteStore,
    config: StrategyConfig,
    *,
    coverage_complete: bool,
    now: datetime | None = None,
) -> dict:
    current = now or datetime.now(timezone.utc)
    started = _started_at(store, config.strategy_id)
    if started is None or config.grid_budget_usdt is None:
        raise ValueError("strategy has not started or has no grid budget")
    if config.mode.value != "long":
        raise ValueError("fixed grid performance requires a long strategy")

    cells = store.list_cells(config.strategy_id)
    levels = store.get_cell_price_history(config.strategy_id)
    order_to_cell: dict[int, str] = {}
    strategy_events = store.list_events(config.strategy_id)
    sell_order_cycles = _sell_order_cycles(strategy_events)
    initial_entry_price = next(
        (
            event["payload"].get("average_price")
            for event in strategy_events
            if event["event_type"] == "CAPITAL_ACTION_APPLIED"
            and event["payload"].get("operation") == "seed"
        ),
        None,
    )
    for event in strategy_events:
        order_id = event["payload"].get("order_id")
        cell_id = event["cell_id"]
        if order_id is not None and cell_id is not None:
            order_to_cell[int(order_id)] = cell_id
    for cell in cells:
        for order_id in (cell.entry_order_id, cell.exit_order_id):
            if order_id is not None:
                order_to_cell[int(order_id)] = cell.cell_id

    actions = store.list_capital_actions(config.strategy_id)
    capital_order_ids = {
        int(action["order_id"])
        for action in actions if action["order_id"] is not None
    }
    reconfiguration_order_ids = {
        int(event["payload"]["position_delta_order_id"])
        for event in strategy_events
        if event["event_type"] == "GRID_RECONFIGURED"
        and event["payload"].get("position_delta_order_id") is not None
    }
    trades = [
        trade for trade in store.list_strategy_trades(config.strategy_id)
        if trade["order_id"] in order_to_cell
        or trade["order_id"] in capital_order_ids
        or trade["order_id"] in reconfiguration_order_ids
    ]
    reconfiguration_fees = sum(
        (Decimal(trade["commission"]) for trade in trades
         if trade["order_id"] in reconfiguration_order_ids
         and trade["commission_asset"] == "USDT"),
        Decimal("0"),
    )
    reconfiguration_realized_pnl = sum(
        (Decimal(trade["realized_pnl"]) for trade in trades
         if trade["order_id"] in reconfiguration_order_ids),
        Decimal("0"),
    )
    other_fee_assets = sorted({
        trade["commission_asset"] for trade in trades
        if trade["commission_asset"] != "USDT" and Decimal(trade["commission"]) != 0
    })
    account_gross = sum(
        (Decimal(trade["realized_pnl"]) for trade in trades), Decimal("0")
    )
    account_fees = sum(
        (Decimal(trade["commission"]) for trade in trades if trade["commission_asset"] == "USDT"),
        Decimal("0"),
    )
    # Match each cell's actual fills. Aggregate seed/top-up buys use the cell's
    # grid buy line as their virtual basis so their initial trend PnL stays out.
    # Binance realizedPnl uses the whole position's average cost instead.
    by_cell: dict[str, list[tuple[int, int, str, Decimal, Decimal, Decimal, int]]] = defaultdict(list)
    accounting_error: str | None = None
    for action in actions:
        if action["status"] != "applied" or action["order_id"] is None:
            continue
        action_fills = [trade for trade in trades if trade["order_id"] == int(action["order_id"])]
        allocations = json.loads(action["planned_allocations_json"] or "{}")
        executed = Decimal(action["executed_qty"])
        if (
            not action_fills
            or any(trade["side"] != "BUY" for trade in action_fills)
            or sum((Decimal(trade["quantity"]) for trade in action_fills), Decimal("0")) != executed
            or sum((Decimal(str(qty)) for qty in allocations.values()), Decimal("0")) != executed
        ):
            accounting_error = "aggregate buy fills or cell allocations are incomplete"
            continue
        total_fee = sum(
            (Decimal(trade["commission"]) for trade in action_fills
             if trade["commission_asset"] == "USDT"), Decimal("0")
        )
        last_fill = max(action_fills, key=lambda trade: (trade["traded_at_ms"], trade["trade_id"]))
        for cell_id, quantity in allocations.items():
            qty = Decimal(str(quantity))
            if qty > 0:
                if cell_id not in levels:
                    accounting_error = f"grid level is missing for cell {cell_id}"
                    continue
                by_cell[cell_id].append((
                    last_fill["traded_at_ms"], last_fill["trade_id"],
                    "BUY", qty, total_fee * qty / executed, levels[cell_id][0],
                    int(action["order_id"]),
                ))

    for event in strategy_events:
        if event["event_type"] != "GRID_RECONFIGURED":
            continue
        payload = event["payload"]
        allocations = payload["allocations"]
        fees = payload["opening_fees"]
        for cell_id, quantity in allocations.items():
            if cell_id not in levels:
                accounting_error = f"reconfigured grid level is missing for cell {cell_id}"
                continue
            by_cell[cell_id].append((
                int(payload["timestamp_ms"]), -1, "BUY", Decimal(quantity),
                Decimal(fees[cell_id]), levels[cell_id][0], 0,
            ))

    for trade in trades:
        cell_id = order_to_cell.get(trade["order_id"])
        if cell_id is None:
            continue
        by_cell[cell_id].append((
            trade["traded_at_ms"], trade["trade_id"], trade["side"],
            Decimal(trade["quantity"]),
            Decimal(trade["commission"]) if trade["commission_asset"] == "USDT" else Decimal("0"),
            Decimal(trade["price"]),
            int(trade["order_id"]),
        ))

    grid_gross = Decimal("0")
    grid_fees = Decimal("0")
    sold_quantity = Decimal("0")
    cell_grid_profit = {cell_id: Decimal("0") for cell_id in levels}
    cell_cycle_profit: dict[str, dict[int, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    current_cell_ids = {cell.cell_id for cell in cells}
    current_open_qty: dict[str, Decimal] = {}
    current_open_fees: dict[str, Decimal] = {}
    cycle_accounting_complete = True
    for cell_id, events in by_cell.items():
        if cell_id not in levels:
            accounting_error = f"grid level is missing for cell {cell_id}"
            continue
        open_lots: deque[list[Decimal]] = deque()
        for _, _, side, qty, fee, fill_price, order_id in sorted(events, key=lambda event: (event[0], event[1])):
            if side == "BUY":
                open_lots.append([qty, fee, fill_price])
                continue
            if side != "SELL":
                accounting_error = f"unexpected trade side {side}"
                continue
            remaining = qty
            matched_fee = Decimal("0")
            matched_buy_cost = Decimal("0")
            while remaining > 0 and open_lots:
                lot = open_lots[0]
                taken = min(remaining, lot[0])
                taken_fee = lot[1] * taken / lot[0]
                matched_buy_cost += taken * lot[2]
                lot[0] -= taken
                lot[1] -= taken_fee
                remaining -= taken
                matched_fee += taken_fee
                if lot[0] == 0:
                    open_lots.popleft()
            if remaining > 0:
                accounting_error = f"buy fill is missing for cell {cell_id}"
                continue
            sold_quantity += qty
            # A catch-up fill may improve on a grid line. Keep the actual
            # paired spread when it is worse, but do not count any excess
            # above the cell's planned spread as pure grid profit.
            actual_spread = qty * fill_price - matched_buy_cost
            planned_spread = qty * (levels[cell_id][1] - levels[cell_id][0])
            gross = min(actual_spread, planned_spread)
            fees = matched_fee + fee
            grid_gross += gross
            grid_fees += fees
            cell_grid_profit[cell_id] += gross - fees
            cycle = sell_order_cycles.get((cell_id, order_id))
            if cycle is None:
                cycle_accounting_complete = False
            else:
                cell_cycle_profit[cell_id][cycle] += gross - fees

        if cell_id in current_cell_ids:
            current_open_qty[cell_id] = sum((lot[0] for lot in open_lots), Decimal("0"))
            current_open_fees[cell_id] = sum((lot[1] for lot in open_lots), Decimal("0"))

    net = None if other_fee_assets or accounting_error else grid_gross - grid_fees
    cell_net = (
        None if net is None or not coverage_complete
        else {cell_id: str(profit) for cell_id, profit in cell_grid_profit.items()}
    )
    cell_cycle_net = (
        None if net is None or not coverage_complete or not cycle_accounting_complete
        else {
            cell_id: {str(cycle): str(profit) for cycle, profit in sorted(profits.items())}
            for cell_id, profits in cell_cycle_profit.items()
        }
    )
    budget = config.grid_budget_usdt
    roi = None if net is None else net / budget * Decimal("100")
    elapsed_seconds = max((current - started).total_seconds(), 0)
    annualized = (
        None if roi is None or elapsed_seconds <= 0
        else roi * YEAR_SECONDS / Decimal(str(elapsed_seconds))
    )
    return {
        "strategy_id": config.strategy_id,
        "initial_entry_price": initial_entry_price,
        "started_at": started.isoformat(),
        "as_of": current.isoformat(),
        "grid_budget_usdt": str(budget),
        "gross_grid_profit_usdt": str(grid_gross),
        "grid_trading_fees_usdt": str(grid_fees),
        "sold_grid_quantity": str(sold_quantity),
        "gross_realized_pnl_usdt": str(account_gross),
        "trading_fees_usdt": str(account_fees),
        "reconfiguration_trading_fees_usdt": str(reconfiguration_fees),
        "reconfiguration_realized_pnl_usdt": str(reconfiguration_realized_pnl),
        "trend_and_execution_pnl_usdt": str(account_gross - grid_gross),
        "net_grid_profit_usdt": None if net is None else str(net),
        "cell_net_grid_profit_usdt": cell_net,
        "cell_cycle_net_grid_profit_usdt": cell_cycle_net,
        "open_buy_qty_by_cell": {key: str(value) for key, value in current_open_qty.items()},
        "open_buy_fees_by_cell_usdt": {key: str(value) for key, value in current_open_fees.items()},
        "cycle_accounting_complete": cycle_accounting_complete,
        "grid_profit_pct": None if roi is None else str(roi),
        "simple_annualized_pct": None if annualized is None else str(annualized),
        "elapsed_seconds": elapsed_seconds,
        "completed_cycles": max(
            sum(cell.cycle_count for cell in cells),
            sum(
                event["event_type"] == "CYCLE_CLOSED"
                or (event["event_type"] == "EXIT_ENDED"
                    and event["payload"].get("action") == "cycle_closed")
                for event in strategy_events
            ),
        ),
        "trade_fill_count": len(trades),
        "coverage_complete": coverage_complete,
        "accounting_complete": accounting_error is None,
        "accounting_error": accounting_error,
        "unsupported_fee_assets": other_fee_assets,
        "funding_included": False,
        "unrealized_included": False,
    }
