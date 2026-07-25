from __future__ import annotations

from decimal import Decimal

from grid_server.domain import OrderSide, OrderSnapshot, OrderStatus, PositionSnapshot, SymbolFilters
from grid_server.exchange import OrderNotFoundError


class FakeExchange:
    def __init__(self, mark: Decimal = Decimal("0")) -> None:
        self.mark = mark
        self.filters = SymbolFilters(
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.001"),
            min_qty=Decimal("0.001"),
            min_notional=Decimal("0"),
        )
        self.orders: dict[int, OrderSnapshot] = {}
        self.order_symbols: dict[int, str] = {}
        self.order_position_sides: dict[int, str] = {}
        self.positions: dict[tuple[str, str], Decimal] = {}
        self.placed: list[dict] = []
        self.next_order_id = 1000
        self.hedge_mode = None
        self.leverage = None
        self.calls: dict[str, int] = {}

    def _called(self, name: str) -> None:
        self.calls[name] = self.calls.get(name, 0) + 1

    def get_mark_price(self, symbol: str) -> Decimal:
        self._called("get_mark_price")
        return self.mark

    def get_symbol_filters(self, symbol: str) -> SymbolFilters:
        self._called("get_symbol_filters")
        return self.filters

    def set_hedge_mode(self, enabled: bool) -> None:
        self._called("set_hedge_mode")
        self.hedge_mode = enabled

    def set_leverage(self, symbol: str, leverage: int) -> None:
        self._called("set_leverage")
        self.leverage = leverage

    def place_limit_order(self, symbol, side, position_side, quantity, price, client_order_id):
        self._called("place_limit_order")
        order_id = self.next_order_id
        self.next_order_id += 1
        snapshot = OrderSnapshot(
            order_id=order_id,
            client_order_id=client_order_id,
            status=OrderStatus.NEW,
            side=side,
            price=Decimal(price),
            original_qty=Decimal(quantity),
            position_side=position_side,
        )
        self.orders[order_id] = snapshot
        self.order_symbols[order_id] = symbol
        self.order_position_sides[order_id] = position_side
        self.placed.append(
            {
                "order_id": order_id,
                "symbol": symbol,
                "side": side,
                "position_side": position_side,
                "quantity": Decimal(quantity),
                "price": Decimal(price),
                "client_order_id": client_order_id,
            }
        )
        return order_id

    def get_order(self, symbol: str, order_id: int) -> OrderSnapshot:
        self._called("get_order")
        if order_id not in self.orders:
            raise OrderNotFoundError(f"{order_id} missing")
        return self.orders[order_id]

    def get_order_by_client_id(
        self,
        symbol: str,
        client_order_id: str,
    ) -> OrderSnapshot:
        self._called("get_order_by_client_id")
        matches = [
            order
            for order in self.orders.values()
            if self.order_symbols.get(order.order_id) == symbol
            and order.client_order_id == client_order_id
        ]
        if not matches:
            raise OrderNotFoundError(f"{client_order_id} missing")
        return max(matches, key=lambda order: order.order_id)

    def get_open_orders(self, symbol: str) -> list[OrderSnapshot]:
        self._called("get_open_orders")
        return [
            order for order in self.orders.values()
            if self.order_symbols.get(order.order_id) == symbol
            and order.status in (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED)
        ]

    def get_positions(self) -> list[PositionSnapshot]:
        self._called("get_positions")
        return [
            PositionSnapshot(symbol, position_side, quantity)
            for (symbol, position_side), quantity in self.positions.items()
            if quantity > 0
        ]

    def set_position(self, symbol: str, position_side: str, quantity: Decimal) -> None:
        self.positions[(symbol, position_side)] = max(Decimal("0"), Decimal(quantity))

    def cancel_order(self, symbol: str, order_id: int) -> OrderSnapshot:
        self._called("cancel_order")
        order = self.orders[order_id]
        canceled = OrderSnapshot(
            **{**order.__dict__, "status": OrderStatus.CANCELED}
        )
        self.orders[order_id] = canceled
        return canceled

    def fill(self, order_id: int, price: Decimal | None = None) -> None:
        order = self.orders[order_id]
        self._apply_execution(order_id, order.original_qty)
        self.orders[order_id] = OrderSnapshot(
            order_id=order.order_id,
            client_order_id=order.client_order_id,
            status=OrderStatus.FILLED,
            side=order.side,
            price=order.price,
            original_qty=order.original_qty,
            executed_qty=order.original_qty,
            average_price=price or order.price,
            position_side=order.position_side,
        )

    def partial_fill(self, order_id: int, quantity: Decimal, price: Decimal | None = None) -> None:
        order = self.orders[order_id]
        if quantity <= 0 or quantity >= order.original_qty:
            raise ValueError("partial fill quantity must be between zero and original quantity")
        self._apply_execution(order_id, quantity)
        self.orders[order_id] = OrderSnapshot(
            order_id=order.order_id,
            client_order_id=order.client_order_id,
            status=OrderStatus.PARTIALLY_FILLED,
            side=order.side,
            price=order.price,
            original_qty=order.original_qty,
            executed_qty=quantity,
            average_price=price or order.price,
            position_side=order.position_side,
        )

    def forget(self, order_id: int) -> None:
        self.orders.pop(order_id, None)
        self.order_symbols.pop(order_id, None)
        self.order_position_sides.pop(order_id, None)

    def _apply_execution(self, order_id: int, cumulative_quantity: Decimal) -> None:
        order = self.orders[order_id]
        delta = cumulative_quantity - order.executed_qty
        if delta <= 0:
            return
        symbol = self.order_symbols[order_id]
        position_side = self.order_position_sides[order_id]
        key = (symbol, position_side)
        current = self.positions.get(key, Decimal("0"))
        if (position_side == "LONG" and order.side == OrderSide.BUY) or (
            position_side == "SHORT" and order.side == OrderSide.SELL
        ):
            current += delta
        else:
            current -= delta
        self.positions[key] = max(Decimal("0"), current)
