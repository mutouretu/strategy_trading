"""Pure grid construction and price-step calculations."""

from __future__ import annotations

import hashlib
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from .models import GridCellState, GridRuleConfig, GridMode


def round_down(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def round_to_nearest(value: Decimal, step: Decimal) -> Decimal:
    return (
        (value / step).to_integral_value(rounding=ROUND_HALF_UP)
        * step
    )


def decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def stable_cell_id(
    grid_id: str,
    buy_price: Decimal,
    sell_price: Decimal,
) -> str:
    raw = f"{grid_id}:{decimal_text(buy_price)}:{decimal_text(sell_price)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def build_grid_cells(config: GridRuleConfig) -> tuple[GridCellState, ...]:
    growth = Decimal("1") + config.grid_ratio
    pairs: list[tuple[Decimal, Decimal]] = []
    if config.mode == GridMode.LONG:
        sell = round_down(config.anchor_price, config.tick_size)
        for _ in range(config.grid_count):
            buy = round_down(sell / growth, config.tick_size)
            if buy >= sell:
                buy = sell - config.tick_size
            pairs.append((buy, sell))
            sell = buy
    else:
        buy = round_down(config.anchor_price, config.tick_size)
        for _ in range(config.grid_count):
            sell = round_down(buy * growth, config.tick_size)
            if sell <= buy:
                sell = buy + config.tick_size
            pairs.append((buy, sell))
            buy = sell

    pairs.sort(key=lambda pair: pair[0])
    return tuple(
        GridCellState(
            cell_id=stable_cell_id(config.grid_id, buy, sell),
            index=index,
            buy_price=buy,
            sell_price=sell,
        )
        for index, (buy, sell) in enumerate(pairs, start=1)
    )


def next_long_cell(
    config: GridRuleConfig,
    highest: GridCellState,
) -> GridCellState:
    buy = highest.sell_price
    sell = round_down(
        buy * (Decimal("1") + config.grid_ratio),
        config.tick_size,
    )
    if sell <= buy:
        sell = buy + config.tick_size
    return GridCellState(
        cell_id=stable_cell_id(config.grid_id, buy, sell),
        index=highest.index + 1,
        buy_price=buy,
        sell_price=sell,
    )


def next_short_cell(
    config: GridRuleConfig,
    lowest: GridCellState,
) -> GridCellState:
    sell = lowest.buy_price
    buy = round_down(
        sell / (Decimal("1") + config.grid_ratio),
        config.tick_size,
    )
    if buy >= sell:
        buy = sell - config.tick_size
    return GridCellState(
        cell_id=stable_cell_id(config.grid_id, buy, sell),
        index=0,
        buy_price=buy,
        sell_price=sell,
    )
