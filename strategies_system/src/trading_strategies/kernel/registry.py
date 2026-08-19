"""Explicit registry for pure trading-rule implementations."""

from __future__ import annotations

from .definition import TradingRuleDefinition
from .rule import TradingRule


class TradingRuleRegistry:
    def __init__(self) -> None:
        self._rules: dict[str, TradingRule] = {}
        self._aliases: dict[str, str] = {}

    def register(self, rule: TradingRule) -> None:
        if not isinstance(rule, TradingRule):
            raise TypeError("rule must be a TradingRule instance")
        rule_type = getattr(rule, "rule_type", None)
        if not isinstance(rule_type, str) or not rule_type.strip():
            raise ValueError("trading rule requires a non-empty rule_type")
        definition = rule.definition()
        if not isinstance(definition, TradingRuleDefinition):
            raise TypeError(
                "trading rule definition must be a TradingRuleDefinition"
            )
        if definition.rule_type != rule_type:
            raise ValueError("definition rule_type must match rule.rule_type")

        claimed_names = {rule_type, *definition.aliases}
        existing_names = set(self._rules) | set(self._aliases)
        collisions = claimed_names & existing_names
        if collisions:
            raise ValueError(
                "trading rule names are already registered: "
                + ", ".join(sorted(collisions))
            )
        if len(claimed_names) != 1 + len(definition.aliases):
            raise ValueError("trading rule aliases must be unique")

        self._rules[rule_type] = rule
        for alias in definition.aliases:
            self._aliases[alias] = rule_type

    def canonical_type(self, rule_type: str) -> str:
        if rule_type in self._rules:
            return rule_type
        try:
            return self._aliases[rule_type]
        except KeyError as exc:
            raise ValueError(f"trading rule {rule_type!r} is not registered") from exc

    def get(self, rule_type: str) -> TradingRule:
        return self._rules[self.canonical_type(rule_type)]

    @property
    def definitions(self) -> tuple[TradingRuleDefinition, ...]:
        return tuple(
            self._rules[rule_type].definition()
            for rule_type in sorted(self._rules)
        )

    @property
    def rule_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._rules))
