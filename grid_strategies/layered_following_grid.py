"""Coordinate multiple following grids deployed at fixed price intervals."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Mapping, Sequence

from grid_rule import (
    GridFill,
    GridMode,
    GridOrderIntent,
    GridOrderRole,
    GridRuleConfig,
    GridRuleEngine,
    build_grid_cells,
)


@dataclass(frozen=True, slots=True)
class LayeredFollowingGridStrategyConfig:
    """Configuration for a downward ladder of independent following grids."""

    strategy_id: str
    rule_template: GridRuleConfig
    deployment_step: Decimal = Decimal("5000")
    max_layers: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.deployment_step, Decimal):
            object.__setattr__(
                self,
                "deployment_step",
                Decimal(str(self.deployment_step)),
            )
        if not self.strategy_id.strip():
            raise ValueError("strategy_id must not be empty")
        if self.rule_template.mode != GridMode.LONG:
            raise ValueError(
                "the first layered following-grid strategy supports LONG only"
            )
        if not self.rule_template.move_grid:
            raise ValueError(
                "layered following-grid strategy requires move_grid=True"
            )
        if self.deployment_step <= 0:
            raise ValueError("deployment_step must be > 0")
        if self.deployment_step >= self.rule_template.anchor_price:
            raise ValueError(
                "deployment_step must be smaller than anchor_price"
            )
        if self.max_layers is not None and self.max_layers < 1:
            raise ValueError("max_layers must be >= 1")
        self._validate_initial_layer_spacing()

    def _validate_initial_layer_spacing(self) -> None:
        upper_cells = build_grid_cells(self.rule_template)
        lower_config = replace(
            self.rule_template,
            grid_id=f"{self.rule_template.grid_id}:spacing-check",
            anchor_price=(
                self.rule_template.anchor_price - self.deployment_step
            ),
        )
        lower_cells = build_grid_cells(lower_config)
        upper_lower_edge = min(cell.buy_price for cell in upper_cells)
        lower_upper_edge = max(cell.sell_price for cell in lower_cells)
        if lower_upper_edge >= upper_lower_edge:
            raise ValueError(
                "adjacent grids overlap at deployment: "
                f"lower upper edge {lower_upper_edge} must be below "
                f"upper lower edge {upper_lower_edge}; reduce grid width "
                "or increase deployment_step"
            )


@dataclass(frozen=True, slots=True)
class FollowingGridLayerSnapshot:
    layer_index: int
    anchor_price: Decimal
    generation: int
    lower_edge: Decimal
    upper_edge: Decimal
    waiting_for_reentry: bool
    reset_count: int
    completed_cycles: int
    position_quantity: Decimal


@dataclass(slots=True)
class _ActiveLayer:
    layer_index: int
    anchor_price: Decimal
    generation: int
    engine: GridRuleEngine
    waiting_for_reentry: bool = False
    reset_count: int = 0


@dataclass(slots=True)
class _RetiringGrid:
    layer_index: int
    anchor_price: Decimal
    generation: int
    engine: GridRuleEngine


@dataclass(frozen=True, slots=True)
class _OrderOwner:
    layer_index: int
    anchor_price: Decimal
    generation: int
    state: str
    engine: GridRuleEngine


class LayeredFollowingGridStrategy:
    """Deploy another following grid after each fixed downward price step.

    When a lower grid follows upward into the lower boundary of the grid above
    it, the lower grid is recreated at its original anchor. Existing positions
    are not discarded: their exit orders remain managed by a retiring engine.
    """

    def __init__(self, config: LayeredFollowingGridStrategyConfig) -> None:
        self.config = config
        self._layers: list[_ActiveLayer] = []
        self._retiring_grids: list[_RetiringGrid] = []
        self._initialized = False
        self._reset_count = 0
        self._archived_completed_cycles = 0
        self._archived_cells_added = 0
        self._archived_cells_reclaimed = 0
        self._archived_cycles_by_layer: dict[int, int] = {}

    @property
    def layers(self) -> tuple[FollowingGridLayerSnapshot, ...]:
        return tuple(self._snapshot(layer) for layer in self._layers)

    @property
    def layer_count(self) -> int:
        return len(self._layers)

    @property
    def reset_count(self) -> int:
        return self._reset_count

    @property
    def retiring_grid_count(self) -> int:
        return len(self._retiring_grids)

    @property
    def completed_cycles(self) -> int:
        return (
            self._archived_completed_cycles
            + sum(layer.engine.completed_cycles for layer in self._layers)
            + sum(
                grid.engine.completed_cycles
                for grid in self._retiring_grids
            )
        )

    @property
    def cells_added(self) -> int:
        return (
            self._archived_cells_added
            + sum(layer.engine.cells_added for layer in self._layers)
            + sum(grid.engine.cells_added for grid in self._retiring_grids)
        )

    @property
    def cells_reclaimed(self) -> int:
        return (
            self._archived_cells_reclaimed
            + sum(layer.engine.cells_reclaimed for layer in self._layers)
            + sum(
                grid.engine.cells_reclaimed
                for grid in self._retiring_grids
            )
        )

    @property
    def desired_orders(self) -> tuple[GridOrderIntent, ...]:
        active_orders = [
            intent
            for layer in self._layers
            for intent in layer.engine.desired_orders
        ]
        retiring_exits = [
            intent
            for grid in self._retiring_grids
            for intent in grid.engine.desired_orders
            if intent.role == GridOrderRole.EXIT
        ]
        return tuple(active_orders + retiring_exits)

    def initialize(
        self,
        mark_price: Decimal,
    ) -> tuple[GridOrderIntent, ...]:
        if self._initialized:
            raise RuntimeError("strategy is already initialized")
        self._check_mark(mark_price)
        self._initialized = True
        self._deploy_crossed_layers(mark_price)
        return self.desired_orders

    def on_market(
        self,
        mark_price: Decimal,
    ) -> tuple[GridOrderIntent, ...]:
        self._require_initialized()
        self._check_mark(mark_price)
        for layer in self._layers:
            if layer.waiting_for_reentry:
                if mark_price >= layer.anchor_price:
                    continue
                layer.waiting_for_reentry = False
            layer.engine.on_market(mark_price)
        self._deploy_crossed_layers(mark_price)
        self._reset_colliding_lower_layers(mark_price)
        return self.desired_orders

    def on_fills(
        self,
        fills: Sequence[GridFill],
    ) -> tuple[GridOrderIntent, ...]:
        self._require_initialized()
        owners = self._order_owners()
        fills_by_engine: dict[GridRuleEngine, list[GridFill]] = {}
        for fill in fills:
            owner = owners.get(fill.order_key)
            if owner is None:
                raise ValueError(
                    f"unexpected fill order_key: {fill.order_key}"
                )
            fills_by_engine.setdefault(owner.engine, []).append(fill)
        for engine, engine_fills in fills_by_engine.items():
            engine.on_fills(engine_fills)
        self._remove_fully_retired_grids()
        return self.desired_orders

    def order_context(self, order_key: str) -> Mapping[str, str]:
        owner = self._order_owners().get(order_key)
        if owner is None:
            raise ValueError(f"unknown strategy order_key: {order_key}")
        return {
            "layer_index": str(owner.layer_index),
            "layer_anchor": str(owner.anchor_price),
            "layer_generation": str(owner.generation),
            "grid_state": owner.state,
        }

    def _deploy_crossed_layers(self, mark_price: Decimal) -> None:
        while self._can_deploy_another_layer():
            layer_index = len(self._layers)
            anchor_price = (
                self.config.rule_template.anchor_price
                - self.config.deployment_step * layer_index
            )
            if anchor_price <= 0:
                break
            if layer_index > 0 and mark_price > anchor_price:
                break
            self._layers.append(
                self._create_layer(
                    layer_index=layer_index,
                    anchor_price=anchor_price,
                    generation=0,
                    mark_price=mark_price,
                )
            )

    def _can_deploy_another_layer(self) -> bool:
        return (
            self.config.max_layers is None
            or len(self._layers) < self.config.max_layers
        )

    def _create_layer(
        self,
        *,
        layer_index: int,
        anchor_price: Decimal,
        generation: int,
        mark_price: Decimal,
    ) -> _ActiveLayer:
        engine = GridRuleEngine(
            replace(
                self.config.rule_template,
                grid_id=(
                    f"{self.config.strategy_id}:layer:{layer_index}:"
                    f"generation:{generation}"
                ),
                anchor_price=anchor_price,
            )
        )
        initialization_mark = min(
            mark_price,
            anchor_price - self.config.rule_template.tick_size,
        )
        engine.initialize(initialization_mark)
        return _ActiveLayer(
            layer_index=layer_index,
            anchor_price=anchor_price,
            generation=generation,
            engine=engine,
        )

    def _reset_colliding_lower_layers(
        self,
        mark_price: Decimal,
    ) -> None:
        for upper, lower in zip(self._layers, self._layers[1:]):
            if lower.waiting_for_reentry:
                continue
            if self._upper_edge(lower.engine) < self._lower_edge(upper.engine):
                continue
            self._reset_layer(lower, mark_price)

    def _reset_layer(
        self,
        layer: _ActiveLayer,
        mark_price: Decimal,
    ) -> None:
        old_engine = layer.engine
        old_generation = layer.generation
        if self._has_open_position(old_engine):
            exits = self._exit_orders(old_engine)
            if not exits:
                raise RuntimeError(
                    "cannot retire a grid with a position but no exit order"
                )
            self._retiring_grids.append(
                _RetiringGrid(
                    layer_index=layer.layer_index,
                    anchor_price=layer.anchor_price,
                    generation=old_generation,
                    engine=old_engine,
                )
            )
        else:
            self._archive_engine(layer.layer_index, old_engine)

        layer.generation += 1
        replacement = self._create_layer(
            layer_index=layer.layer_index,
            anchor_price=layer.anchor_price,
            generation=layer.generation,
            mark_price=mark_price,
        )
        layer.engine = replacement.engine
        layer.waiting_for_reentry = mark_price >= layer.anchor_price
        layer.reset_count += 1
        self._reset_count += 1

    def _remove_fully_retired_grids(self) -> None:
        remaining: list[_RetiringGrid] = []
        for grid in self._retiring_grids:
            if self._has_open_position(grid.engine):
                remaining.append(grid)
                continue
            if self._exit_orders(grid.engine):
                raise RuntimeError(
                    "retiring grid has an exit order without a position"
                )
            self._archive_engine(grid.layer_index, grid.engine)
        self._retiring_grids = remaining

    def _archive_engine(
        self,
        layer_index: int,
        engine: GridRuleEngine,
    ) -> None:
        self._archived_completed_cycles += engine.completed_cycles
        self._archived_cells_added += engine.cells_added
        self._archived_cells_reclaimed += engine.cells_reclaimed
        self._archived_cycles_by_layer[layer_index] = (
            self._archived_cycles_by_layer.get(layer_index, 0)
            + engine.completed_cycles
        )

    def _order_owners(self) -> dict[str, _OrderOwner]:
        owners: dict[str, _OrderOwner] = {}
        for layer in self._layers:
            owner = _OrderOwner(
                layer_index=layer.layer_index,
                anchor_price=layer.anchor_price,
                generation=layer.generation,
                state="active",
                engine=layer.engine,
            )
            for intent in layer.engine.desired_orders:
                owners[intent.order_key] = owner
        for grid in self._retiring_grids:
            owner = _OrderOwner(
                layer_index=grid.layer_index,
                anchor_price=grid.anchor_price,
                generation=grid.generation,
                state="retiring",
                engine=grid.engine,
            )
            for intent in self._exit_orders(grid.engine):
                owners[intent.order_key] = owner
        return owners

    def _snapshot(
        self,
        layer: _ActiveLayer,
    ) -> FollowingGridLayerSnapshot:
        return FollowingGridLayerSnapshot(
            layer_index=layer.layer_index,
            anchor_price=layer.anchor_price,
            generation=layer.generation,
            lower_edge=self._lower_edge(layer.engine),
            upper_edge=self._upper_edge(layer.engine),
            waiting_for_reentry=layer.waiting_for_reentry,
            reset_count=layer.reset_count,
            completed_cycles=(
                self._archived_cycles_by_layer.get(layer.layer_index, 0)
                + layer.engine.completed_cycles
                + sum(
                    grid.engine.completed_cycles
                    for grid in self._retiring_grids
                    if grid.layer_index == layer.layer_index
                )
            ),
            position_quantity=self._layer_position_quantity(layer),
        )

    def _layer_position_quantity(self, layer: _ActiveLayer) -> Decimal:
        engines = [layer.engine] + [
            grid.engine
            for grid in self._retiring_grids
            if grid.layer_index == layer.layer_index
        ]
        return sum(
            (
                cell.position_quantity
                for engine in engines
                for cell in engine.cells
            ),
            Decimal("0"),
        )

    @staticmethod
    def _lower_edge(engine: GridRuleEngine) -> Decimal:
        return min(cell.buy_price for cell in engine.cells)

    @staticmethod
    def _upper_edge(engine: GridRuleEngine) -> Decimal:
        return max(cell.sell_price for cell in engine.cells)

    @staticmethod
    def _has_open_position(engine: GridRuleEngine) -> bool:
        return any(
            cell.position_quantity != 0 for cell in engine.cells
        )

    @staticmethod
    def _exit_orders(
        engine: GridRuleEngine,
    ) -> tuple[GridOrderIntent, ...]:
        return tuple(
            intent
            for intent in engine.desired_orders
            if intent.role == GridOrderRole.EXIT
        )

    @staticmethod
    def _check_mark(mark_price: Decimal) -> None:
        if mark_price <= 0:
            raise ValueError("mark_price must be > 0")

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError("strategy must be initialized first")
