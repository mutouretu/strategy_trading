from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from .models import (
    LiquidityRole,
    SimFill,
    TradeInstruction,
    TradeIntentMode,
)


@dataclass(frozen=True, slots=True)
class FeeResult:
    """The fee charged for one completed simulation fill."""

    liquidity_role: LiquidityRole
    fee_rate: Decimal
    fee_amount: Decimal
    fee_asset: str

    def __post_init__(self) -> None:
        if not isinstance(self.liquidity_role, LiquidityRole):
            raise TypeError("liquidity_role must be a LiquidityRole")
        if self.fee_rate < 0:
            raise ValueError("fee_rate must be >= 0")
        if self.fee_amount < 0:
            raise ValueError("fee_amount must be >= 0")
        if not self.fee_asset.strip():
            raise ValueError("fee_asset must not be empty")


class FeeModel(Protocol):
    """Calculate the deterministic fee for one completed fill."""

    def calculate(
        self,
        instruction: TradeInstruction,
        fill: SimFill,
    ) -> FeeResult: ...


def default_liquidity_role(
    intent_mode: TradeIntentMode,
) -> LiquidityRole:
    if intent_mode == TradeIntentMode.PASSIVE:
        return LiquidityRole.MAKER
    return LiquidityRole.TAKER


class ZeroFeeModel:
    """Default model preserving fee-free simulation behavior."""

    def calculate(
        self,
        instruction: TradeInstruction,
        fill: SimFill,
    ) -> FeeResult:
        return FeeResult(
            liquidity_role=default_liquidity_role(
                instruction.intent_mode
            ),
            fee_rate=Decimal("0"),
            fee_amount=Decimal("0"),
            fee_asset=fill.fee_asset,
        )


class FixedRateFeeModel:
    """Fixed-rate fee model for linear quote-notional products."""

    def __init__(
        self,
        *,
        maker_fee_rate: Decimal,
        taker_fee_rate: Decimal,
        fee_asset: str | None = None,
    ) -> None:
        self.maker_fee_rate = Decimal(maker_fee_rate)
        self.taker_fee_rate = Decimal(taker_fee_rate)
        self.fee_asset = (
            None
            if fee_asset is None
            else fee_asset.strip().upper()
        )
        if self.maker_fee_rate < 0:
            raise ValueError("maker_fee_rate must be >= 0")
        if self.taker_fee_rate < 0:
            raise ValueError("taker_fee_rate must be >= 0")
        if self.fee_asset is not None and not self.fee_asset:
            raise ValueError("fee_asset must not be empty")

    def calculate(
        self,
        instruction: TradeInstruction,
        fill: SimFill,
    ) -> FeeResult:
        role = default_liquidity_role(instruction.intent_mode)
        rate = (
            self.maker_fee_rate
            if role == LiquidityRole.MAKER
            else self.taker_fee_rate
        )
        return FeeResult(
            liquidity_role=role,
            fee_rate=rate,
            fee_amount=fill.price * fill.quantity * rate,
            fee_asset=self.fee_asset or fill.fee_asset,
        )
