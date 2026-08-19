"""Immutable value objects used to compose trading rules into a strategy."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


def required_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def positive_decimal(name: str, value: object) -> Decimal:
    converted = Decimal(str(value))
    if not converted.is_finite() or converted <= 0:
        raise ValueError(f"{name} must be finite and > 0")
    return converted


class StrategyDirection(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class StrategyProductType(StrEnum):
    SPOT = "SPOT"
    LINEAR_PERPETUAL = "LINEAR_PERPETUAL"
    INVERSE_PERPETUAL = "INVERSE_PERPETUAL"


class RulePermission(StrEnum):
    OPEN = "OPEN"
    INCREASE = "INCREASE"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"
    MANAGE_EXITS = "MANAGE_EXITS"


class ProposalBatchMode(StrEnum):
    ATOMIC_PER_EVENT = "ATOMIC_PER_EVENT"


class ConflictResolution(StrEnum):
    FAIL_FAST = "FAIL_FAST"


@dataclass(frozen=True, slots=True)
class InstrumentBinding:
    instrument: str
    direction: StrategyDirection
    product_type: StrategyProductType

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "instrument",
            required_text("instrument", self.instrument),
        )
        if not isinstance(self.direction, StrategyDirection):
            raise TypeError("direction must be a StrategyDirection")
        if not isinstance(self.product_type, StrategyProductType):
            raise TypeError("product_type must be a StrategyProductType")
        if (
            self.product_type == StrategyProductType.SPOT
            and self.direction == StrategyDirection.SHORT
        ):
            raise ValueError("SPOT does not support a SHORT strategy binding")


@dataclass(frozen=True, slots=True)
class CapitalAllocationSpec:
    settlement_asset: str
    amount: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "settlement_asset",
            required_text("settlement_asset", self.settlement_asset).upper(),
        )
        object.__setattr__(
            self,
            "amount",
            positive_decimal("amount", self.amount),
        )


@dataclass(frozen=True, slots=True)
class StrategyCoordinationPolicy:
    proposal_batch: ProposalBatchMode = ProposalBatchMode.ATOMIC_PER_EVENT
    conflict_resolution: ConflictResolution = ConflictResolution.FAIL_FAST
    exit_controller_rule_key: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.proposal_batch, ProposalBatchMode):
            raise TypeError("proposal_batch must be a ProposalBatchMode")
        if not isinstance(self.conflict_resolution, ConflictResolution):
            raise TypeError(
                "conflict_resolution must be a ConflictResolution"
            )
        if self.exit_controller_rule_key is not None:
            object.__setattr__(
                self,
                "exit_controller_rule_key",
                required_text(
                    "exit_controller_rule_key",
                    self.exit_controller_rule_key,
                ),
            )

    def to_document(self) -> dict[str, str | None]:
        """Return the stable JSON-compatible policy payload."""

        return {
            "proposal_batch": self.proposal_batch.value,
            "conflict_resolution": self.conflict_resolution.value,
            "exit_controller_rule_key": self.exit_controller_rule_key,
        }
