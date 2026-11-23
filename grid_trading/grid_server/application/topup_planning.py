"""One cash and quantity calculation for fixed-grid top-up preview and execution."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..domain import FixedEntryMode, GridCell, StrategyConfig, SymbolFilters
from ..domain.grid import fixed_cell_budgets, round_down


MARKET_BUY_PRICE_BUFFER = Decimal("0.02")


@dataclass(frozen=True)
class FixedTopupPlan:
    allocations: dict[str, Decimal]
    held_cell_ids: list[str]
    per_cell_usdt: Decimal
    held_budget_increase_usdt: Decimal
    future_budget_increase_usdt: Decimal
    future_downside_budget_usdt: Decimal
    aggregate_buy_qty: Decimal
    aggregate_buy_usdt: Decimal
    estimated_opening_fee_usdt: Decimal
    price_buffer_usdt: Decimal
    cash_reserve_usdt: Decimal
    minimum_available_usdt: Decimal


def _target_quantity(cell: GridCell, budget: Decimal, filters: SymbolFilters) -> Decimal:
    quantity = round_down(budget / cell.buy_price, filters.step_size)
    if quantity < filters.min_qty or quantity * cell.buy_price < filters.min_notional:
        return Decimal("0")
    return quantity


def plan_fixed_topup(
    config: StrategyConfig,
    cells: list[GridCell],
    amount_usdt: Decimal,
    filters: SymbolFilters,
    mark: Decimal,
    taker_fee_rate: Decimal,
) -> FixedTopupPlan:
    """Price a top-up; available balance already excludes resting order margin."""
    if mark <= 0 or not mark.is_finite():
        raise ValueError("mark price must be positive and finite")
    if taker_fee_rate < 0 or not taker_fee_rate.is_finite():
        raise ValueError("taker fee rate must be nonnegative and finite")
    if config.grid_budget_usdt is None or sum(
        (cell.budget_usdt for cell in cells), Decimal("0")
    ) != config.grid_budget_usdt:
        raise ValueError("grid budget and cell budgets differ; refresh before adding capital")
    increments = fixed_cell_budgets(amount_usdt, len(cells))
    held = [(cell, increment) for cell, increment in zip(cells, increments) if cell.open_qty > 0]
    allocations = {
        cell.cell_id: max(
            Decimal("0"),
            _target_quantity(cell, cell.budget_usdt + increment, filters)
            - _target_quantity(cell, cell.budget_usdt, filters),
        )
        for cell, increment in held
    }
    quantity = sum(allocations.values(), Decimal("0"))
    estimated_buy = quantity * mark
    fee = estimated_buy * taker_fee_rate
    held_increase = sum((increment for _, increment in held), Decimal("0"))
    future_downside = sum(
        (cell.budget_usdt + increment for cell, increment in zip(cells, increments)
         if cell.open_qty == 0 and cell.buy_price <= mark),
        Decimal("0"),
    )
    if config.fixed_entry_mode == FixedEntryMode.PRICE_TRIGGERED:
        assert config.grid_budget_usdt is not None
        reserve = min(Decimal("1000"), (config.grid_budget_usdt + amount_usdt) * Decimal("0.01"))
        price_buffer = estimated_buy * MARKET_BUY_PRICE_BUFFER * (Decimal("1") + taker_fee_rate)
        minimum_available = estimated_buy + fee + price_buffer + reserve
    else:
        # Seeded grids retain the original full-budget funding requirement.
        reserve = Decimal("0")
        price_buffer = Decimal("0")
        minimum_available = amount_usdt
    return FixedTopupPlan(
        allocations=allocations,
        held_cell_ids=[cell.cell_id for cell, _ in held],
        per_cell_usdt=increments[0],
        held_budget_increase_usdt=held_increase,
        future_budget_increase_usdt=amount_usdt - held_increase,
        future_downside_budget_usdt=future_downside,
        aggregate_buy_qty=quantity,
        aggregate_buy_usdt=estimated_buy,
        estimated_opening_fee_usdt=fee,
        price_buffer_usdt=price_buffer,
        cash_reserve_usdt=reserve,
        minimum_available_usdt=minimum_available,
    )
