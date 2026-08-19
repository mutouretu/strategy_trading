"""Deterministic multi-Strategy application host."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

from trading_strategies.kernel import (
    PositionEffect,
    RuleIntentProposal,
    TradingRule,
)

from .allocation import CapitalAllocation
from .accounting import (
    AttributionReconciliation,
    AuthoritativeAccountSnapshot,
    PositionBookFillPreview,
    StrategyAccountingFill,
    StrategyAttributionSnapshot,
    StrategyFundingAllocation,
    StrategyFundingSettlement,
    VirtualPositionBook,
)
from .spec import StrategyApplicationSpec, StrategyLaunchSpec
from .strategy_instance import (
    StrategyEventResult,
    StrategyEventPreview,
    StrategyInstance,
    StrategyInstanceLifecycle,
)


class StrategyApplicationLifecycle(StrEnum):
    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class StrategyRuntimeBinding:
    """Per-launch Rule algorithms and calculation contexts."""

    rules: Mapping[str, TradingRule]
    contexts: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "rules",
            MappingProxyType(dict(self.rules)),
        )
        object.__setattr__(
            self,
            "contexts",
            MappingProxyType(dict(self.contexts)),
        )


StrategyRuntimeFactory = Callable[
    [StrategyLaunchSpec],
    StrategyRuntimeBinding,
]


@dataclass(frozen=True, slots=True)
class ApplicationIntentOwner:
    strategy_instance_id: str
    rule_instance_id: str
    rule_key: str
    local_intent_key: str


@dataclass(frozen=True, slots=True)
class ApplicationEventResult:
    application_event_sequence: int
    strategy_instance_id: str
    strategy_result: StrategyEventResult


@dataclass(frozen=True, slots=True)
class ApplicationBatchResult:
    """Results committed atomically for one cross-Strategy event."""

    application_event_sequence: int
    strategy_results: tuple[ApplicationEventResult, ...]


@dataclass(frozen=True, slots=True)
class _ApplicationStrategyPreview:
    strategy_instance_id: str
    preview: StrategyEventPreview


@dataclass(frozen=True, slots=True)
class StrategyInstanceActivity:
    strategy_instance_id: str
    application_event_count: int
    issued_intent_count: int
    consumed_fill_count: int
    active_intent_count: int
    lifecycle: StrategyInstanceLifecycle


class StrategyApplication:
    """Hosts multiple isolated Strategy instances in one application."""

    def __init__(
        self,
        *,
        spec: StrategyApplicationSpec,
        instances: Mapping[str, StrategyInstance],
    ) -> None:
        if not isinstance(spec, StrategyApplicationSpec):
            raise TypeError("spec must be a StrategyApplicationSpec")
        expected = {
            launch.strategy_instance_id for launch in spec.strategy_launches
        }
        if set(instances) != expected:
            raise ValueError(
                "instances must match Application launch IDs exactly"
            )
        ordered_instances = {
            key: instances[key] for key in sorted(instances)
        }
        for launch in spec.strategy_launches:
            instance = ordered_instances[launch.strategy_instance_id]
            if not isinstance(instance, StrategyInstance):
                raise TypeError("instances must contain StrategyInstance values")
            if instance.strategy_instance_id != launch.strategy_instance_id:
                raise ValueError("StrategyInstance ID does not match its launch")
            if instance.spec != launch.strategy_spec:
                raise ValueError("StrategyInstance spec does not match its launch")
            if instance.lifecycle != StrategyInstanceLifecycle.CREATED:
                raise ValueError(
                    "StrategyInstance must be CREATED when registered"
                )
            if (
                instance.capital_allocation
                != launch.resolved_capital_allocation
            ):
                raise ValueError(
                    "StrategyInstance allocation does not match its launch"
                )

        self.spec = spec
        self.application_id = spec.application_id
        self._instances = ordered_instances
        self._allocations = {
            instance.allocation_id: CapitalAllocation.from_spec(
                strategy_instance_id=instance.strategy_instance_id,
                spec=instance.capital_allocation,
            )
            for instance in ordered_instances.values()
            if instance.allocation_id is not None
            and instance.capital_allocation is not None
        }
        self._intent_index: dict[str, ApplicationIntentOwner] = {}
        self._active_intents: dict[str, RuleIntentProposal] = {}
        self._event_counts = {key: 0 for key in ordered_instances}
        self._issued_intent_counts = {key: 0 for key in ordered_instances}
        self._fill_counts = {key: 0 for key in ordered_instances}
        self._event_sequence = 0
        self.lifecycle = StrategyApplicationLifecycle.CREATED
        self._validate_unique_resources()
        self._position_book = VirtualPositionBook(self._allocations)

    @classmethod
    def build(
        cls,
        spec: StrategyApplicationSpec,
        runtime_factory: StrategyRuntimeFactory,
    ) -> "StrategyApplication":
        if not isinstance(spec, StrategyApplicationSpec):
            raise TypeError("spec must be a StrategyApplicationSpec")
        instances: dict[str, StrategyInstance] = {}
        for launch in spec.strategy_launches:
            binding = runtime_factory(launch)
            if not isinstance(binding, StrategyRuntimeBinding):
                raise TypeError(
                    "runtime_factory must return StrategyRuntimeBinding"
                )
            instances[launch.strategy_instance_id] = StrategyInstance(
                strategy_instance_id=launch.strategy_instance_id,
                spec=launch.strategy_spec,
                rules=binding.rules,
                contexts=binding.contexts,
                capital_allocation=launch.resolved_capital_allocation,
            )
        return cls(spec=spec, instances=instances)

    @property
    def instances(self) -> Mapping[str, StrategyInstance]:
        return MappingProxyType(self._instances)

    @property
    def allocations(self) -> Mapping[str, CapitalAllocation]:
        return MappingProxyType(self._allocations)

    @property
    def position_owner_ids(self) -> tuple[str, ...]:
        return tuple(
            instance.position_owner_id
            for instance in self._instances.values()
        )

    @property
    def position_book(self) -> VirtualPositionBook:
        return self._position_book

    @property
    def active_intents(self) -> tuple[RuleIntentProposal, ...]:
        return tuple(
            self._active_intents[key] for key in sorted(self._active_intents)
        )

    @property
    def event_sequence(self) -> int:
        return self._event_sequence

    def start(self) -> None:
        if self.lifecycle != StrategyApplicationLifecycle.CREATED:
            raise RuntimeError("StrategyApplication can be started only once")
        try:
            for instance in self._instances.values():
                instance.start()
        except Exception:
            self._fail_application()
            raise
        self.lifecycle = StrategyApplicationLifecycle.ACTIVE

    def dispatch(
        self,
        strategy_instance_id: str,
        inputs: Mapping[str, object],
    ) -> ApplicationEventResult:
        """Route a non-Fill domain event to exactly one StrategyInstance."""

        return self.dispatch_batch(
            {strategy_instance_id: inputs}
        ).strategy_results[0]

    def dispatch_batch(
        self,
        inputs_by_strategy: Mapping[str, Mapping[str, object]],
    ) -> ApplicationBatchResult:
        """Preview, approve and commit one event across many Strategies."""

        self._require_active()
        if not inputs_by_strategy:
            raise ValueError(
                "inputs_by_strategy must target at least one StrategyInstance"
            )
        unknown = set(inputs_by_strategy) - set(self._instances)
        if unknown:
            raise ValueError(
                "event references unknown Strategy instances: "
                + ", ".join(sorted(unknown))
            )
        previews: tuple[_ApplicationStrategyPreview, ...]
        try:
            previews = tuple(
                _ApplicationStrategyPreview(
                    strategy_instance_id=instance_id,
                    preview=self._instances[instance_id].preview_event(
                        inputs_by_strategy[instance_id]
                    ),
                )
                for instance_id in sorted(inputs_by_strategy)
            )
            self._validate_application_previews(previews)
            results = tuple(
                self._commit_preview(item)
                for item in previews
            )
        except Exception:
            self._fail_application()
            raise
        self._event_sequence += 1
        self._update_lifecycle()
        return ApplicationBatchResult(
            application_event_sequence=self._event_sequence,
            strategy_results=results,
        )

    def route_fill(
        self,
        global_intent_key: str,
        inputs: Mapping[str, object],
        *,
        accounting_fill: StrategyAccountingFill | None = None,
    ) -> ApplicationEventResult:
        """Resolve a Fill through the intent index; never broadcast it."""

        self._require_active()
        try:
            owner = self._intent_index[global_intent_key]
        except KeyError as exc:
            self._fail_application()
            raise ValueError(
                f"Fill references unknown intent {global_intent_key!r}"
            ) from exc
        if owner.rule_key not in inputs:
            self._fail_application()
            raise ValueError("Fill inputs must include the intent-owning Rule")
        accounting_preview = None
        if accounting_fill is not None:
            instance = self._instances[owner.strategy_instance_id]
            try:
                accounting_preview = self._position_book.preview_fill(
                    fill=accounting_fill,
                    intent=self._active_intents[global_intent_key],
                    strategy_instance_id=owner.strategy_instance_id,
                    position_owner_id=instance.position_owner_id,
                    product_type=instance.spec.binding.product_type,
                )
            except Exception:
                self._fail_application()
                raise
        return self._apply(
            strategy_instance_id=owner.strategy_instance_id,
            inputs=inputs,
            consumed_intent_key=global_intent_key,
            accounting_preview=accounting_preview,
        )

    def attribute_funding(
        self,
        settlement: StrategyFundingSettlement,
    ) -> tuple[StrategyFundingAllocation, ...]:
        self._require_active()
        try:
            return self._position_book.allocate_funding(settlement)
        except Exception:
            self._fail_application()
            raise

    def attribution_snapshot(
        self,
        strategy_instance_id: str,
        marks: Mapping[str, Decimal],
    ) -> StrategyAttributionSnapshot:
        return self._position_book.snapshot(
            strategy_instance_id,
            marks,
        )

    def reconcile_attribution(
        self,
        account: AuthoritativeAccountSnapshot,
        marks: Mapping[str, Decimal],
    ) -> AttributionReconciliation:
        return self._position_book.reconcile(account, marks)

    def resolve_intent(self, global_intent_key: str) -> ApplicationIntentOwner:
        try:
            return self._intent_index[global_intent_key]
        except KeyError as exc:
            raise ValueError(
                f"intent {global_intent_key!r} is not active"
            ) from exc

    def activity(
        self,
        strategy_instance_id: str,
    ) -> StrategyInstanceActivity:
        instance = self._instance(strategy_instance_id)
        return StrategyInstanceActivity(
            strategy_instance_id=strategy_instance_id,
            application_event_count=self._event_counts[strategy_instance_id],
            issued_intent_count=(
                self._issued_intent_counts[strategy_instance_id]
            ),
            consumed_fill_count=self._fill_counts[strategy_instance_id],
            active_intent_count=sum(
                1
                for owner in self._intent_index.values()
                if owner.strategy_instance_id == strategy_instance_id
            ),
            lifecycle=instance.lifecycle,
        )

    def _apply(
        self,
        *,
        strategy_instance_id: str,
        inputs: Mapping[str, object],
        consumed_intent_key: str | None,
        accounting_preview: PositionBookFillPreview | None = None,
    ) -> ApplicationEventResult:
        self._require_active()
        instance = self._instance(strategy_instance_id)
        try:
            preview = instance.preview_event(
                inputs,
                consumed_intent_keys=(
                    ()
                    if consumed_intent_key is None
                    else (consumed_intent_key,)
                ),
            )
            item = _ApplicationStrategyPreview(
                strategy_instance_id=strategy_instance_id,
                preview=preview,
            )
            self._validate_application_previews(
                (item,),
                accounting_previews=(
                    ()
                    if accounting_preview is None
                    else (accounting_preview,)
                ),
            )
            result = self._commit_preview(item)
            if accounting_preview is not None:
                self._position_book.commit_fill(accounting_preview)
        except Exception:
            self._fail_application()
            raise
        self._event_sequence += 1
        self._update_lifecycle()
        return result

    def _commit_preview(
        self,
        item: _ApplicationStrategyPreview,
    ) -> ApplicationEventResult:
        result = self._instances[item.strategy_instance_id].commit_event(
            item.preview
        )
        self._record_result(
            item.strategy_instance_id,
            result,
            consumed_intent_keys=item.preview.consumed_intent_keys,
        )
        self._event_counts[item.strategy_instance_id] += 1
        self._issued_intent_counts[item.strategy_instance_id] += len(
            result.intents
        )
        self._fill_counts[item.strategy_instance_id] += len(
            item.preview.consumed_intent_keys
        )
        return ApplicationEventResult(
            application_event_sequence=self._event_sequence + 1,
            strategy_instance_id=item.strategy_instance_id,
            strategy_result=result,
        )

    def _validate_application_previews(
        self,
        previews: tuple[_ApplicationStrategyPreview, ...],
        *,
        accounting_previews: tuple[PositionBookFillPreview, ...] = (),
    ) -> None:
        intents = tuple(
            intent
            for item in previews
            for intent in item.preview.intents
        )
        intent_keys = [intent.intent_key for intent in intents]
        if len(set(intent_keys)) != len(intent_keys):
            raise ValueError(
                "Application event produced duplicate global intent keys"
            )
        removals = {
            key
            for item in previews
            for key in (
                *item.preview.consumed_intent_keys,
                *item.preview.cancellations,
            )
        }
        surviving_active = set(self._intent_index) - removals
        collisions = set(intent_keys) & surviving_active
        if collisions:
            raise ValueError(
                "Application event reused active intent keys: "
                + ", ".join(sorted(collisions))
            )
        for item in previews:
            for intent in item.preview.intents:
                if (
                    intent.tags.get("strategy_instance_id")
                    != item.strategy_instance_id
                ):
                    raise ValueError(
                        "intent Strategy identity does not match route"
                    )
        considered_intents = tuple(
            self._active_intents[key] for key in sorted(surviving_active)
        ) + intents
        if self.spec.coordination_policy.reject_opposing_open_intents:
            opening = {
                (
                    str(intent.tags["instrument"]),
                    str(intent.tags["product_type"]),
                    str(intent.tags["strategy_direction"]),
                )
                for intent in considered_intents
                if intent.position_effect
                in {PositionEffect.OPEN, PositionEffect.INCREASE}
            }
            directions_by_product: dict[tuple[str, str], set[str]] = {}
            for instrument, product_type, direction in opening:
                directions_by_product.setdefault(
                    (instrument, product_type),
                    set(),
                ).add(direction)
            conflicts = [
                f"{instrument}/{product_type}"
                for (instrument, product_type), directions
                in directions_by_product.items()
                if len(directions) > 1
            ]
            if conflicts:
                raise ValueError(
                    "opposing opening intents conflict under the default "
                    "single-direction account policy: "
                    + ", ".join(sorted(conflicts))
                )
        self._validate_owner_exit_capacity(
            considered_intents,
            accounting_previews,
        )

    def _validate_owner_exit_capacity(
        self,
        intents: tuple[RuleIntentProposal, ...],
        accounting_previews: tuple[PositionBookFillPreview, ...],
    ) -> None:
        prospective_positions = {
            (
                preview.position_owner_id,
                preview.fill.instrument,
            ): preview.next_position
            for preview in accounting_previews
        }
        requested: dict[tuple[str, str], Decimal] = {}
        for intent in intents:
            if intent.position_effect not in {
                PositionEffect.REDUCE,
                PositionEffect.CLOSE,
            }:
                continue
            key = (
                str(intent.tags["position_owner_id"]),
                str(intent.tags["instrument"]),
            )
            requested[key] = (
                requested.get(key, Decimal("0")) + intent.quantity
            )
        for key, quantity in requested.items():
            if (
                key not in prospective_positions
                and not self._position_book.tracks_position(*key)
            ):
                # A non-simulation host may intentionally use the Application
                # only for orchestration and keep accounting in its own port.
                continue
            position = prospective_positions.get(
                key,
                self._position_book.position(*key),
            )
            available = (
                Decimal("0")
                if position is None
                else abs(position.quantity)
            )
            if quantity > available:
                raise ValueError(
                    "owner reduce intents exceed virtual position: "
                    f"position_owner_id={key[0]}, "
                    f"instrument={key[1]}, requested={quantity}, "
                    f"available={available}"
                )

    def _record_result(
        self,
        strategy_instance_id: str,
        result: StrategyEventResult,
        consumed_intent_keys: tuple[str, ...],
    ) -> None:
        removals = set(result.cancellations)
        removals.update(consumed_intent_keys)
        for key in removals:
            self._intent_index.pop(key, None)
            self._active_intents.pop(key, None)
        for intent in result.intents:
            if intent.intent_key in self._intent_index:
                raise ValueError(
                    f"duplicate global intent key {intent.intent_key!r}"
                )
            if intent.tags.get("strategy_instance_id") != strategy_instance_id:
                raise ValueError("intent Strategy identity does not match route")
            owner = ApplicationIntentOwner(
                strategy_instance_id=strategy_instance_id,
                rule_instance_id=str(intent.tags["rule_instance_id"]),
                rule_key=str(intent.tags["rule_key"]),
                local_intent_key=str(intent.tags["local_intent_key"]),
            )
            self._intent_index[intent.intent_key] = owner
            self._active_intents[intent.intent_key] = intent

    def _validate_unique_resources(self) -> None:
        allocation_ids = [
            instance.allocation_id for instance in self._instances.values()
        ]
        if None in allocation_ids or len(set(allocation_ids)) != len(
            allocation_ids
        ):
            raise ValueError("each StrategyInstance needs a unique Allocation")
        owner_ids = self.position_owner_ids
        if len(set(owner_ids)) != len(owner_ids):
            raise ValueError(
                "each StrategyInstance needs a unique Position Owner"
            )

    def _require_active(self) -> None:
        if self.lifecycle != StrategyApplicationLifecycle.ACTIVE:
            raise RuntimeError("StrategyApplication is not active")

    def _instance(self, strategy_instance_id: str) -> StrategyInstance:
        try:
            return self._instances[strategy_instance_id]
        except KeyError as exc:
            raise ValueError(
                f"StrategyInstance {strategy_instance_id!r} is not registered"
            ) from exc

    def _update_lifecycle(self) -> None:
        lifecycles = {
            instance.lifecycle for instance in self._instances.values()
        }
        if lifecycles <= {StrategyInstanceLifecycle.COMPLETED}:
            self.lifecycle = StrategyApplicationLifecycle.COMPLETED
        elif (
            StrategyInstanceLifecycle.STOPPED in lifecycles
            and not self._active_intents
            and not lifecycles & {StrategyInstanceLifecycle.ACTIVE}
        ):
            self.lifecycle = StrategyApplicationLifecycle.STOPPED

    def _fail_application(self) -> None:
        for instance in self._instances.values():
            if instance.lifecycle not in {
                StrategyInstanceLifecycle.COMPLETED,
                StrategyInstanceLifecycle.STOPPED,
            }:
                instance.fail()
        self.lifecycle = StrategyApplicationLifecycle.FAILED
