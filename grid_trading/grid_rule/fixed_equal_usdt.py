"""Pure rules shared by fixed equal-USDT live and simulation adapters.

This module contains no exchange, database, scheduler, or simulator imports.
The strategy owns capital and chooses which rule instances to run; adapters
turn these deterministic cell decisions into exchange orders or simulated
trade instructions.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, localcontext
from enum import StrEnum
from typing import Protocol, Sequence

from .grid import stable_cell_id


class FixedEntryMode(StrEnum):
    SEEDED = "seeded"
    PRICE_TRIGGERED = "price_triggered"


@dataclass(frozen=True, slots=True)
class FixedCellSpec:
    cell_id: str
    index: int
    buy_price: Decimal
    sell_price: Decimal
    budget_usdt: Decimal


class FixedCellLike(Protocol):
    cell_id: str
    buy_price: Decimal
    sell_price: Decimal
    budget_usdt: Decimal


@dataclass(frozen=True, slots=True)
class TriggerCell:
    cell_id: str
    buy_price: Decimal


class TriggerCellLike(Protocol):
    cell_id: str
    buy_price: Decimal


@dataclass(frozen=True, slots=True)
class FixedGridSpec:
    grid_id: str
    lower_price: Decimal
    upper_price: Decimal
    grid_count: int
    grid_budget_usdt: Decimal
    tick_size: Decimal
    generation: int = 0

    def __post_init__(self) -> None:
        if not self.grid_id.strip():
            raise ValueError("grid_id must not be empty")
        if self.generation < 0:
            raise ValueError("generation must be >= 0")


def round_down(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def fixed_grid_ratio(lower: Decimal, upper: Decimal, count: int) -> Decimal:
    if lower <= 0 or upper <= lower or count < 1:
        raise ValueError("fixed grid requires 0 < lower < upper and count >= 1")
    with localcontext() as context:
        context.prec = 40
        return +(((upper / lower).ln() / Decimal(count)).exp() - Decimal("1"))


def fixed_cell_budgets(total: Decimal, count: int) -> tuple[Decimal, ...]:
    quantum = Decimal("0.00000001")
    if total <= 0 or count < 1 or total != total.quantize(quantum):
        raise ValueError("grid budget must be positive with at most 8 decimals")
    per_cell = (total / Decimal(count)).quantize(quantum, rounding=ROUND_DOWN)
    if per_cell <= 0:
        raise ValueError("grid budget is too small for the grid count")
    return (per_cell,) * (count - 1) + (total - per_cell * (count - 1),)


def build_fixed_cells(spec: FixedGridSpec) -> tuple[FixedCellSpec, ...]:
    lower, upper, tick = spec.lower_price, spec.upper_price, spec.tick_size
    if tick <= 0 or round_down(lower, tick) != lower or round_down(upper, tick) != upper:
        raise ValueError("fixed grid boundaries must align with the symbol price tick")
    growth = Decimal("1") + fixed_grid_ratio(lower, upper, spec.grid_count)
    with localcontext() as context:
        context.prec = 40
        boundaries = [lower]
        boundaries.extend(
            round_down(lower * growth**index, tick)
            for index in range(1, spec.grid_count)
        )
    boundaries.append(upper)
    if any(left >= right for left, right in zip(boundaries, boundaries[1:])):
        raise ValueError("grid interval is too narrow for the symbol price tick")
    budgets = fixed_cell_budgets(spec.grid_budget_usdt, spec.grid_count)
    namespace = (
        spec.grid_id if spec.generation == 0
        else f"{spec.grid_id}:generation:{spec.generation}"
    )
    return tuple(
        FixedCellSpec(
            cell_id=stable_cell_id(namespace, buy, sell),
            index=index,
            buy_price=buy,
            sell_price=sell,
            budget_usdt=budgets[index - 1],
        )
        for index, (buy, sell) in enumerate(zip(boundaries, boundaries[1:]), start=1)
    )


def order_quantity(
    cell: FixedCellLike, *, step_size: Decimal,
    min_qty: Decimal, min_notional: Decimal,
) -> Decimal:
    quantity = round_down(cell.budget_usdt / cell.buy_price, step_size)
    if quantity < min_qty or quantity * cell.buy_price < min_notional:
        return Decimal("0")
    return quantity


def initial_sell_allocations(
    cells: Sequence[FixedCellLike], mark: Decimal,
    *, step_size: Decimal, min_qty: Decimal, min_notional: Decimal,
    entry_mode: FixedEntryMode = FixedEntryMode.SEEDED,
) -> dict[str, Decimal]:
    """Position ownership requested at startup; triggered mode owns none."""
    if entry_mode == FixedEntryMode.PRICE_TRIGGERED:
        return {}
    return {
        cell.cell_id: order_quantity(
            cell, step_size=step_size, min_qty=min_qty,
            min_notional=min_notional,
        )
        for cell in cells if cell.sell_price > mark
    }


def triggered_entry_ids(
    cells: Sequence[TriggerCellLike], mark: Decimal,
    *, already_armed: frozenset[str] = frozenset(),
) -> frozenset[str]:
    """Sticky crossing eligibility; order-window selection happens later."""
    return frozenset(
        cell.cell_id for cell in cells
        if cell.cell_id not in already_armed and mark >= cell.buy_price
    )
