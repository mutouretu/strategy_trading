from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from .domain import OrderSide, OrderSnapshot, PositionSnapshot, SymbolFilters


class OrderNotFoundError(RuntimeError):
    """The exchange no longer has a queryable record for an order id."""


class Exchange(Protocol):
    def get_mark_price(self, symbol: str) -> Decimal: ...

    def get_symbol_filters(self, symbol: str) -> SymbolFilters: ...

    def set_hedge_mode(self, enabled: bool) -> None: ...

    def set_leverage(self, symbol: str, leverage: int) -> None: ...

    def place_limit_order(
        self,
        symbol: str,
        side: OrderSide,
        position_side: str,
        quantity: Decimal,
        price: Decimal,
        client_order_id: str,
    ) -> int: ...

    def get_order(self, symbol: str, order_id: int) -> OrderSnapshot: ...

    def get_open_orders(self, symbol: str) -> list[OrderSnapshot]: ...

    def get_positions(self) -> list[PositionSnapshot]: ...

    def cancel_order(self, symbol: str, order_id: int) -> OrderSnapshot: ...
