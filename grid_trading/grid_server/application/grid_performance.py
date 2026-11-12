"""Read-only, strategy-scoped realized performance for fixed USD-M grids."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from ..domain import StrategyConfig
from ..infrastructure.sqlite_store import SQLiteStore


TRADE_PAGE_SIZE = 1000
MAX_PAGES_PER_SYNC = 20
TRADE_WINDOW_MS = 7 * 24 * 60 * 60 * 1000 - 1
HISTORY_LOOKBACK_MS = 90 * 24 * 60 * 60 * 1000
YEAR_SECONDS = Decimal(365 * 24 * 60 * 60)


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
    order_ids = {
        int(action["order_id"])
        for action in store.list_capital_actions(config.strategy_id)
        if action["order_id"] is not None
    }
    order_ids.update(
        int(event["payload"]["order_id"])
        for event in store.list_events(config.strategy_id)
        if isinstance(event["payload"], dict)
        and event["payload"].get("order_id") is not None
    )
    order_ids.update(
        int(order_id)
        for cell in store.list_cells(config.strategy_id)
        for order_id in (cell.entry_order_id, cell.exit_order_id)
        if order_id is not None
    )
    trades = [
        trade for trade in store.list_strategy_trades(config.strategy_id)
        if trade["order_id"] in order_ids
    ]
    other_fee_assets = sorted({
        trade["commission_asset"] for trade in trades
        if trade["commission_asset"] != "USDT" and Decimal(trade["commission"]) != 0
    })
    gross = sum((Decimal(trade["realized_pnl"]) for trade in trades), Decimal("0"))
    fees = sum(
        (Decimal(trade["commission"]) for trade in trades if trade["commission_asset"] == "USDT"),
        Decimal("0"),
    )
    net = None if other_fee_assets else gross - fees
    budget = config.grid_budget_usdt
    roi = None if net is None else net / budget * Decimal("100")
    elapsed_seconds = max((current - started).total_seconds(), 0)
    annualized = (
        None if roi is None or elapsed_seconds <= 0
        else roi * YEAR_SECONDS / Decimal(str(elapsed_seconds))
    )
    return {
        "strategy_id": config.strategy_id,
        "started_at": started.isoformat(),
        "as_of": current.isoformat(),
        "grid_budget_usdt": str(budget),
        "gross_realized_pnl_usdt": str(gross),
        "trading_fees_usdt": str(fees),
        "net_grid_profit_usdt": None if net is None else str(net),
        "grid_profit_pct": None if roi is None else str(roi),
        "simple_annualized_pct": None if annualized is None else str(annualized),
        "elapsed_seconds": elapsed_seconds,
        "completed_cycles": sum(cell.cycle_count for cell in store.list_cells(config.strategy_id)),
        "trade_fill_count": len(trades),
        "coverage_complete": coverage_complete,
        "unsupported_fee_assets": other_fee_assets,
        "funding_included": False,
        "unrealized_included": False,
    }
