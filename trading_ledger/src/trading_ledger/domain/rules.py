"""Frozen first-version market and fee parameters.

Calculation and persistence are deliberately deferred to stage 2.
"""

from dataclasses import dataclass
from decimal import Decimal


CN_STOCK_LOT_SIZE = 100


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    commission_rate: Decimal
    minimum_commission: Decimal
    sell_stamp_tax_rate: Decimal
    sell_transfer_fee_rate: Decimal


CN_STOCK_FEE_SCHEDULE = FeeSchedule(
    commission_rate=Decimal("0.000085"),
    minimum_commission=Decimal("5.00"),
    sell_stamp_tax_rate=Decimal("0.001"),
    sell_transfer_fee_rate=Decimal("0.00001"),
)
