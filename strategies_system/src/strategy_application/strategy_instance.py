"""Atomic coordination of the Rule instances inside one Strategy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from trading_strategies.kernel import (
    PositionEffect,
    RuleIntentProposal,
    RuleTransition,
    TradeSide,
    TradingRule,
)
from trading_strategies.strategy import (
    CapitalAllocationSpec,
    ConflictResolution,
    ProposalBatchMode,
    RulePermission,
    StrategyDirection,
    StrategySpec,
)

from .rule_instance import RuleInstanceLifecycle, TradingRuleInstance


class StrategyInstanceLifecycle(StrEnum):
    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class IntentOwner:
    rule_key: str
    local_intent_key: str


@dataclass(frozen=True, slots=True)
class StrategyEventResult:
    """Approved, globally identified outputs from one atomic event."""

    event_sequence: int
    intents: tuple[RuleIntentProposal, ...] = ()
    cancellations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StrategyEventPreview:
    """Validated Strategy proposal that has not mutated runtime state."""

    strategy_instance_id: str
    base_event_sequence: int
    transitions: tuple[tuple[str, RuleTransition], ...]
    intents: tuple[RuleIntentProposal, ...]
    cancellations: tuple[str, ...]
    consumed_intent_keys: tuple[str, ...]


class StrategyInstance:
    """One stateful run of an immutable StrategySpec."""

    def __init__(
        self,
        *,
        strategy_instance_id: str,
        spec: StrategySpec,
        rules: Mapping[str, TradingRule],
        contexts: Mapping[str, object],
        capital_allocation: CapitalAllocationSpec | None = None,
    ) -> None:
        if (
            not isinstance(strategy_instance_id, str)
            or not strategy_instance_id.strip()
        ):
            raise ValueError("strategy_instance_id must be a non-empty string")
        if not isinstance(spec, StrategySpec):
            raise TypeError("spec must be a StrategySpec")
        expected = {slot.rule_key for slot in spec.rule_slots}
        if set(rules) != expected:
            raise ValueError("rules must match StrategySpec rule slots exactly")
        if set(contexts) != expected:
            raise ValueError("contexts must match StrategySpec rule slots exactly")

        self.strategy_instance_id = strategy_instance_id.strip()
        self.spec = spec
        self.position_owner_id = f"{self.strategy_instance_id}:position"
        resolved_allocation = capital_allocation or spec.capital_allocation
        if resolved_allocation is not None and not isinstance(
            resolved_allocation,
            CapitalAllocationSpec,
        ):
            raise TypeError(
                "capital_allocation must be a CapitalAllocationSpec or None"
            )
        self.capital_allocation = resolved_allocation
        self.allocation_id = (
            f"{self.strategy_instance_id}:allocation"
            if resolved_allocation is not None
            else None
        )
        self._rule_instances = {
            slot.rule_key: TradingRuleInstance.create(
                strategy_instance_id=self.strategy_instance_id,
                slot=slot,
                rule=rules[slot.rule_key],
                context=contexts[slot.rule_key],
            )
            for slot in spec.rule_slots
        }
        self._active_intents: dict[str, RuleIntentProposal] = {}
        self._intent_owners: dict[str, IntentOwner] = {}
        self._event_sequence = 0
        self.lifecycle = StrategyInstanceLifecycle.CREATED

    @property
    def rule_instances(self) -> Mapping[str, TradingRuleInstance]:
        return MappingProxyType(self._rule_instances)

    @property
    def active_intents(self) -> tuple[RuleIntentProposal, ...]:
        return tuple(
            self._active_intents[key] for key in sorted(self._active_intents)
        )

    @property
    def event_sequence(self) -> int:
        return self._event_sequence

    def start(self) -> None:
        if self.lifecycle != StrategyInstanceLifecycle.CREATED:
            raise RuntimeError("StrategyInstance can be started only once")
        self.lifecycle = StrategyInstanceLifecycle.ACTIVE

    def fail(self) -> None:
        if self.lifecycle in {
            StrategyInstanceLifecycle.COMPLETED,
            StrategyInstanceLifecycle.STOPPED,
        }:
            raise RuntimeError("a terminal StrategyInstance cannot fail")
        self.lifecycle = StrategyInstanceLifecycle.FAILED

    def rule_state(self, rule_key: str) -> object:
        return self._instance(rule_key).state

    def owner_for_intent(self, global_intent_key: str) -> IntentOwner:
        try:
            return self._intent_owners[global_intent_key]
        except KeyError as exc:
            raise ValueError(
                f"intent {global_intent_key!r} is not active"
            ) from exc

    def global_intent_key(self, rule_key: str, local_intent_key: str) -> str:
        return f"{self.strategy_instance_id}:{rule_key}:{local_intent_key}"

    def apply_event(
        self,
        inputs: Mapping[str, object],
        *,
        consumed_intent_keys: tuple[str, ...] = (),
    ) -> StrategyEventResult:
        """Preview, validate and atomically commit one routed domain event."""

        preview = self.preview_event(
            inputs,
            consumed_intent_keys=consumed_intent_keys,
        )
        return self.commit_event(preview)

    def preview_event(
        self,
        inputs: Mapping[str, object],
        *,
        consumed_intent_keys: tuple[str, ...] = (),
    ) -> StrategyEventPreview:
        """Produce an immutable proposal without changing Strategy state."""

        if self.lifecycle != StrategyInstanceLifecycle.ACTIVE:
            raise RuntimeError("StrategyInstance is not active")
        if not inputs:
            raise ValueError("inputs must route the event to at least one Rule")
        unknown_rules = set(inputs) - set(self._rule_instances)
        if unknown_rules:
            raise ValueError(
                "event references unknown Rule slots: "
                + ", ".join(sorted(unknown_rules))
            )

        consumed = tuple(consumed_intent_keys)
        if len(set(consumed)) != len(consumed):
            raise ValueError("consumed intent keys must be unique")
        consumed_owners = {
            key: self.owner_for_intent(key) for key in consumed
        }
        routed_rules = set(inputs)
        if any(
            owner.rule_key not in routed_rules
            for owner in consumed_owners.values()
        ):
            raise ValueError(
                "a consumed intent must route its event to the owning Rule"
            )

        previews = tuple(
            (
                rule_key,
                self._rule_instances[rule_key].preview(inputs[rule_key]),
            )
            for rule_key in sorted(inputs)
        )
        approved_intents, approved_cancellations = self._validate_previews(
            previews,
            consumed,
        )
        return StrategyEventPreview(
            strategy_instance_id=self.strategy_instance_id,
            base_event_sequence=self._event_sequence,
            transitions=previews,
            intents=approved_intents,
            cancellations=approved_cancellations,
            consumed_intent_keys=consumed,
        )

    def commit_event(
        self,
        preview: StrategyEventPreview,
    ) -> StrategyEventResult:
        """Commit a previously validated proposal."""

        if self.lifecycle != StrategyInstanceLifecycle.ACTIVE:
            raise RuntimeError("StrategyInstance is not active")
        if not isinstance(preview, StrategyEventPreview):
            raise TypeError("preview must be a StrategyEventPreview")
        if preview.strategy_instance_id != self.strategy_instance_id:
            raise ValueError("preview belongs to another StrategyInstance")
        if preview.base_event_sequence != self._event_sequence:
            raise RuntimeError("Strategy state changed after event preview")

        event_sequence = self._event_sequence + 1
        for rule_key, transition in preview.transitions:
            self._rule_instances[rule_key].commit(
                transition,
                event_sequence=event_sequence,
            )
        for intent_key in (
            *preview.consumed_intent_keys,
            *preview.cancellations,
        ):
            self._active_intents.pop(intent_key, None)
            self._intent_owners.pop(intent_key, None)
        for intent in preview.intents:
            owner = IntentOwner(
                rule_key=str(intent.tags["rule_key"]),
                local_intent_key=str(intent.tags["local_intent_key"]),
            )
            self._active_intents[intent.intent_key] = intent
            self._intent_owners[intent.intent_key] = owner
        self._event_sequence = event_sequence
        self._update_lifecycle()
        return StrategyEventResult(
            event_sequence=event_sequence,
            intents=preview.intents,
            cancellations=preview.cancellations,
        )

    def _validate_previews(
        self,
        previews: tuple[tuple[str, RuleTransition], ...],
        consumed: tuple[str, ...],
    ) -> tuple[tuple[RuleIntentProposal, ...], tuple[str, ...]]:
        policy = self.spec.coordination_policy
        if policy.proposal_batch != ProposalBatchMode.ATOMIC_PER_EVENT:
            raise ValueError("unsupported proposal batch mode")
        if policy.conflict_resolution != ConflictResolution.FAIL_FAST:
            raise ValueError("unsupported conflict resolution policy")

        intents: list[RuleIntentProposal] = []
        cancellations: list[str] = []
        for rule_key, transition in previews:
            slot = self.spec.slot(rule_key)
            for proposal in transition.intents:
                self._validate_permission(slot.permissions, rule_key, proposal)
                self._validate_direction(proposal)
                intents.append(self._qualify_intent(rule_key, proposal))
            cancellations.extend(
                self.global_intent_key(rule_key, key)
                for key in transition.cancellations
            )

        global_keys = [intent.intent_key for intent in intents]
        if len(set(global_keys)) != len(global_keys):
            raise ValueError("Strategy event produced duplicate intent keys")
        if len(set(cancellations)) != len(cancellations):
            raise ValueError("Strategy event produced duplicate cancellations")

        available = set(self._active_intents) - set(consumed)
        unknown_cancellations = set(cancellations) - available
        if unknown_cancellations:
            raise ValueError(
                "Rule tried to cancel inactive intents: "
                + ", ".join(sorted(unknown_cancellations))
            )
        surviving = available - set(cancellations)
        collisions = surviving & set(global_keys)
        if collisions:
            raise ValueError(
                "Strategy event reused active intent keys: "
                + ", ".join(sorted(collisions))
            )

        effects = {intent.position_effect for intent in intents}
        opening = effects & {PositionEffect.OPEN, PositionEffect.INCREASE}
        reducing = effects & {PositionEffect.REDUCE, PositionEffect.CLOSE}
        if opening and reducing:
            raise ValueError(
                "one Strategy event cannot both increase and reduce exposure"
            )
        return tuple(intents), tuple(cancellations)

    def _validate_permission(
        self,
        permissions: tuple[RulePermission, ...],
        rule_key: str,
        proposal: RuleIntentProposal,
    ) -> None:
        required = RulePermission(proposal.position_effect.value)
        if required not in permissions:
            raise PermissionError(
                f"RuleSlot {rule_key!r} lacks {required.value} permission"
            )
        if proposal.position_effect in {
            PositionEffect.REDUCE,
            PositionEffect.CLOSE,
        }:
            controller = (
                self.spec.coordination_policy.exit_controller_rule_key
            )
            if (
                rule_key != controller
                or RulePermission.MANAGE_EXITS not in permissions
            ):
                raise PermissionError(
                    "only the MANAGE_EXITS controller may reduce or close"
                )

    def _validate_direction(self, proposal: RuleIntentProposal) -> None:
        direction = self.spec.binding.direction
        opening_side = (
            TradeSide.BUY
            if direction == StrategyDirection.LONG
            else TradeSide.SELL
        )
        expected = (
            opening_side
            if proposal.position_effect
            in {PositionEffect.OPEN, PositionEffect.INCREASE}
            else (
                TradeSide.SELL
                if opening_side == TradeSide.BUY
                else TradeSide.BUY
            )
        )
        if proposal.side != expected:
            raise ValueError(
                "Rule intent side conflicts with the Strategy direction"
            )

    def _qualify_intent(
        self,
        rule_key: str,
        proposal: RuleIntentProposal,
    ) -> RuleIntentProposal:
        instance = self._instance(rule_key)
        tags = {
            **proposal.tags,
            "strategy_instance_id": self.strategy_instance_id,
            "strategy_spec_id": self.spec.strategy_spec_id,
            "strategy_type": self.spec.strategy_type,
            "instrument": self.spec.binding.instrument,
            "strategy_direction": self.spec.binding.direction.value,
            "product_type": self.spec.binding.product_type.value,
            "rule_key": rule_key,
            "rule_type": instance.rule.rule_type,
            "rule_instance_id": instance.rule_instance_id,
            "local_intent_key": proposal.intent_key,
            "position_owner_id": self.position_owner_id,
        }
        if self.allocation_id is not None:
            tags["allocation_id"] = self.allocation_id
        return RuleIntentProposal(
            intent_key=self.global_intent_key(rule_key, proposal.intent_key),
            side=proposal.side,
            quantity=proposal.quantity,
            quantity_unit=proposal.quantity_unit,
            position_effect=proposal.position_effect,
            intent_mode=proposal.intent_mode,
            target_price=proposal.target_price,
            reduce_only=proposal.reduce_only,
            role=proposal.role,
            tags=tags,
        )

    def _instance(self, rule_key: str) -> TradingRuleInstance:
        try:
            return self._rule_instances[rule_key]
        except KeyError as exc:
            raise ValueError(f"unknown Rule slot: {rule_key!r}") from exc

    def _update_lifecycle(self) -> None:
        lifecycles = {
            instance.lifecycle for instance in self._rule_instances.values()
        }
        if lifecycles <= {RuleInstanceLifecycle.COMPLETED}:
            self.lifecycle = StrategyInstanceLifecycle.COMPLETED
        elif RuleInstanceLifecycle.STOPPED in lifecycles and not self._active_intents:
            self.lifecycle = StrategyInstanceLifecycle.STOPPED
