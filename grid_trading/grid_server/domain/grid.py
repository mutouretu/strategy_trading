from __future__ import annotations

import hashlib
from decimal import Decimal, ROUND_DOWN

from grid_rule.fixed_equal_usdt import (
    FixedGridSpec,
    build_fixed_cells as rule_build_fixed_cells,
    fixed_cell_budgets as rule_fixed_cell_budgets,
    fixed_grid_ratio as rule_fixed_grid_ratio,
)

from .models import GridCell, GridKind, Mode, StrategyConfig


def round_down(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def stable_cell_id(strategy_id: str, buy_price: Decimal, sell_price: Decimal) -> str:
    raw = f"{strategy_id}:{decimal_text(buy_price)}:{decimal_text(sell_price)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def fixed_cell_id(config: StrategyConfig, buy_price: Decimal, sell_price: Decimal) -> str:
    # Keep existing IDs stable; a reconfigured generation must never inherit
    # an old cell's fills, reinvestment or cycle counters at the same prices.
    namespace = (
        config.strategy_id if config.grid_generation == 0
        else f"{config.strategy_id}:generation:{config.grid_generation}"
    )
    return stable_cell_id(namespace, buy_price, sell_price)


def build_cells(config: StrategyConfig, tick_size: Decimal) -> list[GridCell]:
    """Build cells using the Web anchor semantics.

    LONG: anchor is the highest sell boundary and cells are derived downward.
    SHORT: anchor is the lowest buy boundary and cells are derived upward.
    Returned indices always run from lowest price to highest price.
    """
    config.validate()
    if config.grid_kind == GridKind.FIXED_EQUAL_USDT:
        return build_fixed_cells(config, tick_size)
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


def fixed_grid_ratio(lower: Decimal, upper: Decimal, count: int) -> Decimal:
    return rule_fixed_grid_ratio(lower, upper, count)


def fixed_cell_budgets(total: Decimal, count: int) -> list[Decimal]:
    return list(rule_fixed_cell_budgets(total, count))


def build_fixed_cells(config: StrategyConfig, tick_size: Decimal) -> list[GridCell]:
    config.validate()
    assert config.lower_price is not None
    assert config.upper_price is not None
    assert config.grid_budget_usdt is not None
    specs = rule_build_fixed_cells(FixedGridSpec(
        grid_id=config.strategy_id,
        lower_price=config.lower_price,
        upper_price=config.upper_price,
        grid_count=config.grid_count,
        grid_budget_usdt=config.grid_budget_usdt,
        tick_size=tick_size,
        generation=config.grid_generation,
    ))
    return [
        GridCell(
            strategy_id=config.strategy_id,
            cell_id=cell.cell_id,
            index=cell.index,
            buy_price=cell.buy_price,
            sell_price=cell.sell_price,
            budget_usdt=cell.budget_usdt,
        )
        for cell in specs
    ]


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
