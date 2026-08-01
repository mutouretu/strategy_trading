"""Keep one following-grid rule active."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from grid_rule import GridFill, GridOrderIntent, GridRuleConfig

from .ports import GridRuleFactory, GridRulePort


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
    """Deploy one rule at startup and keep that rule active."""

    def __init__(
        self,
        config: SingleFollowingGridStrategyConfig,
        rule_factory: GridRuleFactory,
    ) -> None:
        self.config = config
        self._rule_factory = rule_factory
        self._rule: GridRulePort | None = None

    @property
    def rule(self) -> GridRulePort:
        if self._rule is None:
            raise RuntimeError("strategy must be initialized first")
        return self._rule

    def initialize(
        self,
        mark_price: Decimal,
    ) -> tuple[GridOrderIntent, ...]:
        if self._rule is not None:
            raise RuntimeError("strategy is already initialized")
        self._rule = self._rule_factory.create(self.config.rule)
        return self._rule.initialize(mark_price)

    def on_market(
        self,
        mark_price: Decimal,
    ) -> tuple[GridOrderIntent, ...]:
        return self.rule.on_market(mark_price)

    def on_fills(
        self,
        fills: Sequence[GridFill],
    ) -> tuple[GridOrderIntent, ...]:
        return self.rule.on_fills(fills)
