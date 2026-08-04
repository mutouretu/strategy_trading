"""State transitions for one configured grid."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Sequence

from .grid import (
    build_grid_cells,
    next_long_cell,
    next_short_cell,
    round_down,
    round_to_nearest,
)
from .models import (
    CellPhase,
    GridFill,
    GridOrderIntent,
    GridOrderRole,
    GridOrderSide,
    GridCellState,
    GridRuleConfig,
    GridMarketType,
    GridMode,
)


class GridRuleEngine:
    """Deterministic grid rules with no exchange, storage, or simulator imports."""

    def __init__(self, config: GridRuleConfig) -> None:
        self.config = config
        self._cells: dict[str, GridCellState] = {}
        self._intents: dict[str, GridOrderIntent] = {}
        self._initialized = False
        self._cells_added = 0
        self._cells_reclaimed = 0
        self._completed_cycles = 0

    @property
    def cells(self) -> tuple[GridCellState, ...]:
        return tuple(
            replace(cell)
            for cell in sorted(self._cells.values(), key=lambda item: item.index)
        )

    @property
    def desired_orders(self) -> tuple[GridOrderIntent, ...]:
        by_cell_index = {cell.cell_id: cell.index for cell in self._cells.values()}
        return tuple(
            sorted(
                self._intents.values(),
                key=lambda intent: (
                    by_cell_index[intent.cell_id],
                    intent.role.value,
                ),
            )
        )

    @property
    def completed_cycles(self) -> int:
        return self._completed_cycles

    @property
    def cells_added(self) -> int:
        return self._cells_added

    @property
    def cells_reclaimed(self) -> int:
        return self._cells_reclaimed

    def initialize(self, mark_price: Decimal) -> tuple[GridOrderIntent, ...]:
        if mark_price <= 0:
            raise ValueError("mark_price must be > 0")
        cells = build_grid_cells(self.config)
        self._cells = {cell.cell_id: cell for cell in cells}
        self._intents = {}
        self._initialized = True
        self._cells_added = 0
        self._cells_reclaimed = 0
        self._completed_cycles = 0
        self._move_window(mark_price)
        self._arm_triggered_entries(mark_price)
        return self.desired_orders

    def on_market(self, mark_price: Decimal) -> tuple[GridOrderIntent, ...]:
        self._require_initialized()
        if mark_price <= 0:
            raise ValueError("mark_price must be > 0")
        self._move_window(mark_price)
        self._arm_triggered_entries(mark_price)
        return self.desired_orders

    def on_fills(
        self,
        fills: Sequence[GridFill],
    ) -> tuple[GridOrderIntent, ...]:
        self._require_initialized()
        for fill in fills:
            intent = self._intents.get(fill.order_key)
            if intent is None:
                raise ValueError(f"unexpected fill order_key: {fill.order_key}")
            if fill.instrument != self.config.instrument:
                raise ValueError(
                    f"unexpected fill instrument: {fill.instrument}"
                )
            if fill.side != intent.side:
                raise ValueError(
                    f"unexpected fill side for {fill.order_key}: {fill.side}"
                )
            if fill.quantity != intent.quantity:
                raise ValueError(
                    "partial fills are not supported by the first rule version"
                )

            cell = self._cells[intent.cell_id]
            self._intents.pop(fill.order_key)
            cell.current_order_key = None
            if intent.role == GridOrderRole.ENTRY:
                cell.position_quantity += fill.quantity
                self._arm_exit(cell, fill.quantity)
            else:
                cell.position_quantity -= fill.quantity
                if cell.position_quantity != 0:
                    raise ValueError(
                        f"cell {cell.cell_id} did not close to zero"
                    )
                cell.phase = CellPhase.DORMANT
                cell.cycle_count += 1
                self._completed_cycles += 1
        return self.desired_orders

    def _arm_triggered_entries(self, mark_price: Decimal) -> None:
        for cell in self._cells.values():
            if cell.phase != CellPhase.DORMANT:
                continue
            if self.config.mode == GridMode.LONG:
                triggered = mark_price >= cell.buy_price
            else:
                triggered = mark_price <= cell.sell_price
            if triggered:
                self._arm_entry(cell)

    def _move_window(self, mark_price: Decimal) -> None:
        if not self.config.move_grid or not self._cells:
            return

        cells = sorted(self._cells.values(), key=lambda cell: cell.buy_price)
        additions = 0
        if self.config.mode == GridMode.LONG:
            while mark_price >= cells[-1].sell_price and additions < 100:
                cell = next_long_cell(self.config, cells[-1])
                self._cells[cell.cell_id] = cell
                cells.append(cell)
                additions += 1
                self._cells_added += 1
        else:
            while mark_price <= cells[0].buy_price and additions < 100:
                cell = next_short_cell(self.config, cells[0])
                self._cells[cell.cell_id] = cell
                cells.insert(0, cell)
                additions += 1
                self._cells_added += 1

        while len(cells) > self.config.grid_count:
            removable = (
                cells[0]
                if self.config.mode == GridMode.LONG
                else cells[-1]
            )
            if (
                removable.position_quantity > 0
                or removable.phase == CellPhase.EXIT_PENDING
            ):
                break
            self._remove_unowned_cell(removable)
            cells = [
                cell for cell in cells if cell.cell_id != removable.cell_id
            ]

        for index, cell in enumerate(cells, start=1):
            cell.index = index

    def _remove_unowned_cell(self, cell: GridCellState) -> None:
        if cell.phase == CellPhase.ENTRY_PENDING:
            if cell.current_order_key is None:
                raise RuntimeError(
                    f"entry-pending cell {cell.cell_id} has no order key"
                )
            self._intents.pop(cell.current_order_key)
            cell.current_order_key = None
            cell.phase = CellPhase.DORMANT
        elif cell.phase != CellPhase.DORMANT:
            raise RuntimeError(
                f"cannot reclaim cell {cell.cell_id} in phase {cell.phase}"
            )
        self._cells.pop(cell.cell_id)
        self._cells_reclaimed += 1

    def _arm_entry(self, cell: GridCellState) -> None:
        side = (
            GridOrderSide.BUY
            if self.config.mode == GridMode.LONG
            else GridOrderSide.SELL
        )
        price = (
            cell.buy_price
            if self.config.mode == GridMode.LONG
            else cell.sell_price
        )
        quantity = self._quantity(price)
        if quantity <= 0:
            raise ValueError(
                f"order quantity rounds to zero for cell {cell.cell_id}"
            )
        intent = GridOrderIntent(
            order_key=self._order_key(cell, GridOrderRole.ENTRY),
            instrument=self.config.instrument,
            side=side,
            role=GridOrderRole.ENTRY,
            price=price,
            quantity=quantity,
            cell_id=cell.cell_id,
            cycle=cell.cycle_count,
        )
        cell.phase = CellPhase.ENTRY_PENDING
        cell.current_order_key = intent.order_key
        self._intents[intent.order_key] = intent

    def _quantity(self, price: Decimal) -> Decimal:
        if self.config.market_type == GridMarketType.COINM:
            assert self.config.order_coin_qty is not None
            raw_contracts = (
                self.config.order_coin_qty
                * price
                / self.config.contract_size
            )
            return round_to_nearest(
                raw_contracts,
                self.config.quantity_step,
            )
        return round_down(
            self.config.order_notional / price,
            self.config.quantity_step,
        )

    def _arm_exit(self, cell: GridCellState, quantity: Decimal) -> None:
        side = (
            GridOrderSide.SELL
            if self.config.mode == GridMode.LONG
            else GridOrderSide.BUY
        )
        price = (
            cell.sell_price
            if self.config.mode == GridMode.LONG
            else cell.buy_price
        )
        intent = GridOrderIntent(
            order_key=self._order_key(cell, GridOrderRole.EXIT),
            instrument=self.config.instrument,
            side=side,
            role=GridOrderRole.EXIT,
            price=price,
            quantity=quantity,
            cell_id=cell.cell_id,
            cycle=cell.cycle_count,
        )
        cell.phase = CellPhase.EXIT_PENDING
        cell.current_order_key = intent.order_key
        self._intents[intent.order_key] = intent

    def _order_key(
        self,
        cell: GridCellState,
        role: GridOrderRole,
    ) -> str:
        return (
            f"{self.config.grid_id}:{cell.cell_id}:"
            f"{role.value}:{cell.cycle_count}"
        )

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError("rule engine must be initialized first")
