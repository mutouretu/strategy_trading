"""Domain values for BTC accumulation strategies."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


def _decimal(name: str, value: object) -> Decimal:
    converted = Decimal(str(value))
    if not converted.is_finite():
        raise ValueError(f"{name} must be finite")
    return converted


class LadderState(StrEnum):
    NEW = "NEW"
    WAITING_ENTRY = "WAITING_ENTRY"
    ENTRY_PENDING = "ENTRY_PENDING"
    POSITION_OPEN = "POSITION_OPEN"
    PARTIALLY_EXITED = "PARTIALLY_EXITED"
    COMPLETED = "COMPLETED"


class StrategyOrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class StrategyRole(StrEnum):
    ENTRY = "entry"
    TAKE_PROFIT = "take_profit"


@dataclass(frozen=True, slots=True)
class TargetLiquidationLadderConfig:
    strategy_id: str
    instrument: str
    target_liquidation_price: Decimal
    first_take_profit_ratio: Decimal
    take_profit_end_price: Decimal
    take_profit_count: int
    tick_size: Decimal
    quantity_step: Decimal
    sizing_safety_buffer_ratio: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not self.strategy_id.strip():
            raise ValueError("strategy_id must not be empty")
        if not self.instrument.strip():
            raise ValueError("instrument must not be empty")
        for name in (
            "target_liquidation_price",
            "first_take_profit_ratio",
            "take_profit_end_price",
            "tick_size",
            "quantity_step",
            "sizing_safety_buffer_ratio",
        ):
            object.__setattr__(self, name, _decimal(name, getattr(self, name)))
        if self.target_liquidation_price <= 0:
            raise ValueError("target_liquidation_price must be > 0")
        if self.first_take_profit_ratio <= 1:
            raise ValueError("first_take_profit_ratio must be > 1")
        if self.take_profit_end_price <= 0:
            raise ValueError("take_profit_end_price must be > 0")
        if (
            isinstance(self.take_profit_count, bool)
            or not isinstance(self.take_profit_count, int)
            or self.take_profit_count < 2
        ):
            raise ValueError("take_profit_count must be an integer >= 2")
        if self.tick_size <= 0:
            raise ValueError("tick_size must be > 0")
        if self.quantity_step <= 0:
            raise ValueError("quantity_step must be > 0")
        if not Decimal("0") <= self.sizing_safety_buffer_ratio < Decimal("1"):
            raise ValueError(
                "sizing_safety_buffer_ratio must be >= 0 and < 1"
            )


@dataclass(frozen=True, slots=True)
class PositionPlan:
    quantity: Decimal
    quantity_unit: str
    estimated_liquidation_price: Decimal
    initial_margin: Decimal
    maintenance_margin: Decimal
    margin_buffer: Decimal
    model_version: str

    def __post_init__(self) -> None:
        for name in (
            "quantity",
            "estimated_liquidation_price",
            "initial_margin",
            "maintenance_margin",
            "margin_buffer",
        ):
            object.__setattr__(self, name, _decimal(name, getattr(self, name)))
        if self.quantity <= 0:
            raise ValueError("quantity must be > 0")
        if not self.quantity_unit.strip():
            raise ValueError("quantity_unit must not be empty")
        if self.estimated_liquidation_price <= 0:
            raise ValueError("estimated_liquidation_price must be > 0")
        if self.initial_margin < 0 or self.maintenance_margin < 0:
            raise ValueError("margin requirements must be >= 0")
        if not self.model_version.strip():
            raise ValueError("model_version must not be empty")


@dataclass(frozen=True, slots=True)
class EntryPlan:
    intent_key: str
    reference_price: Decimal
    position: PositionPlan

    def __post_init__(self) -> None:
        if not self.intent_key.strip():
            raise ValueError("intent_key must not be empty")
        object.__setattr__(
            self,
            "reference_price",
            _decimal("reference_price", self.reference_price),
        )
        if self.reference_price <= 0:
            raise ValueError("reference_price must be > 0")


@dataclass(frozen=True, slots=True)
class TakeProfitLevel:
    level: int
    intent_key: str
    target_price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        if self.level < 1:
            raise ValueError("level must be >= 1")
        if not self.intent_key.strip():
            raise ValueError("intent_key must not be empty")
        object.__setattr__(
            self,
            "target_price",
            _decimal("target_price", self.target_price),
        )
        object.__setattr__(
            self,
            "quantity",
            _decimal("quantity", self.quantity),
        )
        if self.target_price <= 0 or self.quantity <= 0:
            raise ValueError("target_price and quantity must be > 0")


@dataclass(frozen=True, slots=True)
class StrategyFill:
    fill_id: str
    intent_key: str
    role: StrategyRole
    side: StrategyOrderSide
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        if not self.fill_id.strip() or not self.intent_key.strip():
            raise ValueError("fill_id and intent_key must not be empty")
        if not isinstance(self.role, StrategyRole):
            raise TypeError("role must be a StrategyRole")
        if not isinstance(self.side, StrategyOrderSide):
            raise TypeError("side must be a StrategyOrderSide")
        object.__setattr__(self, "price", _decimal("price", self.price))
        object.__setattr__(
            self,
            "quantity",
            _decimal("quantity", self.quantity),
        )
        if self.price <= 0 or self.quantity <= 0:
            raise ValueError("fill price and quantity must be > 0")
