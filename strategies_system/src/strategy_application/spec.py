"""Immutable launch configuration for the Strategy application host."""

from __future__ import annotations

from dataclasses import dataclass, field

from trading_strategies.strategy import (
    CapitalAllocationSpec,
    ConflictResolution,
    ProposalBatchMode,
    StrategySpec,
)


def _required_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True, slots=True)
class StrategyLaunchSpec:
    """One independently funded run of a shared StrategySpec."""

    strategy_instance_id: str
    strategy_spec: StrategySpec
    capital_allocation: CapitalAllocationSpec | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "strategy_instance_id",
            _required_text(
                "strategy_instance_id",
                self.strategy_instance_id,
            ),
        )
        if not isinstance(self.strategy_spec, StrategySpec):
            raise TypeError("strategy_spec must be a StrategySpec")
        if self.capital_allocation is not None and not isinstance(
            self.capital_allocation,
            CapitalAllocationSpec,
        ):
            raise TypeError(
                "capital_allocation must be a CapitalAllocationSpec or None"
            )
        _ = self.resolved_capital_allocation

    @property
    def resolved_capital_allocation(self) -> CapitalAllocationSpec:
        allocation = (
            self.capital_allocation or self.strategy_spec.capital_allocation
        )
        if allocation is None:
            raise ValueError(
                "StrategyLaunchSpec requires a settlement-asset allocation"
            )
        return allocation


@dataclass(frozen=True, slots=True)
class ApplicationCoordinationPolicy:
    """Deterministic first-version policy for cross-Strategy proposals."""

    proposal_batch: ProposalBatchMode = ProposalBatchMode.ATOMIC_PER_EVENT
    conflict_resolution: ConflictResolution = ConflictResolution.FAIL_FAST
    reject_opposing_open_intents: bool = True

    def __post_init__(self) -> None:
        if self.proposal_batch != ProposalBatchMode.ATOMIC_PER_EVENT:
            raise ValueError("Application requires ATOMIC_PER_EVENT proposals")
        if self.conflict_resolution != ConflictResolution.FAIL_FAST:
            raise ValueError("Application requires FAIL_FAST conflicts")
        if not isinstance(self.reject_opposing_open_intents, bool):
            raise TypeError("reject_opposing_open_intents must be a bool")

    def to_document(self) -> dict[str, str | bool]:
        return {
            "proposal_batch": self.proposal_batch.value,
            "conflict_resolution": self.conflict_resolution.value,
            "reject_opposing_open_intents": (
                self.reject_opposing_open_intents
            ),
        }


@dataclass(frozen=True, slots=True)
class StrategyApplicationSpec:
    """One or more independently funded Strategy launches."""

    application_id: str
    strategy_launches: tuple[StrategyLaunchSpec, ...]
    coordination_policy: ApplicationCoordinationPolicy = field(
        default_factory=ApplicationCoordinationPolicy
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "application_id",
            _required_text("application_id", self.application_id),
        )
        launches = tuple(self.strategy_launches)
        if not launches:
            raise ValueError("strategy_launches must not be empty")
        if any(not isinstance(item, StrategyLaunchSpec) for item in launches):
            raise TypeError(
                "strategy_launches must contain StrategyLaunchSpec values"
            )
        instance_ids = [item.strategy_instance_id for item in launches]
        if len(set(instance_ids)) != len(instance_ids):
            raise ValueError("strategy_instance_id values must be unique")
        if not isinstance(
            self.coordination_policy,
            ApplicationCoordinationPolicy,
        ):
            raise TypeError(
                "coordination_policy must be an ApplicationCoordinationPolicy"
            )
        object.__setattr__(
            self,
            "strategy_launches",
            tuple(sorted(launches, key=lambda item: item.strategy_instance_id)),
        )

    @property
    def strategy_specs(self) -> tuple[StrategySpec, ...]:
        """Return distinct specs in stable first-launch order."""

        specs: list[StrategySpec] = []
        for launch in self.strategy_launches:
            if launch.strategy_spec not in specs:
                specs.append(launch.strategy_spec)
        return tuple(specs)

    def launch(self, strategy_instance_id: str) -> StrategyLaunchSpec:
        for launch in self.strategy_launches:
            if launch.strategy_instance_id == strategy_instance_id:
                return launch
        raise ValueError(
            f"Strategy launch {strategy_instance_id!r} is not defined"
        )
