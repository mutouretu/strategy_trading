from __future__ import annotations

import hashlib
from decimal import Decimal, ROUND_DOWN

from .domain import GridCell, Mode, StrategyConfig


def round_down(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def stable_cell_id(strategy_id: str, buy_price: Decimal, sell_price: Decimal) -> str:
    raw = f"{strategy_id}:{decimal_text(buy_price)}:{decimal_text(sell_price)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def build_cells(config: StrategyConfig, tick_size: Decimal) -> list[GridCell]:
    """Build cells using the Web anchor semantics.

    LONG: anchor is the highest sell boundary and cells are derived downward.
    SHORT: anchor is the lowest buy boundary and cells are derived upward.
    Returned indices always run from lowest price to highest price.
    """
    config.validate()
    growth = Decimal("1") + config.grid_ratio
    pairs: list[tuple[Decimal, Decimal]] = []

    if config.mode == Mode.LONG:
        sell = round_down(config.anchor_price, tick_size)
        for _ in range(config.grid_count):
            buy = round_down(sell / growth, tick_size)
            if buy >= sell:
                buy = sell - tick_size
            pairs.append((buy, sell))
            sell = buy
    else:
        buy = round_down(config.anchor_price, tick_size)
        for _ in range(config.grid_count):
            sell = round_down(buy * growth, tick_size)
            if sell <= buy:
                sell = buy + tick_size
            pairs.append((buy, sell))
            buy = sell

    pairs.sort(key=lambda pair: pair[0])
    cells = [
        GridCell(
            strategy_id=config.strategy_id,
            cell_id=stable_cell_id(config.strategy_id, buy, sell),
            index=index,
            buy_price=buy,
            sell_price=sell,
        )
        for index, (buy, sell) in enumerate(pairs, start=1)
    ]
    for cell in cells:
        cell.validate()
    return cells


def next_long_cell(config: StrategyConfig, highest: GridCell, tick_size: Decimal) -> GridCell:
    buy = highest.sell_price
    sell = round_down(buy * (Decimal("1") + config.grid_ratio), tick_size)
    if sell <= buy:
        sell = buy + tick_size
    return GridCell(config.strategy_id, stable_cell_id(config.strategy_id, buy, sell), highest.index + 1, buy, sell)


def next_short_cell(config: StrategyConfig, lowest: GridCell, tick_size: Decimal) -> GridCell:
    sell = lowest.buy_price
    buy = round_down(sell / (Decimal("1") + config.grid_ratio), tick_size)
    if buy >= sell:
        buy = sell - tick_size
    return GridCell(config.strategy_id, stable_cell_id(config.strategy_id, buy, sell), 0, buy, sell)
