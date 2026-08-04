"""Deterministic geometric take-profit schedule construction."""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, localcontext

from .models import TakeProfitLevel


def _round_down(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


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
    entry = Decimal(entry_price)
    total = Decimal(position_quantity)
    first_ratio = Decimal(first_take_profit_ratio)
    end = Decimal(end_price)
    tick = Decimal(tick_size)
    step = Decimal(quantity_step)
    if not strategy_id.strip():
        raise ValueError("strategy_id must not be empty")
    if entry <= 0 or total <= 0 or tick <= 0 or step <= 0:
        raise ValueError("prices, quantity and steps must be > 0")
    if first_ratio <= 1:
        raise ValueError("first_take_profit_ratio must be > 1")
    if level_count < 2:
        raise ValueError("level_count must be >= 2")
    if total % step != 0:
        raise ValueError("position_quantity must align with quantity_step")

    first = entry * first_ratio
    if end <= first:
        raise ValueError(
            "take_profit_end_price must be above the first take-profit price"
        )
    with localcontext() as context:
        context.prec = 50
        span = end / first
        prices = []
        for index in range(level_count):
            exponent = Decimal(index) / Decimal(level_count - 1)
            raw = first * context.power(span, exponent)
            prices.append(_round_down(raw, tick))
    if prices[0] <= entry:
        raise ValueError("rounded first take-profit price must exceed entry")
    if any(right <= left for left, right in zip(prices, prices[1:])):
        raise ValueError(
            "tick-size rounding must leave take-profit prices strictly increasing"
        )

    standard = _round_down(total / Decimal(level_count), step)
    if standard <= 0:
        raise ValueError(
            "position_quantity is too small for the requested level_count"
        )
    quantities = [standard] * (level_count - 1)
    quantities.append(total - standard * Decimal(level_count - 1))
    if any(quantity <= 0 or quantity % step != 0 for quantity in quantities):
        raise ValueError("take-profit quantities must be positive and aligned")

    return tuple(
        TakeProfitLevel(
            level=index + 1,
            intent_key=f"{strategy_id}:take-profit:{index + 1}",
            target_price=price,
            quantity=quantity,
        )
        for index, (price, quantity) in enumerate(zip(prices, quantities))
    )
