"""Resolved per-instance allocation records for StrategyApplication."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading_strategies.strategy import CapitalAllocationSpec


@dataclass(frozen=True, slots=True)
class CapitalAllocation:
    allocation_id: str
    strategy_instance_id: str
    settlement_asset: str
    amount: Decimal

    def __post_init__(self) -> None:
        for field_name in ("allocation_id", "strategy_instance_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if (
            not isinstance(self.settlement_asset, str)
            or not self.settlement_asset.strip()
        ):
            raise ValueError("settlement_asset must be a non-empty string")
        object.__setattr__(
            self,
            "settlement_asset",
            self.settlement_asset.strip().upper(),
        )
        amount = Decimal(str(self.amount))
        if not amount.is_finite() or amount <= 0:
            raise ValueError("amount must be finite and > 0")
        object.__setattr__(self, "amount", amount)

    @classmethod
    def from_spec(
        cls,
        *,
        strategy_instance_id: str,
        spec: CapitalAllocationSpec,
    ) -> "CapitalAllocation":
        return cls(
            allocation_id=f"{strategy_instance_id}:allocation",
            strategy_instance_id=strategy_instance_id,
            settlement_asset=spec.settlement_asset,
            amount=spec.amount,
        )
