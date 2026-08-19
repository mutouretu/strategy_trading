"""Resolved immutable strategy composition contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..kernel import TradingRuleSpec
from ..kernel._values import freeze_mapping

from .values import (
    CapitalAllocationSpec,
    InstrumentBinding,
    RulePermission,
    StrategyCoordinationPolicy,
    required_text,
)


@dataclass(frozen=True, slots=True)
class StrategyRuleSlotSpec:
    rule_key: str
    rule: TradingRuleSpec
    permissions: tuple[RulePermission, ...]
    subscriptions: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "rule_key",
            required_text("rule_key", self.rule_key),
        )
        if not isinstance(self.rule, TradingRuleSpec):
            raise TypeError("rule must be a TradingRuleSpec")
        permissions = tuple(self.permissions)
        if any(not isinstance(item, RulePermission) for item in permissions):
            raise TypeError("permissions must contain RulePermission values")
        if len(set(permissions)) != len(permissions):
            raise ValueError("permissions must not contain duplicates")
        object.__setattr__(self, "permissions", permissions)
        subscriptions = tuple(
            required_text("subscription", item) for item in self.subscriptions
        )
        if not subscriptions:
            raise ValueError("subscriptions must not be empty")
        if len(set(subscriptions)) != len(subscriptions):
            raise ValueError("subscriptions must not contain duplicates")
        object.__setattr__(self, "subscriptions", subscriptions)


@dataclass(frozen=True, slots=True)
class StrategySpec:
    strategy_spec_id: str
    strategy_type: str
    display_name: str
    parameters: Mapping[str, object]
    binding: InstrumentBinding
    rule_slots: tuple[StrategyRuleSlotSpec, ...]
    coordination_policy: StrategyCoordinationPolicy
    capital_allocation: CapitalAllocationSpec | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "strategy_spec_id",
            "strategy_type",
            "display_name",
        ):
            object.__setattr__(
                self,
                field_name,
                required_text(field_name, getattr(self, field_name)),
            )
        object.__setattr__(self, "parameters", freeze_mapping(self.parameters))
        if not isinstance(self.binding, InstrumentBinding):
            raise TypeError("binding must be an InstrumentBinding")
        slots = tuple(self.rule_slots)
        if not slots:
            raise ValueError("rule_slots must contain at least one Rule")
        if any(not isinstance(slot, StrategyRuleSlotSpec) for slot in slots):
            raise TypeError("rule_slots must contain StrategyRuleSlotSpec values")
        keys = [slot.rule_key for slot in slots]
        if len(set(keys)) != len(keys):
            raise ValueError("rule keys must be unique within a StrategySpec")
        object.__setattr__(self, "rule_slots", slots)
        if not isinstance(
            self.coordination_policy,
            StrategyCoordinationPolicy,
        ):
            raise TypeError(
                "coordination_policy must be a StrategyCoordinationPolicy"
            )
        if self.capital_allocation is not None and not isinstance(
            self.capital_allocation,
            CapitalAllocationSpec,
        ):
            raise TypeError(
                "capital_allocation must be a CapitalAllocationSpec or None"
            )
        self._validate_exit_controller(slots)

    def _validate_exit_controller(
        self,
        slots: tuple[StrategyRuleSlotSpec, ...],
    ) -> None:
        managers = [
            slot.rule_key
            for slot in slots
            if RulePermission.MANAGE_EXITS in slot.permissions
        ]
        controller = self.coordination_policy.exit_controller_rule_key
        if controller is None:
            if managers:
                raise ValueError(
                    "MANAGE_EXITS requires exit_controller_rule_key"
                )
            return
        if controller not in {slot.rule_key for slot in slots}:
            raise ValueError("exit_controller_rule_key must identify a RuleSlot")
        if managers != [controller]:
            raise ValueError(
                "exactly the exit controller must have MANAGE_EXITS"
            )

    def slot(self, rule_key: str) -> StrategyRuleSlotSpec:
        for slot in self.rule_slots:
            if slot.rule_key == rule_key:
                return slot
        raise ValueError(f"rule slot {rule_key!r} is not defined")
