"""The smallest high-level strategy: keep one following grid active."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from grid_rule import (
    GridFill,
    GridOrderIntent,
    GridRuleConfig,
    GridRuleEngine,
)


@dataclass(frozen=True, slots=True)
class SingleFollowingGridStrategyConfig:
    strategy_id: str
    rule: GridRuleConfig

    def __post_init__(self) -> None:
        if not self.strategy_id.strip():
            raise ValueError("strategy_id must not be empty")
        if not self.rule.move_grid:
            raise ValueError(
                "single following grid strategy requires move_grid=True"
            )


class SingleFollowingGridStrategy:
    """Deploy one following grid at startup and keep it active.

    This deliberately minimal strategy never adds a second grid, changes its
    capital allocation, or exits it. Those decisions belong to later strategy
    versions rather than the grid rule engine.
    """

    def __init__(self, config: SingleFollowingGridStrategyConfig) -> None:
        self.config = config
        self._engine: GridRuleEngine | None = None

    @property
    def engine(self) -> GridRuleEngine:
        if self._engine is None:
            raise RuntimeError("strategy must be initialized first")
        return self._engine

    def initialize(
        self,
        mark_price: Decimal,
    ) -> tuple[GridOrderIntent, ...]:
        if self._engine is not None:
            raise RuntimeError("strategy is already initialized")
        self._engine = GridRuleEngine(self.config.rule)
        return self._engine.initialize(mark_price)

    def on_market(
        self,
        mark_price: Decimal,
    ) -> tuple[GridOrderIntent, ...]:
        return self.engine.on_market(mark_price)

    def on_fills(
        self,
        fills: Sequence[GridFill],
    ) -> tuple[GridOrderIntent, ...]:
        return self.engine.on_fills(fills)
