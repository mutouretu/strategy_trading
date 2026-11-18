"""Built-in StrategyDefinition catalog independent of experiment storage."""

from __future__ import annotations

from .entry_then_ladder_exit import EntryThenLadderExitStrategyDefinition
from .fixed_equal_usdt_grid_strategy import FixedEqualUsdtGridStrategyDefinition
from .strategy import StrategyDefinitionRegistry


def build_strategy_definition_registry() -> StrategyDefinitionRegistry:
    registry = StrategyDefinitionRegistry()
    registry.register(EntryThenLadderExitStrategyDefinition())
    registry.register(FixedEqualUsdtGridStrategyDefinition())
    return registry
