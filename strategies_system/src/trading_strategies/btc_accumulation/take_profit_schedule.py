"""Backward-compatible view of the canonical Ladder Rule schedule."""

from __future__ import annotations

from decimal import Decimal

from ..kernel import TradeSide
from ..rules import (
    LadderPositionOpenedInput,
    LadderTakeProfitRuleConfig,
    build_ladder_take_profit_levels,
)
from .models import TakeProfitLevel


def build_take_profit_schedule(
    *,
    strategy_id: str,
    entry_price: Decimal,
    position_quantity: Decimal,
    first_take_profit_ratio: Decimal,
    end_price: Decimal,
    level_count: int,
    tick_size: Decimal,
    quantity_step: Decimal,
) -> tuple[TakeProfitLevel, ...]:
    """Return legacy DTOs without maintaining a second pricing algorithm."""

    if not isinstance(strategy_id, str) or not strategy_id.strip():
        raise ValueError("strategy_id must not be empty")
    levels = build_ladder_take_profit_levels(
        LadderTakeProfitRuleConfig(
            first_take_profit_ratio=first_take_profit_ratio,
            end_price=end_price,
            level_count=level_count,
            tick_size=tick_size,
            quantity_step=quantity_step,
            quantity_unit="contracts",
            side=TradeSide.SELL,
        ),
        LadderPositionOpenedInput(
            entry_price=entry_price,
            position_quantity=position_quantity,
        ),
    )
    return tuple(
        TakeProfitLevel(
            level=level.level,
            intent_key=f"{strategy_id}:take-profit:{level.intent_key}",
            target_price=level.target_price,
            quantity=level.quantity,
        )
        for level in levels
    )
