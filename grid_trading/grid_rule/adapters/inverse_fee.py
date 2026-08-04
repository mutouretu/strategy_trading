"""Fee calculation for coin-margined inverse contracts."""

from __future__ import annotations

from decimal import Decimal

from simulation_runtime import (
    FeeResult,
    LiquidityRole,
    SimFill,
    TradeInstruction,
    TradeIntentMode,
)


class InverseContractFeeModel:
    """Charge inverse-contract fees in the base settlement asset."""

    def __init__(
        self,
        *,
        contract_size: Decimal,
        maker_fee_rate: Decimal,
        taker_fee_rate: Decimal,
        fee_asset: str = "BTC",
    ) -> None:
        self.contract_size = Decimal(contract_size)
        self.maker_fee_rate = Decimal(maker_fee_rate)
        self.taker_fee_rate = Decimal(taker_fee_rate)
        self.fee_asset = fee_asset.strip().upper()
        if self.contract_size <= 0:
            raise ValueError("contract_size must be > 0")
        if self.maker_fee_rate < 0:
            raise ValueError("maker_fee_rate must be >= 0")
        if self.taker_fee_rate < 0:
            raise ValueError("taker_fee_rate must be >= 0")
        if not self.fee_asset:
            raise ValueError("fee_asset must not be empty")

    def calculate(
        self,
        instruction: TradeInstruction,
        fill: SimFill,
    ) -> FeeResult:
        role = (
            LiquidityRole.MAKER
            if instruction.intent_mode == TradeIntentMode.PASSIVE
            else LiquidityRole.TAKER
        )
        rate = (
            self.maker_fee_rate
            if role == LiquidityRole.MAKER
            else self.taker_fee_rate
        )
        return FeeResult(
            liquidity_role=role,
            fee_rate=rate,
            fee_amount=(
                fill.quantity
                * self.contract_size
                / fill.price
                * rate
            ),
            fee_asset=self.fee_asset,
        )
