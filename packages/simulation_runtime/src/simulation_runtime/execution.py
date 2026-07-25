from __future__ import annotations

from collections.abc import Iterable

from market_protocol import MarketFrame

from .models import ActiveOrder, OrderType, SimFill


class BarTouchExecutionModel:
    """Execute previously active orders from one completed OHLC bar.

    LIMIT orders fill at their limit when the inclusive bar range touches that
    price. MARKET orders fill at bar open. Side affects the ledger but never
    order eligibility; no counterparties or order book are simulated.
    """

    def match(
        self,
        current: MarketFrame,
        active_orders: Iterable[ActiveOrder],
    ) -> tuple[SimFill, ...]:
        eligible = [
            active
            for active in active_orders
            if active.order.instrument == current.instrument
            and active.activated_at_sequence < current.sequence
            and self._is_filled(active, current)
        ]
        # A bar does not reveal intrabar path. Stable logical-key order makes
        # batch delivery deterministic without pretending to know fill order.
        eligible.sort(key=lambda active: active.order.order_key)

        return tuple(
            SimFill(
                fill_id=f"{active.order.order_key}@{current.sequence}",
                order_key=active.order.order_key,
                instrument=active.order.instrument,
                side=active.order.side,
                price=(
                    current.open
                    if active.order.order_type == OrderType.MARKET
                    else active.order.limit_price
                ),
                quantity=active.order.quantity,
                sequence=current.sequence,
                timestamp=current.timestamp,
                tags=active.order.tags,
            )
            for active in eligible
        )

    @staticmethod
    def _is_filled(active: ActiveOrder, current: MarketFrame) -> bool:
        order = active.order
        if order.order_type == OrderType.MARKET:
            return True
        assert order.limit_price is not None
        return current.low <= order.limit_price <= current.high
