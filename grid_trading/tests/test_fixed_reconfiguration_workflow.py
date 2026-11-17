from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from grid_server.application.fixed_reconfiguration import FixedReconfigurationWorkflow
from grid_server.application.grid_performance import strategy_performance
from grid_server.application.service import GridService
from grid_server.domain import (
    CellStage, ExchangeVenue, OrderSide, OrderSnapshot, OrderStatus, PositionSnapshot,
    StrategyStatus, SymbolFilters,
)
from grid_server.infrastructure.sqlite_store import SQLiteStore


def fill(number: int, order_id: int, side: str, qty: Decimal, price: Decimal, fee: str) -> dict:
    return {
        "id": number, "orderId": order_id, "side": side, "qty": str(qty),
        "price": str(price), "realizedPnl": "0", "commission": fee,
        "commissionAsset": "USDT",
        "time": int(datetime.now(timezone.utc).timestamp() * 1000) + number,
    }


class Supervisor:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store
        self.shutdowns = 0

    def stop(self, strategy_id: str) -> None:
        self.store.set_status(strategy_id, StrategyStatus.STOPPED)

    def shutdown_if_idle(self) -> None:
        self.shutdowns += 1

    def start(self, strategy_id: str) -> int:
        self.store.mark_started(strategy_id)
        return 42


class Exchange:
    def __init__(self, qty: Decimal) -> None:
        self.qty = qty
        self.mark = Decimal("80")
        self.orders: dict[int, OrderSnapshot] = {}
        self.clients: dict[str, int] = {}
        self.trades: list[dict] = []
        self.market_calls = 0

    def get_symbol_filters(self, _symbol: str) -> SymbolFilters:
        return SymbolFilters(Decimal("0.01"), Decimal("0.01"), Decimal("0.01"))

    def get_mark_price(self, _symbol: str) -> Decimal:
        return self.mark

    def get_open_orders(self, _symbol: str) -> list[OrderSnapshot]:
        return [order for order in self.orders.values()
                if order.status in {OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED}]

    def cancel_order(self, _symbol: str, order_id: int) -> OrderSnapshot:
        old = self.orders[order_id]
        closed = OrderSnapshot(
            old.order_id, old.client_order_id, OrderStatus.CANCELED,
            old.side, old.price, old.original_qty, old.executed_qty,
            old.average_price, old.position_side,
        )
        self.orders[order_id] = closed
        return closed

    def get_order(self, _symbol: str, order_id: int) -> OrderSnapshot:
        return self.orders[order_id]

    def get_order_by_client_id(self, _symbol: str, client_id: str) -> OrderSnapshot:
        return self.orders[self.clients[client_id]]

    def get_positions(self) -> list[PositionSnapshot]:
        return [PositionSnapshot("SOL-USDT-SWAP", "LONG", self.qty)] if self.qty else []

    def get_available_balance(self, _asset: str) -> Decimal:
        return Decimal("1000")

    def place_market_order(self, _symbol: str, side: OrderSide, position_side: str,
                           qty: Decimal, client_id: str) -> int:
        assert position_side == "LONG" and client_id not in self.clients
        self.market_calls += 1
        self.qty += qty if side == OrderSide.BUY else -qty
        order_id = 200
        self.orders[order_id] = OrderSnapshot(
            order_id, client_id, OrderStatus.FILLED, side,
            Decimal("0"), qty, qty, self.mark, "LONG",
        )
        self.clients[client_id] = order_id
        self.trades.append(fill(4, order_id, side.value, qty, self.mark, "0.005"))
        return order_id

    def get_user_trades(self, _symbol: str, **kwargs) -> list[dict]:
        if kwargs.get("from_id") is not None:
            return [trade for trade in self.trades if trade["id"] >= kwargs["from_id"]]
        return [trade for trade in self.trades
                if kwargs["start_time_ms"] <= trade["time"] <= kwargs["end_time_ms"]]


def test_historical_filled_entry_reference_is_not_counted_twice() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteStore(Path(directory) / "old-entry.sqlite3", venue=ExchangeVenue.OKX)
        config = GridService(store, Supervisor(store)).create_fixed(
            "SOL-USDT-SWAP", Decimal("70"), Decimal("90"), 4,
            Decimal("200"), Decimal("0.01"), venue=ExchangeVenue.OKX,
        )
        cell = store.list_cells(config.strategy_id)[0]
        cell.stage = CellStage.PENDING_EXIT
        cell.open_qty = Decimal("0.57")
        cell.entry_order_id = 101
        cell.entry_client_id = "oldentry"
        store.save_cell(cell)
        store.set_status(config.strategy_id, StrategyStatus.STOPPED)
        order = OrderSnapshot(
            101, "oldentry", OrderStatus.FILLED, OrderSide.BUY,
            cell.buy_price, Decimal("0.57"), Decimal("0.57"),
            cell.buy_price, "LONG",
        )
        assert store.settle_reconfiguration_order(config.strategy_id, cell.cell_id, "entry", order)
        after = store.get_cell(config.strategy_id, cell.cell_id)
        assert after is not None and after.open_qty == Decimal("0.57")
        assert after.entry_order_id is None
        assert not store.settle_reconfiguration_order(config.strategy_id, cell.cell_id, "entry", order)


def test_workflow_quiesces_partial_orders_then_reconfigures_and_restarts() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteStore(Path(directory) / "workflow.sqlite3", venue=ExchangeVenue.OKX)
        supervisor = Supervisor(store)
        service = GridService(store, supervisor)
        config = service.create_fixed(
            "SOL-USDT-SWAP", Decimal("70"), Decimal("90"), 4,
            Decimal("200"), Decimal("0.01"), venue=ExchangeVenue.OKX,
        )
        store.mark_started(config.strategy_id)
        cells = store.list_cells(config.strategy_id)
        sell = [cell for cell in cells if cell.sell_price > Decimal("80")]
        buy = next(cell for cell in cells if cell.sell_price <= Decimal("80"))
        allocations = {
            cell.cell_id: (cell.budget_usdt / cell.buy_price // Decimal("0.01")) * Decimal("0.01")
            for cell in sell
        }
        seed_qty = sum(allocations.values(), Decimal("0"))
        action_id = f"seed:{config.strategy_id}"
        store.create_capital_action(action_id, config.strategy_id, "seed", Decimal("200"))
        store.plan_capital_action(action_id, list(allocations), allocations, seed_qty, "seedclient")
        store.mark_capital_action_submitted(action_id, 100)
        store.apply_capital_action(action_id, allocations, seed_qty)
        store.append_event(config.strategy_id, "CAPITAL_ACTION_APPLIED",
                           {"operation": "seed", "average_price": "80"})
        sell_cell = store.get_cell(config.strategy_id, sell[0].cell_id)
        assert sell_cell is not None
        sell_cell.exit_order_id = 101
        sell_cell.exit_client_id = "oldexit"
        store.save_cell(sell_cell)
        buy.entry_order_id = 102
        buy.entry_client_id = "oldentry"
        store.save_cell(buy)
        store.append_event(config.strategy_id, "EXIT_PLACED", {"order_id": 101}, sell_cell.cell_id)
        store.append_event(config.strategy_id, "ENTRY_PLACED", {"order_id": 102}, buy.cell_id)
        store.save_strategy_trades(config.strategy_id, [
            fill(1, 100, "BUY", seed_qty, Decimal("80"), "0.020"),
            fill(2, 101, "SELL", Decimal("0.10"), sell_cell.sell_price, "0.002"),
            fill(3, 102, "BUY", Decimal("0.05"), buy.buy_price, "0.001"),
        ])
        exchange = Exchange(seed_qty - Decimal("0.10") + Decimal("0.05"))
        exchange.orders[101] = OrderSnapshot(
            101, "oldexit", OrderStatus.PARTIALLY_FILLED, OrderSide.SELL,
            sell_cell.sell_price, sell_cell.open_qty, Decimal("0.10"), sell_cell.sell_price, "LONG",
        )
        exchange.orders[102] = OrderSnapshot(
            102, "oldentry", OrderStatus.PARTIALLY_FILLED, OrderSide.BUY,
            buy.buy_price, Decimal("0.50"), Decimal("0.05"), buy.buy_price, "LONG",
        )
        workflow = FixedReconfigurationWorkflow(store, service, exchange)
        preview = workflow.preview(
            config.strategy_id, Decimal("75"), Decimal("95"), 5, Decimal("250"),
        )
        assert preview["grid_budget_usdt"] == "250"
        action = workflow.request(
            "reconfig-workflow-1", config.strategy_id,
            Decimal("75"), Decimal("95"), 5, Decimal("250"),
        )
        result = workflow.execute(action["id"])
        assert result["status"] == "applied"
        assert result["strategy_status"] == "starting"
        assert supervisor.shutdowns == 1
        assert exchange.market_calls == 1
        after = store.get_strategy(config.strategy_id)
        assert after is not None and after.grid_generation == 1
        assert after.grid_budget_usdt == Decimal("250")
        assert all(cell.budget_usdt == Decimal("50") for cell in store.list_cells(config.strategy_id))
        assert not exchange.get_open_orders(config.symbol)
        assert sum((cell.open_qty for cell in store.list_cells(config.strategy_id)), Decimal("0")) == exchange.qty
        performance = strategy_performance(store, after, coverage_complete=True)
        assert performance["accounting_complete"]
        assert performance["initial_entry_price"] == "80"
        assert Decimal(performance["net_grid_profit_usdt"]) > 0
        assert workflow.execute(action["id"])["status"] == "applied"
        assert exchange.market_calls == 1
