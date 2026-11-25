"""Straight-line break-even estimate from the exchange's current position."""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

from ..domain import CellStage, GridCell, Mode, StrategyConfig, SymbolFilters
from ..infrastructure.sqlite_store import SQLiteStore
from ..ports.exchange import Exchange


def current_break_even_projection(
    store: SQLiteStore, exchange: Exchange, config: StrategyConfig,
    *, mark: Decimal | None = None,
) -> dict:
    """Read one live exchange position and project from its break-even price."""
    side = "LONG" if config.mode == Mode.LONG else "SHORT"
    if side != "LONG":
        return {"status": "unavailable", "reason": "目前仅支持做多固定网格"}
    _, break_even = exchange.get_position_prices(config.symbol, side)
    positions = exchange.get_positions()
    qty = sum(
        (position.quantity for position in positions
         if position.symbol == config.symbol
         and position.position_side.upper() in {side, "BOTH"}),
        Decimal("0"),
    )
    if break_even is None or qty <= 0:
        return {"status": "unavailable", "reason": "平台当前没有可用的持仓损益两平价"}
    return project_break_even(
        mark=mark if mark is not None else exchange.get_mark_price(config.symbol),
        platform_break_even=break_even,
        position_qty=qty,
        cells=store.list_cells(config.strategy_id),
        filters=exchange.get_symbol_filters(config.symbol),
    )


def project_break_even(
    *,
    mark: Decimal,
    platform_break_even: Decimal,
    position_qty: Decimal,
    cells: list[GridCell],
    filters: SymbolFilters,
) -> dict:
    """Walk fills on a monotonic path; ignore fees, funding and future reinvestment.

    The exchange break-even price compresses all of its past position accounting
    into one current cost basis.  Only fills *after* this snapshot are replayed.
    Untriggered cells are deliberately excluded: they have no buy order yet.
    """
    if mark <= 0 or platform_break_even <= 0 or position_qty <= 0:
        raise ValueError("mark, platform break-even and position quantity must be positive")
    if filters.step_size <= 0:
        raise ValueError("quantity step must be positive")

    logical_qty = sum((cell.open_qty for cell in cells), Decimal("0"))
    if abs(logical_qty - position_qty) >= filters.step_size:
        return {
            "status": "unavailable",
            "reason": "平台持仓与单元格持仓不一致，暂不估算",
            "platform_break_even": str(platform_break_even),
            "position_qty": str(position_qty),
            "logical_qty": str(logical_qty),
        }

    direction = "up" if platform_break_even >= mark else "down"
    events: list[tuple[Decimal, str, Decimal]] = []
    for cell in cells:
        if direction == "up":
            if (cell.stage == CellStage.PENDING_EXIT and cell.open_qty > 0
                    and cell.sell_price > mark):
                events.append((cell.sell_price, "sell", cell.open_qty))
        elif cell.stage == CellStage.PENDING_ENTRY and cell.buy_price < mark:
            qty = (cell.budget_usdt / cell.buy_price / filters.step_size).to_integral_value(
                rounding=ROUND_DOWN,
            ) * filters.step_size
            if qty >= filters.min_qty and qty * cell.buy_price >= filters.min_notional and qty > 0:
                events.append((cell.buy_price, "buy", qty))
    events.sort(key=lambda event: event[0], reverse=direction == "down")

    cost = position_qty * platform_break_even
    qty = position_qty
    cursor = mark
    buy_count = sell_count = 0
    for event_price, side, event_qty in events:
        candidate = cost / qty if qty > 0 else None
        if candidate is not None and (
            cursor <= candidate < event_price if direction == "up"
            else event_price < candidate <= cursor
        ):
            break
        if side == "sell":
            qty -= event_qty
            cost -= event_qty * event_price
            sell_count += 1
        else:
            qty += event_qty
            cost += event_qty * event_price
            buy_count += 1
        cursor = event_price
    else:
        candidate = cost / qty if qty > 0 else None

    found = candidate is not None and (
        candidate >= cursor if direction == "up" else candidate <= cursor
    )
    return {
        "status": "ok" if found else "unavailable",
        "reason": None if found else "沿当前方向走完可预计订单后没有剩余仓位或未出现回本价",
        "direction": direction,
        "estimated_price": str(candidate) if found else None,
        "platform_break_even": str(platform_break_even),
        "mark_price": str(mark),
        "position_qty": str(position_qty),
        "remaining_qty": str(qty),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "logical_qty": str(logical_qty),
    }
