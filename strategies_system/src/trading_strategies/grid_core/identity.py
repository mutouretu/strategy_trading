"""Stable identifiers for grid cells across runtime environments."""

from __future__ import annotations

import hashlib
from decimal import Decimal


def decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def stable_cell_id(
    grid_id: str,
    buy_price: Decimal,
    sell_price: Decimal,
) -> str:
    raw = f"{grid_id}:{decimal_text(buy_price)}:{decimal_text(sell_price)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
