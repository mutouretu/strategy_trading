"""Replay simulator market frames through the same engine used in live trading.

This adapter is deliberately offline: it has an in-memory exchange and a
temporary SQLite store. A resting limit can fill only on a later frame. OHLC
bars cannot establish intrabar order, so no newly placed limit is filled on
the frame that placed it.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from ..application.fixed_engine import FixedGridEngine
from ..application.service import GridService
from ..domain import GridCell, OrderSide, OrderSnapshot, OrderStatus, PositionSnapshot, SymbolFilters
from ..infrastructure.sqlite_store import SQLiteStore
from ..ports.exchange import OrderNotFoundError
from ..runtime.supervisor import StrategySupervisor


class MarketFrameLike(Protocol):
    sequence: int
    instrument: str
    low: Decimal
    high: Decimal
    close: Decimal


class MarketSourceLike(Protocol):
    def reset(self, seed: int | None = None) -> MarketFrameLike: ...
    def next(self) -> MarketFrameLike: ...


@dataclass(frozen=True)
class ReplayFrame:
    sequence: int
    mark: Decimal
    filled_order_ids: tuple[int, ...]
    entry_order_count: int
    exit_order_count: int
    logical_position_qty: Decimal
    exchange_position_qty: Decimal
    completed_cycles: int


@dataclass(frozen=True)
class ReplayResult:
    frames: tuple[ReplayFrame, ...]
    final_cells: tuple[GridCell, ...]
    events: tuple[dict, ...]


class ReplayExchange:
    """Minimal exchange port with deterministic limit and position behavior."""

    def __init__(self, mark: Decimal, filters: SymbolFilters, balance: Decimal) -> None:
        self.mark = mark
        self.filters = filters
        self.available_balance = balance
        self.orders: dict[int, OrderSnapshot] = {}
        self.order_symbols: dict[int, str] = {}
        self.positions: dict[tuple[str, str], Decimal] = {}
        self.next_order_id = 1

    def get_mark_price(self, symbol: str) -> Decimal:
        return self.mark

    def get_symbol_filters(self, symbol: str) -> SymbolFilters:
        return self.filters

    def set_hedge_mode(self, enabled: bool) -> None:
        return None

    def set_leverage(self, symbol: str, leverage: int) -> None:
        return None

    def place_limit_order(
        self, symbol: str, side: OrderSide, position_side: str,
        quantity: Decimal, price: Decimal, client_order_id: str,
        post_only: bool = False,
    ) -> int:
        order_id = self.next_order_id
        self.next_order_id += 1
        self.orders[order_id] = OrderSnapshot(
            order_id, client_order_id, OrderStatus.NEW, side, price,
            quantity, position_side=position_side,
        )
        self.order_symbols[order_id] = symbol
        # A non-post-only catch-up limit is marketable at the observed mark.
        if not post_only and (
            (side == OrderSide.BUY and price >= self.mark)
            or (side == OrderSide.SELL and price <= self.mark)
        ):
            self.fill(order_id, self.mark)
        return order_id

    def place_market_order(
        self, symbol: str, side: OrderSide, position_side: str,
        quantity: Decimal, client_order_id: str,
    ) -> int:
        order_id = self.place_limit_order(
            symbol, side, position_side, quantity, self.mark,
            client_order_id, post_only=True,
        )
        self.fill(order_id, self.mark)
        return order_id

    def get_available_balance(self, asset: str = "USDT") -> Decimal:
        return self.available_balance

    def get_commission_rates(self, symbol: str) -> tuple[Decimal, Decimal]:
        return Decimal("0"), Decimal("0")

    def get_user_trades(
        self, symbol: str, *, start_time_ms: int | None = None,
        end_time_ms: int | None = None, from_id: int | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        # This replay validates order decisions, not fee or reinvestment
        # accounting. An empty trade history keeps the live accounting path
        # from treating offline fills as fee-verified cycles.
        return []

    def get_order(self, symbol: str, order_id: int) -> OrderSnapshot:
        if order_id not in self.orders or self.order_symbols[order_id] != symbol:
            raise OrderNotFoundError(f"{order_id} missing")
        return self.orders[order_id]

    def get_order_by_client_id(self, symbol: str, client_order_id: str) -> OrderSnapshot:
        matches = [
            order for order in self.orders.values()
            if self.order_symbols[order.order_id] == symbol
            and order.client_order_id == client_order_id
        ]
        if not matches:
            raise OrderNotFoundError(f"{client_order_id} missing")
        return max(matches, key=lambda order: order.order_id)

    def get_open_orders(self, symbol: str) -> list[OrderSnapshot]:
        return [
            order for order in self.orders.values()
            if self.order_symbols[order.order_id] == symbol
            and order.status in {OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED}
        ]

    def get_positions(self) -> list[PositionSnapshot]:
        return [
            PositionSnapshot(symbol, side, quantity)
            for (symbol, side), quantity in self.positions.items() if quantity > 0
        ]

    def cancel_order(self, symbol: str, order_id: int) -> OrderSnapshot:
        order = self.get_order(symbol, order_id)
        if order.status in {OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED}:
            order = replace(order, status=OrderStatus.CANCELED)
            self.orders[order_id] = order
        return order

    def fill(self, order_id: int, price: Decimal) -> None:
        order = self.orders[order_id]
        if order.status != OrderStatus.NEW:
            raise ValueError("only resting new orders can be filled")
        symbol = self.order_symbols[order_id]
        key = (symbol, order.position_side)
        signed_qty = order.original_qty if order.side == OrderSide.BUY else -order.original_qty
        self.positions[key] = self.positions.get(key, Decimal("0")) + signed_qty
        if self.positions[key] < 0:
            raise ValueError("replay exit exceeded its position")
        self.orders[order_id] = replace(
            order, status=OrderStatus.FILLED,
            executed_qty=order.original_qty, average_price=price,
        )

    def advance_bar(self, frame: MarketFrameLike) -> tuple[int, ...]:
        resting = tuple(self.get_open_orders(frame.instrument))
        filled: list[int] = []
        for order in resting:
            if frame.low <= order.price <= frame.high:
                self.fill(order.order_id, order.price)
                filled.append(order.order_id)
        self.mark = frame.close
        return tuple(filled)


class FixedGridReplay:
    """Use simulator MarketSource frames with the production fixed-grid engine."""

    def __init__(
        self, source: MarketSourceLike, *, symbol: str,
        lower_price: Decimal, upper_price: Decimal, grid_count: int,
        grid_budget_usdt: Decimal, filters: SymbolFilters,
        available_balance: Decimal | None = None,
    ) -> None:
        self.source = source
        self.symbol = symbol
        self.lower_price = lower_price
        self.upper_price = upper_price
        self.grid_count = grid_count
        self.grid_budget_usdt = grid_budget_usdt
        self.filters = filters
        self.available_balance = (
            grid_budget_usdt * 2 if available_balance is None
            else available_balance
        )

    def run(self, *, max_frames: int | None = None) -> ReplayResult:
        if max_frames is not None and max_frames < 1:
            raise ValueError("max_frames must be positive")
        first = self.source.reset()
        self._check_instrument(first)
        with tempfile.TemporaryDirectory(prefix="fixed-grid-replay-") as directory:
            root = Path(directory)
            store = SQLiteStore(root / "replay.sqlite3")
            exchange = ReplayExchange(first.close, self.filters, self.available_balance)
            service = GridService(store, StrategySupervisor(store, root / "logs"))
            config = service.create_fixed(
                self.symbol, self.lower_price, self.upper_price,
                self.grid_count, self.grid_budget_usdt, self.filters.tick_size,
            )
            store.mark_started(config.strategy_id)
            engine = FixedGridEngine(store, exchange, config.strategy_id)
            frames: list[ReplayFrame] = []
            engine.tick()
            frames.append(self._snapshot(first, (), store, exchange, config.strategy_id))
            while max_frames is None or len(frames) < max_frames:
                try:
                    frame = self.source.next()
                except StopIteration:
                    break
                self._check_instrument(frame)
                filled = exchange.advance_bar(frame)
                engine.tick()
                frames.append(self._snapshot(frame, filled, store, exchange, config.strategy_id))
            return ReplayResult(
                frames=tuple(frames),
                final_cells=tuple(store.list_cells(config.strategy_id)),
                events=tuple(store.list_events(config.strategy_id)),
            )

    def _check_instrument(self, frame: MarketFrameLike) -> None:
        if frame.instrument != self.symbol:
            raise ValueError(f"unexpected instrument {frame.instrument}")

    def _snapshot(
        self, frame: MarketFrameLike, filled: tuple[int, ...],
        store: SQLiteStore, exchange: ReplayExchange, strategy_id: str,
    ) -> ReplayFrame:
        cells = store.list_cells(strategy_id)
        orders = exchange.get_open_orders(self.symbol)
        return ReplayFrame(
            sequence=frame.sequence, mark=frame.close, filled_order_ids=filled,
            entry_order_count=sum(order.side == OrderSide.BUY for order in orders),
            exit_order_count=sum(order.side == OrderSide.SELL for order in orders),
            logical_position_qty=sum((cell.open_qty for cell in cells), Decimal("0")),
            exchange_position_qty=sum(
                (position.quantity for position in exchange.get_positions()
                 if position.symbol == self.symbol and position.position_side == "LONG"),
                Decimal("0"),
            ),
            completed_cycles=sum(cell.cycle_count for cell in cells),
        )
