from __future__ import annotations

import hashlib
from decimal import Decimal, ROUND_DOWN, localcontext

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
    if lower <= 0 or upper <= lower or count < 1:
        raise ValueError("fixed grid requires 0 < lower < upper and count >= 1")
    with localcontext() as context:
        context.prec = 40
        return +(((upper / lower).ln() / Decimal(count)).exp() - Decimal("1"))


def fixed_cell_budgets(total: Decimal, count: int) -> list[Decimal]:
    quantum = Decimal("0.00000001")
    if total <= 0 or count < 1 or total != total.quantize(quantum):
        raise ValueError("grid budget must be positive with at most 8 decimals")
    per_cell = (total / Decimal(count)).quantize(quantum, rounding=ROUND_DOWN)
    if per_cell <= 0:
        raise ValueError("grid budget is too small for the grid count")
    return [per_cell] * (count - 1) + [total - per_cell * (count - 1)]


def build_fixed_cells(config: StrategyConfig, tick_size: Decimal) -> list[GridCell]:
    config.validate()
    assert config.lower_price is not None
    assert config.upper_price is not None
    assert config.grid_budget_usdt is not None
    lower, upper = config.lower_price, config.upper_price
    if tick_size <= 0 or round_down(lower, tick_size) != lower or round_down(upper, tick_size) != upper:
        raise ValueError("fixed grid boundaries must align with the symbol price tick")
    growth = Decimal("1") + fixed_grid_ratio(lower, upper, config.grid_count)
    with localcontext() as context:
        context.prec = 40
        boundaries = [lower]
        boundaries.extend(
            round_down(lower * growth**index, tick_size)
            for index in range(1, config.grid_count)
        )
    boundaries.append(upper)
    if any(left >= right for left, right in zip(boundaries, boundaries[1:])):
        raise ValueError("grid interval is too narrow for the symbol price tick")
    budgets = fixed_cell_budgets(config.grid_budget_usdt, config.grid_count)
    return [
        GridCell(
            strategy_id=config.strategy_id,
            cell_id=fixed_cell_id(config, buy, sell),
            index=index,
            buy_price=buy,
            sell_price=sell,
            budget_usdt=budgets[index - 1],
        )
        for index, (buy, sell) in enumerate(zip(boundaries, boundaries[1:]), start=1)
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
