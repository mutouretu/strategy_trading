"""Coordinate multiple grid rules deployed at fixed price intervals."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Mapping, Sequence

from grid_rule import (
    GridFill,
    GridMode,
    GridOrderIntent,
    GridRuleConfig,
)

from .ports import GridRuleFactory, GridRulePort


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
    rule: GridRulePort
    waiting_for_reentry: bool = False
    reset_count: int = 0


@dataclass(slots=True)
class _RetiringGrid:
    layer_index: int
    anchor_price: Decimal
    generation: int
    rule: GridRulePort


@dataclass(frozen=True, slots=True)
class _OrderOwner:
    layer_index: int
    anchor_price: Decimal
    generation: int
    state: str
    rule: GridRulePort


class LayeredFollowingGridStrategy:
    """Deploy another following grid after each fixed downward price step.

    When a lower grid follows upward into the lower boundary of the grid above
    it, the lower grid is recreated at its original anchor. Existing positions
    are not discarded: their exit orders remain managed by a retiring engine.
    """

    def __init__(
        self,
        config: LayeredFollowingGridStrategyConfig,
        rule_factory: GridRuleFactory,
    ) -> None:
        self.config = config
        self._rule_factory = rule_factory
        self._validate_initial_layer_spacing()
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
            + sum(
                layer.rule.snapshot().completed_cycles
                for layer in self._layers
            )
            + sum(
                grid.rule.snapshot().completed_cycles
                for grid in self._retiring_grids
            )
        )

    @property
    def cells_added(self) -> int:
        return (
            self._archived_cells_added
            + sum(
                layer.rule.snapshot().cells_added
                for layer in self._layers
            )
            + sum(
                grid.rule.snapshot().cells_added
                for grid in self._retiring_grids
            )
        )

    @property
    def cells_reclaimed(self) -> int:
        return (
            self._archived_cells_reclaimed
            + sum(
                layer.rule.snapshot().cells_reclaimed
                for layer in self._layers
            )
            + sum(
                grid.rule.snapshot().cells_reclaimed
                for grid in self._retiring_grids
            )
        )

    @property
    def desired_orders(self) -> tuple[GridOrderIntent, ...]:
        active_orders = [
            intent
            for layer in self._layers
            for intent in layer.rule.desired_orders
        ]
        retiring_exits = [
            intent
            for grid in self._retiring_grids
            for intent in grid.rule.exit_orders
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
            layer.rule.on_market(mark_price)
        self._deploy_crossed_layers(mark_price)
        self._reset_colliding_lower_layers(mark_price)
        return self.desired_orders

    def on_fills(
        self,
        fills: Sequence[GridFill],
    ) -> tuple[GridOrderIntent, ...]:
        self._require_initialized()
        owners = self._order_owners()
        fills_by_rule: dict[GridRulePort, list[GridFill]] = {}
        for fill in fills:
            owner = owners.get(fill.order_key)
            if owner is None:
                raise ValueError(
                    f"unexpected fill order_key: {fill.order_key}"
                )
            fills_by_rule.setdefault(owner.rule, []).append(fill)
        for rule, rule_fills in fills_by_rule.items():
            rule.on_fills(rule_fills)
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

    def _validate_initial_layer_spacing(self) -> None:
        upper = self._rule_factory.preview(self.config.rule_template)
        lower_config = replace(
            self.config.rule_template,
            grid_id=f"{self.config.rule_template.grid_id}:spacing-check",
            anchor_price=(
                self.config.rule_template.anchor_price
                - self.config.deployment_step
            ),
        )
        lower = self._rule_factory.preview(lower_config)
        if lower.upper_edge >= upper.lower_edge:
            raise ValueError(
                "adjacent grids overlap at deployment: "
                f"lower upper edge {lower.upper_edge} must be below "
                f"upper lower edge {upper.lower_edge}; reduce grid width "
                "or increase deployment_step"
            )

    def _create_layer(
        self,
        *,
        layer_index: int,
        anchor_price: Decimal,
        generation: int,
        mark_price: Decimal,
    ) -> _ActiveLayer:
        rule = self._rule_factory.create(
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
        rule.initialize(initialization_mark)
        return _ActiveLayer(
            layer_index=layer_index,
            anchor_price=anchor_price,
            generation=generation,
            rule=rule,
        )

    def _reset_colliding_lower_layers(
        self,
        mark_price: Decimal,
    ) -> None:
        for upper, lower in zip(self._layers, self._layers[1:]):
            if lower.waiting_for_reentry:
                continue
            if (
                lower.rule.snapshot().upper_edge
                < upper.rule.snapshot().lower_edge
            ):
                continue
            self._reset_layer(lower, mark_price)

    def _reset_layer(
        self,
        layer: _ActiveLayer,
        mark_price: Decimal,
    ) -> None:
        old_rule = layer.rule
        old_generation = layer.generation
        if old_rule.snapshot().has_open_position:
            exits = old_rule.exit_orders
            if not exits:
                raise RuntimeError(
                    "cannot retire a grid with a position but no exit order"
                )
            self._retiring_grids.append(
                _RetiringGrid(
                    layer_index=layer.layer_index,
                    anchor_price=layer.anchor_price,
                    generation=old_generation,
                    rule=old_rule,
                )
            )
        else:
            self._archive_rule(layer.layer_index, old_rule)

        layer.generation += 1
        replacement = self._create_layer(
            layer_index=layer.layer_index,
            anchor_price=layer.anchor_price,
            generation=layer.generation,
            mark_price=mark_price,
        )
        layer.rule = replacement.rule
        layer.waiting_for_reentry = mark_price >= layer.anchor_price
        layer.reset_count += 1
        self._reset_count += 1

    def _remove_fully_retired_grids(self) -> None:
        remaining: list[_RetiringGrid] = []
        for grid in self._retiring_grids:
            if grid.rule.snapshot().has_open_position:
                remaining.append(grid)
                continue
            if grid.rule.exit_orders:
                raise RuntimeError(
                    "retiring grid has an exit order without a position"
                )
            self._archive_rule(grid.layer_index, grid.rule)
        self._retiring_grids = remaining

    def _archive_rule(
        self,
        layer_index: int,
        rule: GridRulePort,
    ) -> None:
        snapshot = rule.snapshot()
        self._archived_completed_cycles += snapshot.completed_cycles
        self._archived_cells_added += snapshot.cells_added
        self._archived_cells_reclaimed += snapshot.cells_reclaimed
        self._archived_cycles_by_layer[layer_index] = (
            self._archived_cycles_by_layer.get(layer_index, 0)
            + snapshot.completed_cycles
        )

    def _order_owners(self) -> dict[str, _OrderOwner]:
        owners: dict[str, _OrderOwner] = {}
        for layer in self._layers:
            owner = _OrderOwner(
                layer_index=layer.layer_index,
                anchor_price=layer.anchor_price,
                generation=layer.generation,
                state="active",
                rule=layer.rule,
            )
            for intent in layer.rule.desired_orders:
                owners[intent.order_key] = owner
        for grid in self._retiring_grids:
            owner = _OrderOwner(
                layer_index=grid.layer_index,
                anchor_price=grid.anchor_price,
                generation=grid.generation,
                state="retiring",
                rule=grid.rule,
            )
            for intent in grid.rule.exit_orders:
                owners[intent.order_key] = owner
        return owners

    def _snapshot(
        self,
        layer: _ActiveLayer,
    ) -> FollowingGridLayerSnapshot:
        active = layer.rule.snapshot()
        return FollowingGridLayerSnapshot(
            layer_index=layer.layer_index,
            anchor_price=layer.anchor_price,
            generation=layer.generation,
            lower_edge=active.lower_edge,
            upper_edge=active.upper_edge,
            waiting_for_reentry=layer.waiting_for_reentry,
            reset_count=layer.reset_count,
            completed_cycles=(
                self._archived_cycles_by_layer.get(layer.layer_index, 0)
                + active.completed_cycles
                + sum(
                    grid.rule.snapshot().completed_cycles
                    for grid in self._retiring_grids
                    if grid.layer_index == layer.layer_index
                )
            ),
            position_quantity=self._layer_position_quantity(layer),
        )

    def _layer_position_quantity(self, layer: _ActiveLayer) -> Decimal:
        rules = [layer.rule] + [
            grid.rule
            for grid in self._retiring_grids
            if grid.layer_index == layer.layer_index
        ]
        return sum(
            (
                rule.snapshot().position_quantity
                for rule in rules
            ),
            Decimal("0"),
        )

    @staticmethod
    def _check_mark(mark_price: Decimal) -> None:
        if mark_price <= 0:
            raise ValueError("mark_price must be > 0")

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError("strategy must be initialized first")
