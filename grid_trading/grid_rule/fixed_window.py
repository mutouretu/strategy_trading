"""Deterministic near-price order-window decisions for a fixed grid.

Adapters report which cells are executable at this mark. The rule chooses
which logical cell orders should occupy exchange slots; cancellation,
placement, recovery, and account audits remain adapter responsibilities.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .fixed_equal_usdt import FixedEntryMode


@dataclass(frozen=True, slots=True)
class WindowCell:
    cell_id: str
    index: int
    buy_price: Decimal
    sell_price: Decimal
    stage: str
    open_qty: Decimal
    has_entry_order: bool
    has_exit_order: bool
    entry_ready: bool
    exit_ready: bool


@dataclass(frozen=True, slots=True)
class WindowInput:
    cells: tuple[WindowCell, ...]
    mark: Decimal
    prior_entry_ids: frozenset[str] = frozenset()
    prior_exit_ids: frozenset[str] = frozenset()
    repair_ids: frozenset[str] = frozenset()
    repair_audit_passed: bool = False
    initialized: bool = False
    cleanup_pending: bool = False
    entry_mode: FixedEntryMode = FixedEntryMode.SEEDED
    orders_per_side: int = 10
    rebalance_below: int = 5
    rebalance_above: int = 15


@dataclass(frozen=True, slots=True)
class WindowDecision:
    entry_ids: frozenset[str]
    exit_ids: frozenset[str]
    active_entry_ids: frozenset[str]
    active_exit_ids: frozenset[str]
    rebalance: bool
    price_priority_changed: bool
    entry_gap: bool
    exit_gap: bool
    audited_repair_exits: frozenset[str]
    crossed_entry_ids: frozenset[str]


def _interior_gap(
    preferred: list[WindowCell], active: frozenset[str],
    paired: set[str], blocked: set[str] | None = None,
) -> bool:
    blocked_ids = blocked if blocked is not None else set()
    farthest_active = max(
        (index for index, cell in enumerate(preferred) if cell.cell_id in active),
        default=-1,
    )
    return any(
        index < farthest_active and cell.cell_id not in active
        and cell.cell_id not in paired and cell.cell_id not in blocked_ids
        for index, cell in enumerate(preferred)
    )


def plan_fixed_window(value: WindowInput) -> WindowDecision:
    """Choose order slots without reading a database or placing an order."""
    if value.orders_per_side < 1 or value.rebalance_below < 1:
        raise ValueError("window limits must be positive")
    entries = [cell for cell in value.cells if cell.stage == "pending_entry"]
    exits = [
        cell for cell in value.cells
        if cell.stage == "pending_exit" and cell.open_qty > 0
    ]
    active_entries = frozenset(
        cell.cell_id for cell in entries if cell.has_entry_order
    )
    active_exits = frozenset(
        cell.cell_id for cell in exits if cell.has_exit_order
    )
    ready_entry_cells = [cell for cell in entries if cell.entry_ready]
    ready_exit_cells = [cell for cell in exits if cell.exit_ready]
    ready_entries = sum(cell.cell_id in active_entries for cell in ready_entry_cells)
    ready_exits = sum(cell.cell_id in active_exits for cell in ready_exit_cells)
    if value.entry_mode == FixedEntryMode.PRICE_TRIGGERED:
        sparse_window = (
            bool(ready_entry_cells) and ready_entries < value.rebalance_below
        ) or (
            bool(ready_exit_cells) and ready_exits < value.rebalance_below
        )
    else:
        sparse_window = (
            min(len(active_entries), len(active_exits)) < value.rebalance_below
            or min(ready_entries, ready_exits) == 0
        )

    crossed_entries = sorted(
        (
            cell for cell in ready_entry_cells
            if not cell.has_entry_order
            and cell.cell_id not in value.prior_exit_ids
            and cell.buy_price >= value.mark
        ),
        key=lambda cell: (abs(cell.buy_price - value.mark), cell.index),
    )[:value.orders_per_side]
    crossed_ids = frozenset(cell.cell_id for cell in crossed_entries)
    preferred_entry_cells = crossed_entries + [
        cell for cell in sorted(
            ready_entry_cells,
            key=lambda item: (abs(item.buy_price - value.mark), item.index),
        )
        if cell.cell_id not in crossed_ids
    ][:value.orders_per_side - len(crossed_entries)]
    preferred_entries = {cell.cell_id for cell in preferred_entry_cells}
    preferred_exit_cells = sorted(
        ready_exit_cells,
        key=lambda cell: (abs(cell.sell_price - value.mark), cell.index),
    )[:value.orders_per_side]
    preferred_exits = {cell.cell_id for cell in preferred_exit_cells}
    paired_entries = {
        cell.cell_id for cell in entries
        if not cell.has_entry_order and cell.cell_id in value.prior_exit_ids
    }
    paired_exits = {
        cell.cell_id for cell in exits
        if not cell.has_exit_order and cell.cell_id in value.prior_entry_ids
    }
    price_priority_changed = (
        bool((preferred_entries - active_entries - paired_entries)
             and (active_entries - preferred_entries))
        or bool((preferred_exits - active_exits - paired_exits)
                and (active_exits - preferred_exits))
    )
    repair_candidates = (value.repair_ids & preferred_exits) - active_exits
    audited_repair_exits = frozenset(
        repair_candidates if value.repair_audit_passed else ()
    )
    entry_gap = _interior_gap(
        preferred_entry_cells, active_entries, paired_entries,
    )
    exit_gap = _interior_gap(
        preferred_exit_cells, active_exits, paired_exits,
        set(value.repair_ids - audited_repair_exits),
    )
    rebalance = (
        not value.initialized or sparse_window
        or max(len(active_entries), len(active_exits)) > value.rebalance_above
        or value.cleanup_pending or price_priority_changed
        or entry_gap or exit_gap or bool(audited_repair_exits)
        or bool(crossed_ids)
    )
    if rebalance:
        entry_ids = preferred_entries | paired_entries
        exit_ids = preferred_exits | paired_exits
    else:
        entry_ids = set(active_entries)
        exit_ids = set(active_exits)
        entry_ids.update(paired_entries)
        exit_ids.update(paired_exits)
        crossed_exits = sorted(
            (
                cell for cell in exits
                if not cell.has_exit_order
                and cell.cell_id not in value.prior_entry_ids
                and cell.sell_price <= value.mark
            ),
            key=lambda cell: abs(cell.sell_price - value.mark),
        )[:value.orders_per_side]
        exit_ids.update(cell.cell_id for cell in crossed_exits)
    return WindowDecision(
        entry_ids=frozenset(entry_ids), exit_ids=frozenset(exit_ids),
        active_entry_ids=active_entries, active_exit_ids=active_exits,
        rebalance=rebalance, price_priority_changed=price_priority_changed,
        entry_gap=entry_gap, exit_gap=exit_gap,
        audited_repair_exits=audited_repair_exits,
        crossed_entry_ids=crossed_ids,
    )
