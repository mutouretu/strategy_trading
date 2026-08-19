"""Built-in StrategyDefinition catalog independent of experiment storage."""

from __future__ import annotations

from .entry_then_ladder_exit import EntryThenLadderExitStrategyDefinition
from .strategy import StrategyDefinitionRegistry


def build_strategy_definition_registry() -> StrategyDefinitionRegistry:
    registry = StrategyDefinitionRegistry()
    registry.register(EntryThenLadderExitStrategyDefinition())
    return registry
