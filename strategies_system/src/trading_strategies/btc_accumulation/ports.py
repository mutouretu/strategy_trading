from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from .models import PositionPlan


class CoinMLongPositionSizer(Protocol):
    def size_long(
        self,
        *,
        entry_price: Decimal,
        target_liquidation_price: Decimal,
        safety_buffer_ratio: Decimal = Decimal("0"),
    ) -> PositionPlan: ...

    def size_long_by_effective_leverage(
        self,
        *,
        entry_price: Decimal,
        effective_leverage: Decimal,
    ) -> PositionPlan: ...

    def evaluate_long(
        self,
        *,
        entry_price: Decimal,
        quantity: Decimal,
    ) -> PositionPlan: ...
