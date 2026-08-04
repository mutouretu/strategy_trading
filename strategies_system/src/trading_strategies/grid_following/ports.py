"""Stable rule boundary consumed by high-level grid strategies."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, Sequence

from grid_rule import GridFill, GridOrderIntent, GridRuleConfig


@dataclass(frozen=True, slots=True)
class GridRuleCellSnapshot:
    cell_id: str
    buy_price: Decimal
    sell_price: Decimal
    phase: str
    position_quantity: Decimal
    cycle_count: int


@dataclass(frozen=True, slots=True)
class GridRuleSnapshot:
    cells: tuple[GridRuleCellSnapshot, ...]
    completed_cycles: int
    cells_added: int
    cells_reclaimed: int

    @property
    def lower_edge(self) -> Decimal:
        if not self.cells:
            raise RuntimeError("grid rule snapshot has no cells")
        return min(cell.buy_price for cell in self.cells)

    @property
    def upper_edge(self) -> Decimal:
        if not self.cells:
            raise RuntimeError("grid rule snapshot has no cells")
        return max(cell.sell_price for cell in self.cells)

    @property
    def position_quantity(self) -> Decimal:
        return sum(
            (cell.position_quantity for cell in self.cells),
            Decimal("0"),
        )

    @property
    def has_open_position(self) -> bool:
        return self.position_quantity != 0


class GridRulePort(Protocol):
    """One independently running grid rule instance."""

    @property
    def config(self) -> GridRuleConfig: ...

    @property
    def desired_orders(self) -> tuple[GridOrderIntent, ...]: ...

    @property
    def exit_orders(self) -> tuple[GridOrderIntent, ...]: ...

    def snapshot(self) -> GridRuleSnapshot: ...

    def initialize(
        self,
        mark_price: Decimal,
    ) -> tuple[GridOrderIntent, ...]: ...

    def on_market(
        self,
        mark_price: Decimal,
    ) -> tuple[GridOrderIntent, ...]: ...

    def on_fills(
        self,
        fills: Sequence[GridFill],
    ) -> tuple[GridOrderIntent, ...]: ...


class GridRuleFactory(Protocol):
    """Create or preview rule instances without exposing their implementation."""

    def preview(self, config: GridRuleConfig) -> GridRuleSnapshot: ...

    def create(self, config: GridRuleConfig) -> GridRulePort: ...
