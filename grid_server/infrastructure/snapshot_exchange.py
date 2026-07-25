from __future__ import annotations

from decimal import Decimal

from ..domain import (
    FuturesMarket,
    OrderSide,
    OrderSnapshot,
    OrderStatus,
    PositionSnapshot,
    SymbolFilters,
)
from ..ports.exchange import Exchange


class SnapshotExchange:
    """Shares one REST snapshot between every strategy due in a scheduler cycle."""

    def __init__(self, exchange: Exchange) -> None:
        self.exchange = exchange
        self.market_type = FuturesMarket(
            getattr(exchange, "market_type", FuturesMarket.USDM)
        )
        self._mark_prices: dict[str, Decimal] = {}
        self._open_orders: dict[str, dict[int, OrderSnapshot]] = {}
        self._positions: list[PositionSnapshot] | None = None
        self._filters: dict[str, SymbolFilters] = {}
        self._hedge_mode: bool | None = None
        self._leverages: dict[str, int] = {}

    def begin_cycle(self) -> None:
        self._mark_prices.clear()
        self._open_orders.clear()
        self._positions = None

    def get_mark_price(self, symbol: str) -> Decimal:
        if symbol not in self._mark_prices:
            self._mark_prices[symbol] = self.exchange.get_mark_price(symbol)
        return self._mark_prices[symbol]

    def get_symbol_filters(self, symbol: str) -> SymbolFilters:
        if symbol not in self._filters:
            self._filters[symbol] = self.exchange.get_symbol_filters(symbol)
        return self._filters[symbol]

    def set_hedge_mode(self, enabled: bool) -> None:
        if self._hedge_mode is None:
            self.exchange.set_hedge_mode(enabled)
            self._hedge_mode = enabled
        elif self._hedge_mode != enabled:
            raise ValueError("all strategies in one account must use the same position mode")

    def set_leverage(self, symbol: str, leverage: int) -> None:
        current = self._leverages.get(symbol)
        if current is None:
            self.exchange.set_leverage(symbol, leverage)
            self._leverages[symbol] = leverage
        elif current != leverage:
            raise ValueError(
                f"strategies sharing {symbol} must use one leverage: existing={current}, requested={leverage}"
            )

    def place_limit_order(
        self,
        symbol: str,
        side: OrderSide,
        position_side: str,
        quantity: Decimal,
        price: Decimal,
        client_order_id: str,
    ) -> int:
        order_id = self.exchange.place_limit_order(
            symbol, side, position_side, quantity, price, client_order_id
        )
        cached = self._open_orders.get(symbol)
        if cached is not None:
            cached[order_id] = OrderSnapshot(
                order_id=order_id,
                client_order_id=client_order_id,
                status=OrderStatus.NEW,
                side=side,
                price=price,
                original_qty=quantity,
                position_side=position_side,
            )
        return order_id

    def get_order(self, symbol: str, order_id: int) -> OrderSnapshot:
        open_order = self._orders_for_symbol(symbol).get(order_id)
        if open_order is not None:
            return open_order
        # Only an order that disappeared from the open-order snapshot needs an
        # individual query to distinguish FILLED from CANCELED/EXPIRED.
        return self.exchange.get_order(symbol, order_id)

    def get_order_by_client_id(
        self,
        symbol: str,
        client_order_id: str,
    ) -> OrderSnapshot:
        for order in self._orders_for_symbol(symbol).values():
            if order.client_order_id == client_order_id:
                return order
        return self.exchange.get_order_by_client_id(symbol, client_order_id)

    def get_open_orders(self, symbol: str) -> list[OrderSnapshot]:
        return list(self._orders_for_symbol(symbol).values())

    def get_positions(self) -> list[PositionSnapshot]:
        if self._positions is None:
            self._positions = self.exchange.get_positions()
        return list(self._positions)

    def invalidate_positions(self) -> None:
        self._positions = None

    def cancel_order(self, symbol: str, order_id: int) -> OrderSnapshot:
        canceled = self.exchange.cancel_order(symbol, order_id)
        cached = self._open_orders.get(symbol)
        if cached is not None:
            cached.pop(order_id, None)
        return canceled

    def _orders_for_symbol(self, symbol: str) -> dict[int, OrderSnapshot]:
        if symbol not in self._open_orders:
            self._open_orders[symbol] = {
                order.order_id: order for order in self.exchange.get_open_orders(symbol)
            }
        return self._open_orders[symbol]
