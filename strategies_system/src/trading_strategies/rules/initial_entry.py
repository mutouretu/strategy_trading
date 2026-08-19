"""Reusable initial-entry state machine."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, TypeAlias

from ..kernel import (
    PositionEffect,
    RuleConfigFieldDefinition,
    RuleIntentMode,
    RuleIntentProposal,
    RuleLifecycleRequest,
    RuleTransition,
    TradeSide,
    TradingRule,
    TradingRuleDefinition,
)
from ..kernel._values import freeze_mapping


def _positive_decimal(name: str, value: object) -> Decimal:
    converted = Decimal(str(value))
    if not converted.is_finite() or converted <= 0:
        raise ValueError(f"{name} must be finite and > 0")
    return converted


@dataclass(frozen=True, slots=True)
class SizedPosition:
    quantity: Decimal
    quantity_unit: str
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "quantity",
            _positive_decimal("quantity", self.quantity),
        )
        if (
            not isinstance(self.quantity_unit, str)
            or not self.quantity_unit.strip()
        ):
            raise ValueError("quantity_unit must be a non-empty string")
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))


class EntryPositionSizer(Protocol):
    def size_entry(self, *, reference_price: Decimal) -> SizedPosition: ...


@dataclass(frozen=True, slots=True)
class InitialEntryRuleContext:
    position_sizer: EntryPositionSizer


@dataclass(frozen=True, slots=True)
class InitialEntryRuleConfig:
    intent_key: str
    side: TradeSide
    role: str = "entry"

    def __post_init__(self) -> None:
        if not isinstance(self.intent_key, str) or not self.intent_key.strip():
            raise ValueError("intent_key must be a non-empty string")
        if not isinstance(self.side, TradeSide):
            raise TypeError("side must be a TradeSide")
        if not isinstance(self.role, str) or not self.role.strip():
            raise ValueError("role must be a non-empty string")


@dataclass(frozen=True, slots=True)
class InitialEntryStartInput:
    reference_price: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reference_price",
            _positive_decimal("reference_price", self.reference_price),
        )


@dataclass(frozen=True, slots=True)
class InitialEntryFillInput:
    fill_id: str
    intent_key: str
    side: TradeSide
    price: Decimal
    quantity: Decimal
    actual_position: SizedPosition

    def __post_init__(self) -> None:
        for field_name in ("fill_id", "intent_key"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if not isinstance(self.side, TradeSide):
            raise TypeError("side must be a TradeSide")
        object.__setattr__(self, "price", _positive_decimal("price", self.price))
        object.__setattr__(
            self,
            "quantity",
            _positive_decimal("quantity", self.quantity),
        )
        if not isinstance(self.actual_position, SizedPosition):
            raise TypeError("actual_position must be a SizedPosition")
        if self.actual_position.quantity != self.quantity:
            raise ValueError("actual_position quantity must match fill quantity")


InitialEntryInput: TypeAlias = InitialEntryStartInput | InitialEntryFillInput


class InitialEntryPhase(StrEnum):
    WAITING_START = "WAITING_START"
    ENTRY_PENDING = "ENTRY_PENDING"
    FILLED = "FILLED"


@dataclass(frozen=True, slots=True)
class InitialEntryFillRecord:
    fill_id: str
    price: Decimal
    quantity: Decimal
    actual_position: SizedPosition


@dataclass(frozen=True, slots=True)
class InitialEntryRuleState:
    phase: InitialEntryPhase = InitialEntryPhase.WAITING_START
    reference_price: Decimal | None = None
    planned_position: SizedPosition | None = None
    fill: InitialEntryFillRecord | None = None
    processed_fill_ids: frozenset[str] = frozenset()


class InitialEntryRule(
    TradingRule[
        InitialEntryRuleConfig,
        InitialEntryInput,
        InitialEntryRuleState,
        InitialEntryRuleContext,
    ]
):
    rule_type = "initial-entry/v1"

    @classmethod
    def definition(cls) -> TradingRuleDefinition:
        return TradingRuleDefinition(
            rule_type=cls.rule_type,
            display_name="初始建仓",
            version="v1",
            summary="在 Strategy 启动后生成一次明确数量的建仓意图。",
            family="position-entry",
            input_types=("InitialEntryStartInput", "InitialEntryFillInput"),
            config_fields=(
                RuleConfigFieldDefinition(
                    "intent_key",
                    "意图局部键",
                    required=True,
                ),
                RuleConfigFieldDefinition("side", "交易方向", required=True),
            ),
            output_types=("RuleIntentProposal", "RuleTransition"),
            supported_directions=("LONG", "SHORT"),
            supported_product_types=(
                "SPOT",
                "LINEAR_PERPETUAL",
                "INVERSE_PERPETUAL",
            ),
            lifecycle=("WAITING_START", "ENTRY_PENDING", "FILLED"),
            formulae=("(s[t+1], y[t]) = T_entry(s[t], x[t]; theta)",),
            constraints=(
                "只生成一次 OPEN 意图",
                "Intent quantity 使用产品原生数量",
                "不接受目标仓位",
            ),
        )

    def initial_state(
        self,
        rule_config: InitialEntryRuleConfig,
        context: InitialEntryRuleContext,
    ) -> InitialEntryRuleState:
        del rule_config, context
        return InitialEntryRuleState()

    def transition(
        self,
        rule_config: InitialEntryRuleConfig,
        state: InitialEntryRuleState,
        input: InitialEntryInput,
        context: InitialEntryRuleContext,
    ) -> RuleTransition[InitialEntryRuleState]:
        if isinstance(input, InitialEntryStartInput):
            return self._start(rule_config, state, input, context)
        if isinstance(input, InitialEntryFillInput):
            return self._fill(rule_config, state, input)
        raise TypeError(f"unsupported InitialEntryRule input: {type(input).__name__}")

    def _start(
        self,
        config: InitialEntryRuleConfig,
        state: InitialEntryRuleState,
        input: InitialEntryStartInput,
        context: InitialEntryRuleContext,
    ) -> RuleTransition[InitialEntryRuleState]:
        if state.phase != InitialEntryPhase.WAITING_START:
            raise RuntimeError("initial entry has already been started")
        planned = context.position_sizer.size_entry(
            reference_price=input.reference_price
        )
        intent = RuleIntentProposal(
            intent_key=config.intent_key,
            side=config.side,
            quantity=planned.quantity,
            quantity_unit=planned.quantity_unit,
            position_effect=PositionEffect.OPEN,
            intent_mode=RuleIntentMode.ACTIVE,
            target_price=input.reference_price,
            role=config.role,
        )
        return RuleTransition(
            next_state=InitialEntryRuleState(
                phase=InitialEntryPhase.ENTRY_PENDING,
                reference_price=input.reference_price,
                planned_position=planned,
            ),
            intents=(intent,),
        )

    def _fill(
        self,
        config: InitialEntryRuleConfig,
        state: InitialEntryRuleState,
        input: InitialEntryFillInput,
    ) -> RuleTransition[InitialEntryRuleState]:
        if input.fill_id in state.processed_fill_ids:
            return RuleTransition.unchanged(state)
        if state.phase != InitialEntryPhase.ENTRY_PENDING:
            raise RuntimeError("entry fill is not expected")
        if state.planned_position is None:
            raise RuntimeError("entry plan is missing")
        if input.intent_key != config.intent_key:
            raise ValueError("entry fill references an unknown intent")
        if input.side != config.side:
            raise ValueError("entry fill side does not match the Rule")
        if input.quantity != state.planned_position.quantity:
            raise ValueError("partial entry fills are not supported in v1")
        return RuleTransition(
            next_state=InitialEntryRuleState(
                phase=InitialEntryPhase.FILLED,
                reference_price=state.reference_price,
                planned_position=state.planned_position,
                fill=InitialEntryFillRecord(
                    fill_id=input.fill_id,
                    price=input.price,
                    quantity=input.quantity,
                    actual_position=input.actual_position,
                ),
                processed_fill_ids=state.processed_fill_ids | {input.fill_id},
            ),
            lifecycle_request=RuleLifecycleRequest.COMPLETE,
        )
