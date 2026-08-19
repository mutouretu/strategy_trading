"""Product-neutral ladder take-profit state machine."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, localcontext
from enum import StrEnum
from typing import TypeAlias

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


def _positive_decimal(name: str, value: object) -> Decimal:
    converted = Decimal(str(value))
    if not converted.is_finite() or converted <= 0:
        raise ValueError(f"{name} must be finite and > 0")
    return converted


def _round_down(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


@dataclass(frozen=True, slots=True)
class LadderTakeProfitRuleConfig:
    first_take_profit_ratio: Decimal
    end_price: Decimal
    level_count: int
    tick_size: Decimal
    quantity_step: Decimal
    quantity_unit: str
    side: TradeSide
    role: str = "take_profit"

    def __post_init__(self) -> None:
        for field_name in (
            "first_take_profit_ratio",
            "end_price",
            "tick_size",
            "quantity_step",
        ):
            object.__setattr__(
                self,
                field_name,
                _positive_decimal(field_name, getattr(self, field_name)),
            )
        if self.first_take_profit_ratio <= 1:
            raise ValueError("first_take_profit_ratio must be > 1")
        if (
            isinstance(self.level_count, bool)
            or not isinstance(self.level_count, int)
            or self.level_count < 2
        ):
            raise ValueError("level_count must be an integer >= 2")
        if not isinstance(self.side, TradeSide):
            raise TypeError("side must be a TradeSide")
        if (
            not isinstance(self.quantity_unit, str)
            or not self.quantity_unit.strip()
        ):
            raise ValueError("quantity_unit must be a non-empty string")
        if not isinstance(self.role, str) or not self.role.strip():
            raise ValueError("role must be a non-empty string")


@dataclass(frozen=True, slots=True)
class LadderLevel:
    level: int
    intent_key: str
    target_price: Decimal
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class LadderPositionOpenedInput:
    entry_price: Decimal
    position_quantity: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "entry_price",
            _positive_decimal("entry_price", self.entry_price),
        )
        object.__setattr__(
            self,
            "position_quantity",
            _positive_decimal("position_quantity", self.position_quantity),
        )


@dataclass(frozen=True, slots=True)
class LadderFillInput:
    fill_id: str
    intent_key: str
    side: TradeSide
    price: Decimal
    quantity: Decimal

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


@dataclass(frozen=True, slots=True)
class LadderStopInput:
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")


LadderTakeProfitInput: TypeAlias = (
    LadderPositionOpenedInput | LadderFillInput | LadderStopInput
)


class LadderTakeProfitPhase(StrEnum):
    WAITING_POSITION = "WAITING_POSITION"
    POSITION_OPEN = "POSITION_OPEN"
    PARTIALLY_EXITED = "PARTIALLY_EXITED"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"


@dataclass(frozen=True, slots=True)
class LadderTakeProfitRuleState:
    phase: LadderTakeProfitPhase = LadderTakeProfitPhase.WAITING_POSITION
    entry_price: Decimal | None = None
    position_quantity: Decimal = Decimal("0")
    levels: tuple[LadderLevel, ...] = ()
    filled_intent_keys: frozenset[str] = frozenset()
    processed_fill_ids: frozenset[str] = frozenset()

    @property
    def visible_levels(self) -> tuple[LadderLevel, ...]:
        return tuple(
            level
            for level in self.levels
            if level.intent_key not in self.filled_intent_keys
        )


def build_ladder_take_profit_levels(
    config: LadderTakeProfitRuleConfig,
    input: LadderPositionOpenedInput,
) -> tuple[LadderLevel, ...]:
    """Build the canonical deterministic schedule used by every adapter."""

    total = input.position_quantity
    if total % config.quantity_step != 0:
        raise ValueError("position_quantity must align with quantity_step")
    closes_long = config.side == TradeSide.SELL
    first = (
        input.entry_price * config.first_take_profit_ratio
        if closes_long
        else input.entry_price / config.first_take_profit_ratio
    )
    if closes_long and config.end_price <= first:
        raise ValueError(
            "end_price must be above the first take-profit price"
        )
    if not closes_long and config.end_price >= first:
        raise ValueError(
            "end_price must be below the first take-profit price"
        )
    with localcontext() as context:
        context.prec = 50
        span = config.end_price / first
        prices = tuple(
            _round_down(
                first
                * context.power(
                    span,
                    Decimal(index) / Decimal(config.level_count - 1),
                ),
                config.tick_size,
            )
            for index in range(config.level_count)
        )
    if closes_long:
        if prices[0] <= input.entry_price:
            raise ValueError(
                "rounded first take-profit price must exceed entry"
            )
        if any(right <= left for left, right in zip(prices, prices[1:])):
            raise ValueError("rounded take-profit prices must be increasing")
    else:
        if prices[0] >= input.entry_price:
            raise ValueError(
                "rounded first take-profit price must be below entry"
            )
        if any(right >= left for left, right in zip(prices, prices[1:])):
            raise ValueError("rounded take-profit prices must be decreasing")
    standard = _round_down(
        total / Decimal(config.level_count),
        config.quantity_step,
    )
    if standard <= 0:
        raise ValueError("position is too small for the requested level_count")
    quantities = (standard,) * (config.level_count - 1) + (
        total - standard * Decimal(config.level_count - 1),
    )
    if quantities[-1] <= 0:
        raise ValueError("last take-profit quantity must be > 0")
    if any(quantity % config.quantity_step != 0 for quantity in quantities):
        raise ValueError("take-profit quantities must align with quantity_step")
    return tuple(
        LadderLevel(
            level=index + 1,
            intent_key=str(index + 1),
            target_price=price,
            quantity=quantity,
        )
        for index, (price, quantity) in enumerate(zip(prices, quantities))
    )


class LadderTakeProfitRule(
    TradingRule[
        LadderTakeProfitRuleConfig,
        LadderTakeProfitInput,
        LadderTakeProfitRuleState,
        None,
    ]
):
    rule_type = "ladder-take-profit/v1"

    @classmethod
    def definition(cls) -> TradingRuleDefinition:
        return TradingRuleDefinition(
            rule_type=cls.rule_type,
            display_name="阶梯止盈",
            version="v1",
            summary="根据真实建仓成交价和明确仓位数量生成阶梯退出意图。",
            family="position-exit",
            input_types=(
                "LadderPositionOpenedInput",
                "LadderFillInput",
                "LadderStopInput",
            ),
            config_fields=(
                RuleConfigFieldDefinition(
                    "first_take_profit_ratio",
                    "首档止盈倍数",
                    required=True,
                ),
                RuleConfigFieldDefinition("end_price", "末档价格", required=True),
                RuleConfigFieldDefinition("level_count", "档位数", required=True),
                RuleConfigFieldDefinition(
                    "quantity_unit",
                    "数量单位",
                    required=True,
                ),
                RuleConfigFieldDefinition("side", "平仓方向", required=True),
            ),
            output_types=("RuleIntentProposal", "RuleTransition"),
            supported_directions=("LONG", "SHORT"),
            supported_product_types=(
                "SPOT",
                "LINEAR_PERPETUAL",
                "INVERSE_PERPETUAL",
            ),
            lifecycle=(
                "WAITING_POSITION",
                "POSITION_OPEN",
                "PARTIALLY_EXITED",
                "COMPLETED",
                "STOPPED",
            ),
            formulae=(
                "LONG: P[first] = P[entry] * r; SHORT: P[first] = P[entry] / r",
                "P[i] = P[first] * (P[end] / P[first])^(i/(n-1))",
            ),
            constraints=(
                "退出数量使用产品原生数量",
                "全部退出意图均为 reduce-only",
                "最后一档承担数量舍入余量",
            ),
        )

    def initial_state(
        self,
        rule_config: LadderTakeProfitRuleConfig,
        context: None,
    ) -> LadderTakeProfitRuleState:
        del rule_config, context
        return LadderTakeProfitRuleState()

    def transition(
        self,
        rule_config: LadderTakeProfitRuleConfig,
        state: LadderTakeProfitRuleState,
        input: LadderTakeProfitInput,
        context: None,
    ) -> RuleTransition[LadderTakeProfitRuleState]:
        del context
        if isinstance(input, LadderPositionOpenedInput):
            return self._open(rule_config, state, input)
        if isinstance(input, LadderFillInput):
            return self._fill(rule_config, state, input)
        if isinstance(input, LadderStopInput):
            return self._stop(state)
        raise TypeError(
            f"unsupported LadderTakeProfitRule input: {type(input).__name__}"
        )

    def _open(
        self,
        config: LadderTakeProfitRuleConfig,
        state: LadderTakeProfitRuleState,
        input: LadderPositionOpenedInput,
    ) -> RuleTransition[LadderTakeProfitRuleState]:
        if state.phase != LadderTakeProfitPhase.WAITING_POSITION:
            raise RuntimeError("ladder position is already initialized")
        levels = build_ladder_take_profit_levels(config, input)
        intents = tuple(
            RuleIntentProposal(
                intent_key=level.intent_key,
                side=config.side,
                quantity=level.quantity,
                quantity_unit=config.quantity_unit,
                position_effect=PositionEffect.REDUCE,
                intent_mode=RuleIntentMode.PASSIVE,
                target_price=level.target_price,
                reduce_only=True,
                role=config.role,
                tags={"take_profit_level": str(level.level)},
            )
            for level in levels
        )
        return RuleTransition(
            next_state=LadderTakeProfitRuleState(
                phase=LadderTakeProfitPhase.POSITION_OPEN,
                entry_price=input.entry_price,
                position_quantity=input.position_quantity,
                levels=levels,
            ),
            intents=intents,
        )

    def _fill(
        self,
        config: LadderTakeProfitRuleConfig,
        state: LadderTakeProfitRuleState,
        input: LadderFillInput,
    ) -> RuleTransition[LadderTakeProfitRuleState]:
        if input.fill_id in state.processed_fill_ids:
            return RuleTransition.unchanged(state)
        if state.phase not in {
            LadderTakeProfitPhase.POSITION_OPEN,
            LadderTakeProfitPhase.PARTIALLY_EXITED,
        }:
            raise RuntimeError("take-profit fill is not expected")
        if input.side != config.side:
            raise ValueError("take-profit fill side does not match the Rule")
        levels = {level.intent_key: level for level in state.levels}
        try:
            level = levels[input.intent_key]
        except KeyError as exc:
            raise ValueError("take-profit fill references an unknown intent") from exc
        if input.intent_key in state.filled_intent_keys:
            raise ValueError("take-profit intent has already filled")
        if input.quantity != level.quantity:
            raise ValueError("partial take-profit fills are not supported in v1")
        filled_keys = state.filled_intent_keys | {input.intent_key}
        completed = len(filled_keys) == len(state.levels)
        return RuleTransition(
            next_state=LadderTakeProfitRuleState(
                phase=(
                    LadderTakeProfitPhase.COMPLETED
                    if completed
                    else LadderTakeProfitPhase.PARTIALLY_EXITED
                ),
                entry_price=state.entry_price,
                position_quantity=state.position_quantity,
                levels=state.levels,
                filled_intent_keys=filled_keys,
                processed_fill_ids=state.processed_fill_ids | {input.fill_id},
            ),
            lifecycle_request=(
                RuleLifecycleRequest.COMPLETE
                if completed
                else RuleLifecycleRequest.NONE
            ),
        )

    def _stop(
        self,
        state: LadderTakeProfitRuleState,
    ) -> RuleTransition[LadderTakeProfitRuleState]:
        if state.phase in {
            LadderTakeProfitPhase.COMPLETED,
            LadderTakeProfitPhase.STOPPED,
        }:
            return RuleTransition.unchanged(state)
        return RuleTransition(
            next_state=LadderTakeProfitRuleState(
                phase=LadderTakeProfitPhase.STOPPED,
                entry_price=state.entry_price,
                position_quantity=state.position_quantity,
                levels=state.levels,
                filled_intent_keys=state.filled_intent_keys,
                processed_fill_ids=state.processed_fill_ids,
            ),
            cancellations=tuple(
                level.intent_key for level in state.visible_levels
            ),
            lifecycle_request=RuleLifecycleRequest.STOP,
        )
