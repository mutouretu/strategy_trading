"""Concrete grid-rule implementation behind the strategy-owned port."""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from grid_rule import (
    GridFill,
    GridOrderIntent,
    GridOrderRole,
    GridRuleConfig,
    GridRuleEngine,
    build_grid_cells,
)
from trading_strategies.grid_following.ports import (
    GridRuleCellSnapshot,
    GridRulePort,
    GridRuleSnapshot,
)


def _snapshot(
    *,
    cells,
    completed_cycles: int,
    cells_added: int,
    cells_reclaimed: int,
) -> GridRuleSnapshot:
    return GridRuleSnapshot(
        cells=tuple(
            GridRuleCellSnapshot(
                cell_id=cell.cell_id,
                buy_price=cell.buy_price,
                sell_price=cell.sell_price,
                phase=cell.phase.value,
                position_quantity=cell.position_quantity,
                cycle_count=cell.cycle_count,
            )
            for cell in cells
        ),
        completed_cycles=completed_cycles,
        cells_added=cells_added,
        cells_reclaimed=cells_reclaimed,
    )


class GridRuleEnginePort:
    """Expose only the rule operations and facts needed by strategies."""

    def __init__(self, config: GridRuleConfig) -> None:
        self._engine = GridRuleEngine(config)

    @property
    def config(self) -> GridRuleConfig:
        return self._engine.config

    @property
    def desired_orders(self) -> tuple[GridOrderIntent, ...]:
        return self._engine.desired_orders

    @property
    def exit_orders(self) -> tuple[GridOrderIntent, ...]:
        return tuple(
            intent
            for intent in self._engine.desired_orders
            if intent.role == GridOrderRole.EXIT
        )

    def snapshot(self) -> GridRuleSnapshot:
        return _snapshot(
            cells=self._engine.cells,
            completed_cycles=self._engine.completed_cycles,
            cells_added=self._engine.cells_added,
            cells_reclaimed=self._engine.cells_reclaimed,
        )

    def initialize(
        self,
        mark_price: Decimal,
    ) -> tuple[GridOrderIntent, ...]:
        return self._engine.initialize(mark_price)

    def on_market(
        self,
        mark_price: Decimal,
    ) -> tuple[GridOrderIntent, ...]:
        return self._engine.on_market(mark_price)

    def on_fills(
        self,
        fills: Sequence[GridFill],
    ) -> tuple[GridOrderIntent, ...]:
        return self._engine.on_fills(fills)


class GridRuleEngineFactory:
    """Default factory used by simulation; no strategy imports the engine."""

    def preview(self, config: GridRuleConfig) -> GridRuleSnapshot:
        return _snapshot(
            cells=build_grid_cells(config),
            completed_cycles=0,
            cells_added=0,
            cells_reclaimed=0,
        )

    def create(self, config: GridRuleConfig) -> GridRulePort:
        return GridRuleEnginePort(config)
