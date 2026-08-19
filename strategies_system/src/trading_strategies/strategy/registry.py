"""Explicit registry for pure Strategy definitions."""

from __future__ import annotations

from .definition import StrategyDefinition, StrategyDefinitionDescriptor


class StrategyDefinitionRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, StrategyDefinition] = {}
        self._aliases: dict[str, str] = {}

    def register(self, strategy: StrategyDefinition) -> None:
        if not isinstance(strategy, StrategyDefinition):
            raise TypeError("strategy must be a StrategyDefinition instance")
        strategy_type = getattr(strategy, "strategy_type", None)
        if not isinstance(strategy_type, str) or not strategy_type.strip():
            raise ValueError("StrategyDefinition requires a strategy_type")
        descriptor = strategy.definition()
        if not isinstance(descriptor, StrategyDefinitionDescriptor):
            raise TypeError(
                "Strategy definition must be a StrategyDefinitionDescriptor"
            )
        if descriptor.strategy_type != strategy_type:
            raise ValueError(
                "descriptor strategy_type must match strategy.strategy_type"
            )
        claimed_names = {strategy_type, *descriptor.aliases}
        existing_names = set(self._definitions) | set(self._aliases)
        collisions = claimed_names & existing_names
        if collisions:
            raise ValueError(
                "Strategy names are already registered: "
                + ", ".join(sorted(collisions))
            )
        if len(claimed_names) != 1 + len(descriptor.aliases):
            raise ValueError("Strategy aliases must be unique")
        self._definitions[strategy_type] = strategy
        for alias in descriptor.aliases:
            self._aliases[alias] = strategy_type

    def canonical_type(self, strategy_type: str) -> str:
        if strategy_type in self._definitions:
            return strategy_type
        try:
            return self._aliases[strategy_type]
        except KeyError as exc:
            raise ValueError(
                f"StrategyDefinition {strategy_type!r} is not registered"
            ) from exc

    def get(self, strategy_type: str) -> StrategyDefinition:
        return self._definitions[self.canonical_type(strategy_type)]

    @property
    def definitions(self) -> tuple[StrategyDefinitionDescriptor, ...]:
        return tuple(
            self._definitions[strategy_type].definition()
            for strategy_type in sorted(self._definitions)
        )

    @property
    def strategy_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))
