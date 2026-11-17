from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from grid_server.application.grid_performance import strategy_performance
from grid_server.application.service import GridService
from grid_server.domain import (
    ExchangeVenue, OrderSide, OrderSnapshot, OrderStatus,
    PositionSnapshot, StrategyStatus, SymbolFilters,
)
from grid_server.infrastructure.sqlite_store import SQLiteStore


class ReconfigurationExchange:
    def __init__(self, quantity: Decimal) -> None:
        self.quantity = quantity
        self.mark = Decimal("80")
        self.orders: list[OrderSnapshot] = []
        self.adjustment: OrderSnapshot | None = None

    def get_symbol_filters(self, _symbol: str) -> SymbolFilters:
        return SymbolFilters(Decimal("0.01"), Decimal("0.01"), Decimal("0.01"))

    def get_mark_price(self, _symbol: str) -> Decimal:
        return self.mark

    def get_open_orders(self, _symbol: str) -> list[OrderSnapshot]:
        return self.orders

    def get_positions(self) -> list[PositionSnapshot]:
        return [PositionSnapshot("SOL-USDT-SWAP", "LONG", self.quantity)]

    def get_order(self, _symbol: str, _order_id: int) -> OrderSnapshot:
        assert self.adjustment is not None
        return self.adjustment

    def get_user_trades(self, _symbol: str, **_kwargs) -> list[dict]:
        return []


def trade(trade_id: int, order_id: int, side: str, qty: Decimal,
          price: Decimal, fee: str) -> dict:
    return {
        "id": trade_id, "orderId": order_id, "side": side,
        "qty": str(qty), "price": str(price), "realizedPnl": "0",
        "commission": fee, "commissionAsset": "USDT",
        "time": int(datetime.now(timezone.utc).timestamp() * 1000) + trade_id,
    }


def test_same_strategy_keeps_history_and_restarts_cell_accounting() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteStore(Path(directory) / "test.sqlite3", venue=ExchangeVenue.OKX)
        service = GridService(store, SimpleNamespace())
        config = service.create_fixed(
            "SOL-USDT-SWAP", Decimal("70"), Decimal("90"), 4,
            Decimal("200"), Decimal("0.01"), venue=ExchangeVenue.OKX,
            auto_reinvest_enabled=True, auto_reinvest_percent=Decimal("70"),
        )
        store.mark_started(config.strategy_id)
        started_at = store.get_first_started_at(config.strategy_id)
        cells = store.list_cells(config.strategy_id)
        sell_cells = [cell for cell in cells if cell.sell_price > Decimal("80")]
        allocations = {
            cell.cell_id: (cell.budget_usdt / cell.buy_price // Decimal("0.01")) * Decimal("0.01")
            for cell in sell_cells
        }
        total_qty = sum(allocations.values(), Decimal("0"))
        action_id = f"seed:{config.strategy_id}"
        store.create_capital_action(action_id, config.strategy_id, "seed", Decimal("200"))
        store.plan_capital_action(action_id, list(allocations), allocations, total_qty, "seedclient")
        store.mark_capital_action_submitted(action_id, 100)
        store.apply_capital_action(action_id, allocations, total_qty)
        store.append_event(config.strategy_id, "CAPITAL_ACTION_APPLIED",
                           {"operation": "seed", "average_price": "80.10"})
        first = store.get_cell(config.strategy_id, sell_cells[0].cell_id)
        assert first is not None
        store.append_event(config.strategy_id, "EXIT_PLACED", {"order_id": 101}, first.cell_id)
        store.append_event(config.strategy_id, "ENTRY_PLACED", {"order_id": 102}, first.cell_id)
        store.append_event(config.strategy_id, "CYCLE_CLOSED", {"cycle_count": 1}, first.cell_id)
        first.cycle_count = 1
        store.save_cell(first)
        store.save_strategy_trades(config.strategy_id, [
            trade(1, 100, "BUY", total_qty, Decimal("80.10"), "0.0242"),
            trade(2, 101, "SELL", allocations[first.cell_id], first.sell_price, "0.004"),
            trade(3, 102, "BUY", allocations[first.cell_id], first.buy_price, "0.004"),
        ])
        store.set_status(config.strategy_id, StrategyStatus.STOPPED)
        old = strategy_performance(store, store.get_strategy(config.strategy_id), coverage_complete=True)
        assert old["completed_cycles"] == 1
        assert Decimal(old["net_grid_profit_usdt"]) > 0
        assert old["initial_entry_price"] == "80.10"

        exchange = ReconfigurationExchange(total_qty)
        plan = service.preview_fixed_reconfiguration(
            config.strategy_id, Decimal("60"), Decimal("100"), 4, exchange,
        )
        assert plan["delta_qty"] == Decimal("-0.01")
        assert plan["config"].grid_budget_usdt == Decimal("200")
        exchange.adjustment = OrderSnapshot(
            200, "adjustment", OrderStatus.FILLED, OrderSide.SELL,
            Decimal("80"), Decimal("0.01"), Decimal("0.01"), Decimal("80"), "LONG",
        )
        exchange.quantity = plan["target_qty"]
        new_config = service.apply_fixed_reconfiguration(plan, exchange, adjustment_order_id=200)
        new_cells = store.list_cells(config.strategy_id)
        assert new_config.strategy_id == config.strategy_id
        assert new_config.grid_generation == 1
        assert new_config.grid_budget_usdt == config.grid_budget_usdt
        assert store.get_first_started_at(config.strategy_id) == started_at
        assert {cell.cell_id for cell in new_cells}.isdisjoint({cell.cell_id for cell in cells})
        assert all(cell.cycle_count == 0 for cell in new_cells)
        assert sum((cell.open_qty for cell in new_cells), Decimal("0")) == plan["target_qty"]
        after = strategy_performance(store, new_config, coverage_complete=True)
        assert after["net_grid_profit_usdt"] == old["net_grid_profit_usdt"]
        assert after["completed_cycles"] == 1
        assert after["initial_entry_price"] == "80.10"
        assert sum((Decimal(after["cell_net_grid_profit_usdt"][cell.cell_id])
                    for cell in new_cells), Decimal("0")) == 0

        closing = next(cell for cell in new_cells if cell.open_qty > 0)
        store.append_event(config.strategy_id, "EXIT_PLACED", {"order_id": 201}, closing.cell_id)
        store.save_strategy_trades(config.strategy_id, [
            trade(4, 201, "SELL", closing.open_qty, closing.sell_price, "0.004")
        ])
        later = strategy_performance(store, new_config, coverage_complete=True)
        assert later["accounting_complete"]
        assert Decimal(later["net_grid_profit_usdt"]) > Decimal(old["net_grid_profit_usdt"])
        assert Decimal(later["cell_net_grid_profit_usdt"][closing.cell_id]) > 0


def test_same_budget_reconfiguration_can_increase_position_and_carry_fees() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteStore(Path(directory) / "buy.sqlite3", venue=ExchangeVenue.OKX)
        service = GridService(store, SimpleNamespace())
        config = service.create_fixed(
            "SOL-USDT-SWAP", Decimal("69"), Decimal("77"), 20,
            Decimal("400"), Decimal("0.01"), venue=ExchangeVenue.OKX,
        )
        store.mark_started(config.strategy_id)
        cells = store.list_cells(config.strategy_id)
        sell_cells = [cell for cell in cells if cell.sell_price > Decimal("72.71")]
        allocations = {
            cell.cell_id: (cell.budget_usdt / cell.buy_price // Decimal("0.01")) * Decimal("0.01")
            for cell in sell_cells
        }
        old_qty = sum(allocations.values(), Decimal("0"))
        action_id = f"seed:{config.strategy_id}"
        store.create_capital_action(action_id, config.strategy_id, "seed", Decimal("400"))
        store.plan_capital_action(action_id, list(allocations), allocations, old_qty, "seedclient")
        store.mark_capital_action_submitted(action_id, 100)
        store.apply_capital_action(action_id, allocations, old_qty)
        store.save_strategy_trades(config.strategy_id, [
            trade(1, 100, "BUY", old_qty, Decimal("72.71"), "0.04")
        ])
        store.set_status(config.strategy_id, StrategyStatus.STOPPED)
        exchange = ReconfigurationExchange(old_qty)
        exchange.mark = Decimal("72.71")
        plan = service.preview_fixed_reconfiguration(
            config.strategy_id, Decimal("68"), Decimal("80"), 24, exchange,
        )
        assert plan["delta_qty"] > 0
        assert plan["target_qty"] * exchange.mark < Decimal("500")
        exchange.adjustment = OrderSnapshot(
            200, "adjustment", OrderStatus.FILLED, OrderSide.BUY,
            Decimal("0"), plan["delta_qty"], plan["delta_qty"], exchange.mark, "LONG",
        )
        exchange.quantity = plan["target_qty"]
        store.save_strategy_trades(config.strategy_id, [
            trade(2, 200, "BUY", plan["delta_qty"], exchange.mark, "0.005")
        ])
        new_config = service.apply_fixed_reconfiguration(plan, exchange, adjustment_order_id=200)
        assert new_config.grid_budget_usdt == Decimal("400")
        summary = strategy_performance(store, new_config, coverage_complete=True)
        assert summary["accounting_complete"]
        assert sum((Decimal(value) for value in summary["open_buy_fees_by_cell_usdt"].values()), Decimal("0")).quantize(Decimal("0.00000001")) == Decimal("0.04500000")
        assert sum((cell.cycle_count for cell in store.list_cells(config.strategy_id))) == 0

        second_plan = service.preview_fixed_reconfiguration(
            config.strategy_id, Decimal("69"), Decimal("77"), 20, exchange,
            grid_budget_usdt=Decimal("380"),
        )
        assert second_plan["delta_qty"] < 0
        exchange.adjustment = OrderSnapshot(
            201, "second-adjustment", OrderStatus.FILLED, OrderSide.SELL,
            Decimal("0"), abs(second_plan["delta_qty"]), abs(second_plan["delta_qty"]),
            exchange.mark, "LONG",
        )
        exchange.quantity = second_plan["target_qty"]
        generation_two = service.apply_fixed_reconfiguration(
            second_plan, exchange, adjustment_order_id=201,
        )
        assert generation_two.grid_generation == 2
        assert generation_two.grid_budget_usdt == Decimal("380")
        second_cells = store.list_cells(config.strategy_id)
        assert all(cell.budget_usdt == Decimal("19") for cell in second_cells)
        assert {cell.cell_id for cell in second_cells}.isdisjoint(
            {cell.cell_id for cell in cells} | {cell.cell_id for cell in plan["cells"]}
        )
        second_summary = strategy_performance(store, generation_two, coverage_complete=True)
        assert second_summary["accounting_complete"]
        assert second_summary["net_grid_profit_usdt"] == summary["net_grid_profit_usdt"]
        assert sum((Decimal(value) for value in second_summary["open_buy_fees_by_cell_usdt"].values()), Decimal("0")).quantize(Decimal("0.00000001")) == (Decimal("0.045") * second_plan["target_qty"] / plan["target_qty"]).quantize(Decimal("0.00000001"))
