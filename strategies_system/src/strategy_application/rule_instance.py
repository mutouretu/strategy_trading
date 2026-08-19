"""Runtime state holder for one rule slot in one StrategyInstance."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from trading_strategies.kernel import (
    RuleLifecycleRequest,
    RuleTransition,
    TradingRule,
)
from trading_strategies.strategy import StrategyRuleSlotSpec


class RuleInstanceLifecycle(StrEnum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"


@dataclass(slots=True)
class TradingRuleInstance:
    """Binds a stateless Rule implementation to config, context and state."""

    rule_instance_id: str
    slot: StrategyRuleSlotSpec
    rule: TradingRule
    context: object
    state: object
    lifecycle: RuleInstanceLifecycle = RuleInstanceLifecycle.ACTIVE
    last_event_sequence: int | None = None

    @classmethod
    def create(
        cls,
        *,
        strategy_instance_id: str,
        slot: StrategyRuleSlotSpec,
        rule: TradingRule,
        context: object,
    ) -> "TradingRuleInstance":
        if rule.rule_type != slot.rule.rule_type:
            raise ValueError(
                f"Rule {rule.rule_type!r} does not match slot "
                f"type {slot.rule.rule_type!r}"
            )
        definition = rule.definition()
        if definition.rule_type != rule.rule_type:
            raise ValueError("Rule definition type must match rule.rule_type")
        unknown_subscriptions = set(slot.subscriptions) - set(
            definition.input_types
        )
        if unknown_subscriptions:
            raise ValueError(
                "RuleSlot subscribes to inputs not declared by the Rule: "
                + ", ".join(sorted(unknown_subscriptions))
            )
        state = rule.initial_state(slot.rule.config, context)
        return cls(
            rule_instance_id=(
                f"{strategy_instance_id}:{slot.rule_key}"
            ),
            slot=slot,
            rule=rule,
            context=context,
            state=state,
        )

    def preview(self, input_value: object) -> RuleTransition[Any]:
        if self.lifecycle != RuleInstanceLifecycle.ACTIVE:
            raise RuntimeError(
                f"RuleInstance {self.rule_instance_id!r} is not active"
            )
        input_type = type(input_value).__name__
        if input_type not in self.slot.subscriptions:
            raise TypeError(
                f"RuleSlot {self.slot.rule_key!r} does not subscribe to "
                f"{input_type!r}"
            )
        transition = self.rule.transition(
            self.slot.rule.config,
            self.state,
            input_value,
            self.context,
        )
        if not isinstance(transition, RuleTransition):
            raise TypeError("TradingRule.transition must return RuleTransition")
        return transition

    def commit(
        self,
        transition: RuleTransition[Any],
        *,
        event_sequence: int,
    ) -> None:
        if event_sequence < 1:
            raise ValueError("event_sequence must be >= 1")
        if (
            self.last_event_sequence is not None
            and event_sequence <= self.last_event_sequence
        ):
            raise ValueError("Rule events must commit in increasing order")
        self.state = transition.next_state
        self.last_event_sequence = event_sequence
        if transition.lifecycle_request == RuleLifecycleRequest.COMPLETE:
            self.lifecycle = RuleInstanceLifecycle.COMPLETED
        elif transition.lifecycle_request == RuleLifecycleRequest.STOP:
            self.lifecycle = RuleInstanceLifecycle.STOPPED
