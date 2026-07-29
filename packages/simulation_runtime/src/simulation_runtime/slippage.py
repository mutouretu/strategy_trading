from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from market_protocol import MarketFrame

from .models import OrderSide, TradeInstruction, TradeIntentMode


class SlippageModel(Protocol):
    """Calculate an effective fill price without mutating account state."""

    @property
    def enabled(self) -> bool: ...

    @property
    def source(self) -> str: ...

    def apply(
        self,
        instruction: TradeInstruction,
        reference_price: Decimal,
        frame: MarketFrame,
    ) -> Decimal: ...


class NoSlippageModel:
    """Default model preserving the instruction's reference price."""

    enabled = False
    source = "ZERO"

    def apply(
        self,
        instruction: TradeInstruction,
        reference_price: Decimal,
        frame: MarketFrame,
    ) -> Decimal:
        del instruction, frame
        return reference_price


class FixedBpsSlippageModel:
    """Apply a fixed adverse price deviation to ACTIVE instructions.

    PASSIVE instructions preserve their specified touch price. A positive
    configuration increases ACTIVE BUY prices and decreases ACTIVE SELL
    prices.
    """

    enabled = True
    source = "FIXED_BPS"

    def __init__(self, slippage_bps: Decimal) -> None:
        self.slippage_bps = Decimal(slippage_bps)
        if not self.slippage_bps.is_finite():
            raise ValueError("slippage_bps must be finite")
        if self.slippage_bps < 0:
            raise ValueError("slippage_bps must be >= 0")
        if self.slippage_bps >= Decimal("10000"):
            raise ValueError("slippage_bps must be < 10000")

    def apply(
        self,
        instruction: TradeInstruction,
        reference_price: Decimal,
        frame: MarketFrame,
    ) -> Decimal:
        del frame
        if reference_price <= 0:
            raise ValueError("reference_price must be > 0")
        if instruction.intent_mode == TradeIntentMode.PASSIVE:
            return reference_price

        rate = self.slippage_bps / Decimal("10000")
        if instruction.side == OrderSide.BUY:
            return reference_price * (Decimal("1") + rate)
        return reference_price * (Decimal("1") - rate)
