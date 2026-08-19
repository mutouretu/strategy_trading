"""Product-neutral trade intent proposed by a trading rule."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from ._values import freeze_mapping


def _decimal(name: str, value: object) -> Decimal:
    converted = Decimal(str(value))
    if not converted.is_finite():
        raise ValueError(f"{name} must be finite")
    return converted


class RuleIntentMode(StrEnum):
    ACTIVE = "ACTIVE"
    PASSIVE = "PASSIVE"


class TradeSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class PositionEffect(StrEnum):
    OPEN = "OPEN"
    INCREASE = "INCREASE"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"


@dataclass(frozen=True, slots=True)
class RuleIntentProposal:
    intent_key: str
    side: TradeSide
    quantity: Decimal
    quantity_unit: str
    position_effect: PositionEffect
    intent_mode: RuleIntentMode
    target_price: Decimal | None = None
    reduce_only: bool = False
    role: str = "trade"
    tags: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.intent_key, str) or not self.intent_key.strip():
            raise ValueError("intent_key must be a non-empty string")
        if not isinstance(self.side, TradeSide):
            raise TypeError("side must be a TradeSide")
        if not isinstance(self.position_effect, PositionEffect):
            raise TypeError("position_effect must be a PositionEffect")
        if not isinstance(self.intent_mode, RuleIntentMode):
            raise TypeError("intent_mode must be a RuleIntentMode")
        quantity = _decimal("quantity", self.quantity)
        if quantity <= 0:
            raise ValueError("quantity must be > 0")
        object.__setattr__(self, "quantity", quantity)
        if (
            not isinstance(self.quantity_unit, str)
            or not self.quantity_unit.strip()
        ):
            raise ValueError("quantity_unit must be a non-empty string")
        object.__setattr__(
            self,
            "quantity_unit",
            self.quantity_unit.strip(),
        )
        if self.target_price is not None:
            target_price = _decimal("target_price", self.target_price)
            if target_price <= 0:
                raise ValueError("target_price must be > 0")
            object.__setattr__(self, "target_price", target_price)
        if self.intent_mode == RuleIntentMode.PASSIVE and self.target_price is None:
            raise ValueError("a passive intent requires target_price")
        if not isinstance(self.reduce_only, bool):
            raise TypeError("reduce_only must be a bool")
        reducing = self.position_effect in {
            PositionEffect.REDUCE,
            PositionEffect.CLOSE,
        }
        if reducing != self.reduce_only:
            raise ValueError(
                "REDUCE/CLOSE intents must be reduce_only and "
                "OPEN/INCREASE intents must not be reduce_only"
            )
        if not isinstance(self.role, str) or not self.role.strip():
            raise ValueError("role must be a non-empty string")
        object.__setattr__(self, "tags", freeze_mapping(dict(self.tags)))
