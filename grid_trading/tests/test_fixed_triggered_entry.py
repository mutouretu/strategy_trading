from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grid_server.application.fixed_engine import FixedGridEngine
from grid_server.application.grid_performance import strategy_performance
from grid_server.application.service import GridService
from grid_server.domain import CellStage, FixedEntryMode, OrderSide, StrategyStatus
from grid_server.infrastructure.sqlite_store import SQLiteStore
from grid_server.infrastructure.binance import BinanceAPIError
from grid_server.interfaces.api import create_app
from tests.fakes import FakeExchange


def test_triggered_fixed_grid_starts_without_position_and_pairs_fills() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteStore(Path(directory) / "triggered.sqlite3")
        exchange = FakeExchange(Decimal("100"))
        config = GridService(store).create_fixed(
            "SOLUSDT", Decimal("80"), Decimal("120"), 4,
            Decimal("400"), Decimal("0.01"),
            fixed_entry_mode=FixedEntryMode.PRICE_TRIGGERED,
        )
        store.mark_started(config.strategy_id)
        engine = FixedGridEngine(store, exchange, config.strategy_id)
        with pytest.raises(ValueError, match="not yet supported"):
            GridService(store).preview_fixed_reconfiguration(
                config.strategy_id, Decimal("81"), Decimal("121"), 4, exchange,
            )

        engine.tick()
        cells = store.list_cells(config.strategy_id)
        assert store.get_strategy(config.strategy_id).fixed_entry_mode == FixedEntryMode.PRICE_TRIGGERED
        assert exchange.calls.get("place_market_order", 0) == 0
        assert exchange.get_positions() == []
        assert len([cell for cell in cells if cell.stage == CellStage.PENDING_ENTRY]) == 3
        assert cells[-1].stage == CellStage.UNTRIGGERED
        assert all(order["side"] == OrderSide.BUY for order in exchange.placed)
        seed = store.get_capital_action(f"seed:{config.strategy_id}")
        assert seed is not None and seed["status"] == "applied" and seed["order_id"] is None
        order_count = len(exchange.placed)

        # Restarting after the zero-position seed keeps the armed buy orders.
        FixedGridEngine(store, exchange, config.strategy_id).tick()
        assert len(exchange.placed) == order_count
        exchange.mark = Decimal("110")
        engine.tick()
        upper = store.list_cells(config.strategy_id)[-1]
        assert upper.stage == CellStage.PENDING_ENTRY and upper.entry_order_id is not None
        assert exchange.get_positions() == []

        exchange.fill(upper.entry_order_id, upper.buy_price)
        engine.tick()
        upper = store.get_cell(config.strategy_id, upper.cell_id)
        assert upper is not None and upper.stage == CellStage.PENDING_EXIT
        assert upper.exit_order_id is not None
        assert exchange.orders[upper.exit_order_id].side == OrderSide.SELL
        assert sum(p.quantity for p in exchange.get_positions()) == upper.open_qty
        store.save_strategy_trades(config.strategy_id, [{
            "id": 1, "orderId": upper.entry_order_id, "side": "BUY",
            "qty": str(upper.open_qty), "price": str(upper.buy_price),
            "realizedPnl": "0", "commission": "0.01", "commissionAsset": "USDT",
            "time": int(datetime.now(timezone.utc).timestamp() * 1000),
        }])
        performance = strategy_performance(store, store.get_strategy(config.strategy_id), coverage_complete=True)
        assert Decimal(performance["initial_entry_price"]) == upper.buy_price

        exit_order_id = upper.exit_order_id
        filled_qty = upper.open_qty
        sell_price = upper.sell_price
        exchange.fill(exit_order_id, sell_price)
        store.save_strategy_trades(config.strategy_id, [{
            "id": 2, "orderId": exit_order_id, "side": "SELL",
            "qty": str(filled_qty), "price": str(sell_price),
            "realizedPnl": "0", "commission": "0.02", "commissionAsset": "USDT",
            "time": int(datetime.now(timezone.utc).timestamp() * 1000) + 1,
        }])
        exchange.mark = upper.sell_price
        engine.tick()
        upper = store.get_cell(config.strategy_id, upper.cell_id)
        assert upper is not None and upper.cycle_count == 1
        assert upper.stage == CellStage.PENDING_ENTRY and upper.entry_order_id is not None
        assert exchange.get_positions() == []
        assert exchange.calls.get("place_market_order", 0) == 0
        performance = strategy_performance(store, store.get_strategy(config.strategy_id), coverage_complete=True)
        assert performance["completed_cycles"] == 1
        assert Decimal(performance["net_grid_profit_usdt"]) == (
            filled_qty * (sell_price - upper.buy_price) - Decimal("0.03")
        )


def test_triggered_topup_preserves_uncrossed_buy_levels() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteStore(Path(directory) / "triggered-topup.sqlite3")
        exchange = FakeExchange(Decimal("100"))
        config = GridService(store).create_fixed(
            "SOLUSDT", Decimal("80"), Decimal("120"), 4,
            Decimal("400"), Decimal("0.01"),
            fixed_entry_mode=FixedEntryMode.PRICE_TRIGGERED,
        )
        store.mark_started(config.strategy_id)
        engine = FixedGridEngine(store, exchange, config.strategy_id)
        engine.tick()
        upper = store.list_cells(config.strategy_id)[-1]
        assert upper.stage == CellStage.UNTRIGGERED
        store.create_capital_action("topup-uncrossed", config.strategy_id, "topup", Decimal("40"))
        engine.tick()
        updated = store.get_cell(config.strategy_id, upper.cell_id)
        assert updated is not None
        assert updated.stage == CellStage.UNTRIGGERED
        assert updated.entry_order_id is None
        assert updated.budget_usdt == Decimal("110")
        assert exchange.calls.get("place_market_order", 0) == 0


def test_upper_seed_frontier_never_spreads_on_flat_or_falling_price() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "upper-seed.sqlite3"
        store = SQLiteStore(path)
        config = GridService(store).create_fixed(
            "SOLUSDT", Decimal("80"), Decimal("120"), 40,
            Decimal("4000"), Decimal("0.01"),
            fixed_entry_mode=FixedEntryMode.PRICE_TRIGGERED,
            upper_seed_count=3,
        )
        store.mark_started(config.strategy_id)
        assert store.arm_upper_seed_cells(config.strategy_id, Decimal("100")) == 3
        first = [e for e in store.list_events(config.strategy_id)
                 if e["event_type"] == "UPPER_SEED_TRIGGERED"]
        assert len(first) == 3
        frontier = store.get_strategy(config.strategy_id).upper_seed_frontier
        assert frontier is not None
        assert store.arm_upper_seed_cells(config.strategy_id, Decimal("100")) == 0
        assert store.arm_upper_seed_cells(config.strategy_id, Decimal("90")) == 0
        restarted = SQLiteStore(path)
        assert restarted.arm_upper_seed_cells(config.strategy_id, Decimal("90")) == 0
        assert restarted.get_strategy(config.strategy_id).upper_seed_frontier == frontier
        assert restarted.arm_upper_seed_cells(config.strategy_id, Decimal("105")) == 0
        assert sum(e["event_type"] == "UPPER_SEED_TRIGGERED"
                   for e in restarted.list_events(config.strategy_id)) == 3

        # A completed sale releases only one slot. A new cell is preactivated
        # only after price has reached a higher frontier.
        old = restarted.get_cell(config.strategy_id, first[0]["cell_id"])
        assert old is not None
        old.cycle_count += 1
        old.stage = CellStage.PENDING_ENTRY
        restarted.save_cell(old)
        assert restarted.arm_upper_seed_cells(config.strategy_id, Decimal("105")) == 1
        assert sum(e["event_type"] == "UPPER_SEED_TRIGGERED"
                   for e in restarted.list_events(config.strategy_id)) == 4


def test_upper_seed_uses_three_price_protected_buys_and_pairs_sells() -> None:
    class CrossingExchange(FakeExchange):
        def place_limit_order(self, symbol, side, position_side, quantity, price, client_order_id, post_only=False):
            order_id = super().place_limit_order(
                symbol, side, position_side, quantity, price, client_order_id, post_only,
            )
            if side == OrderSide.BUY and not post_only and Decimal(price) >= self.mark:
                self.fill(order_id, self.mark)
            return order_id

    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteStore(Path(directory) / "upper-seed-orders.sqlite3")
        exchange = CrossingExchange(Decimal("100"))
        config = GridService(store).create_fixed(
            "SOLUSDT", Decimal("80"), Decimal("120"), 40,
            Decimal("4000"), Decimal("0.01"),
            fixed_entry_mode=FixedEntryMode.PRICE_TRIGGERED,
            upper_seed_count=3,
        )
        store.mark_started(config.strategy_id)
        engine = FixedGridEngine(store, exchange, config.strategy_id)
        engine.tick()
        upper_buys = [order for order in exchange.placed
                      if order["side"] == OrderSide.BUY and not order["post_only"]]
        assert len(upper_buys) == 3
        assert exchange.calls.get("place_market_order", 0) == 0
        engine.tick()
        seeded = [cell for cell in store.list_cells(config.strategy_id)
                  if cell.stage == CellStage.PENDING_EXIT and cell.buy_price > Decimal("100")]
        assert len(seeded) == 3
        assert all(cell.exit_order_id is not None for cell in seeded)
        first = seeded[0]
        exchange.fill(first.exit_order_id, first.sell_price)
        exchange.mark = first.sell_price
        engine.tick()
        assert store.upper_seed_active_count(config.strategy_id) <= 3
        assert sum(e["event_type"] == "UPPER_SEED_TRIGGERED"
                   for e in store.list_events(config.strategy_id)) == 4
        exchange.mark = Decimal("90")
        engine.tick()
        assert sum((cell.open_qty for cell in store.list_cells(config.strategy_id)), Decimal("0")) > 0
        assert store.upper_seed_active_count(config.strategy_id) <= 3
        assert sum(e["event_type"] == "UPPER_SEED_TRIGGERED"
                   for e in store.list_events(config.strategy_id)) == 4


def test_upper_seed_topup_does_not_activate_the_rest_of_the_range() -> None:
    class CrossingExchange(FakeExchange):
        def place_limit_order(self, symbol, side, position_side, quantity, price, client_order_id, post_only=False):
            order_id = super().place_limit_order(
                symbol, side, position_side, quantity, price, client_order_id, post_only,
            )
            if side == OrderSide.BUY and not post_only and Decimal(price) >= self.mark:
                self.fill(order_id, self.mark)
            return order_id

    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteStore(Path(directory) / "upper-seed-topup.sqlite3")
        exchange = CrossingExchange(Decimal("100"))
        config = GridService(store).create_fixed(
            "SOLUSDT", Decimal("80"), Decimal("120"), 40,
            Decimal("4000"), Decimal("0.01"),
            fixed_entry_mode=FixedEntryMode.PRICE_TRIGGERED,
            upper_seed_count=3,
        )
        store.mark_started(config.strategy_id)
        engine = FixedGridEngine(store, exchange, config.strategy_id)
        engine.tick()
        engine.tick()
        untouched = {
            cell.cell_id for cell in store.list_cells(config.strategy_id)
            if cell.stage == CellStage.UNTRIGGERED
        }
        assert untouched
        store.create_capital_action("upper-seed-topup", config.strategy_id, "topup", Decimal("400"))
        engine.tick()
        for mark in (Decimal("100"), Decimal("90"), Decimal("100")) * 5:
            exchange.mark = mark
            engine.tick()
        cells = store.list_cells(config.strategy_id)
        assert untouched.issubset({cell.cell_id for cell in cells if cell.stage == CellStage.UNTRIGGERED})
        assert store.upper_seed_active_count(config.strategy_id) == 3
        assert sum(e["event_type"] == "UPPER_SEED_TRIGGERED"
                   for e in store.list_events(config.strategy_id)) == 3
        assert store.get_strategy(config.strategy_id).grid_budget_usdt == Decimal("4400")
        assert exchange.calls.get("place_market_order", 0) == 1  # Aggregate top-up only.


def test_upper_seed_preview_and_running_setting_are_persisted() -> None:
    with tempfile.TemporaryDirectory() as directory:
        exchange = FakeExchange(Decimal("100"))
        client = TestClient(create_app(Path(directory) / "upper-seed-api.sqlite3", lambda: exchange))
        payload = {
            "symbol": "SOLUSDT", "lower_price": "80", "upper_price": "120",
            "grid_count": 40, "grid_budget_usdt": "4000",
            "fixed_entry_mode": "price_triggered", "upper_seed_count": 3,
        }
        preview = client.post("/fixed-grids/preview", json=payload)
        assert preview.status_code == 200, preview.text
        assert preview.json()["allocation"]["initial_upper_seed_cell_count"] == 3
        assert Decimal(preview.json()["allocation"]["estimated_upper_seed_buy_usdt"]) > 0
        created = client.post("/fixed-grids", json=payload)
        assert created.status_code == 201, created.text
        sid = created.json()["strategy_id"]
        assert created.json()["upper_seed_count"] == 3
        disabled = client.post(f"/strategies/{sid}/upper-seed", json={"count": 0})
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["upper_seed_count"] == 0
        enabled = client.post(f"/strategies/{sid}/upper-seed", json={"count": 3})
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["upper_seed_count"] == 3
        invalid = client.post(f"/strategies/{sid}/upper-seed", json={"count": 2})
        assert invalid.status_code == 400


def test_triggered_fixed_grid_preview_has_no_seed_buy() -> None:
    with tempfile.TemporaryDirectory() as directory:
        exchange = FakeExchange(Decimal("100"))
        client = TestClient(create_app(Path(directory) / "api.sqlite3", lambda: exchange))
        payload = {
            "symbol": "SOLUSDT", "lower_price": "80", "upper_price": "120",
            "grid_count": 4, "grid_budget_usdt": "400",
            "fixed_entry_mode": "price_triggered",
        }
        preview = client.post("/fixed-grids/preview", json=payload)
        assert preview.status_code == 200, preview.text
        assert preview.json()["allocation"]["seed_quantity"] == "0"
        assert preview.json()["allocation"]["initial_sell_order_count"] == 0
        assert preview.json()["allocation"]["initial_triggerable_buy_cell_count"] == 3
        created = client.post("/fixed-grids", json=payload)
        assert created.status_code == 201, created.text
        assert created.json()["fixed_entry_mode"] == "price_triggered"
        assert client.get("/strategies").json()[0]["fixed_entry_mode"] == "price_triggered"
        client.close()


def test_triggered_cells_stay_armed_with_only_ten_live_buy_orders() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteStore(Path(directory) / "window.sqlite3")
        exchange = FakeExchange(Decimal("100"))
        config = GridService(store).create_fixed(
            "SOLUSDT", Decimal("80"), Decimal("120"), 40,
            Decimal("4000"), Decimal("0.01"),
            fixed_entry_mode=FixedEntryMode.PRICE_TRIGGERED,
        )
        store.mark_started(config.strategy_id)
        engine = FixedGridEngine(store, exchange, config.strategy_id)
        engine.tick()
        armed = [cell for cell in store.list_cells(config.strategy_id)
                 if cell.stage == CellStage.PENDING_ENTRY]
        assert len(armed) > 10
        assert len(exchange.get_open_orders(config.symbol)) == 10
        assert exchange.get_positions() == []
        assert exchange.calls.get("place_market_order", 0) == 0

        exchange.mark = Decimal("95")
        engine.tick()
        # Once crossed, the trigger remains sticky even after the mark falls.
        assert all(store.get_cell(config.strategy_id, cell.cell_id).stage == CellStage.PENDING_ENTRY
                   for cell in armed)
        assert len(exchange.get_open_orders(config.symbol)) <= 10


def test_triggered_grid_recovers_from_error_and_pairs_buy_filled_during_outage() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteStore(Path(directory) / "recovery.sqlite3")
        exchange = FakeExchange(Decimal("100"))
        config = GridService(store).create_fixed(
            "SOXLUSDT", Decimal("80"), Decimal("120"), 40,
            Decimal("4000"), Decimal("0.01"),
            fixed_entry_mode=FixedEntryMode.PRICE_TRIGGERED,
        )
        store.mark_started(config.strategy_id)
        engine = FixedGridEngine(store, exchange, config.strategy_id)
        engine.tick()
        pending = next(
            cell for cell in store.list_cells(config.strategy_id)
            if cell.entry_order_id is not None
        )
        exchange.fill(pending.entry_order_id, pending.buy_price)
        # A transient exchange failure leaves the scheduler retrying an ERROR
        # strategy. The fill must still be paired on the next successful tick.
        store.set_status(config.strategy_id, StrategyStatus.ERROR)

        engine.tick()

        recovered = store.get_cell(config.strategy_id, pending.cell_id)
        assert recovered is not None and recovered.stage == CellStage.PENDING_EXIT
        assert recovered.exit_order_id is not None
        assert exchange.orders[recovered.exit_order_id].side == OrderSide.SELL
        assert store.get_strategy(config.strategy_id).status == StrategyStatus.RUNNING


def test_late_fee_history_reinvests_into_already_rearmed_buy() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteStore(Path(directory) / "late-fee.sqlite3")
        exchange = FakeExchange(Decimal("100"))
        config = GridService(store).create_fixed(
            "SOLUSDT", Decimal("80"), Decimal("120"), 1,
            Decimal("100"), Decimal("0.01"),
            fixed_entry_mode=FixedEntryMode.PRICE_TRIGGERED,
            auto_reinvest_enabled=True, auto_reinvest_percent=Decimal("50"),
        )
        store.mark_started(config.strategy_id)
        engine = FixedGridEngine(store, exchange, config.strategy_id)
        engine.tick()
        cell = store.list_cells(config.strategy_id)[0]
        first_buy = cell.entry_order_id
        exchange.fill(first_buy, cell.buy_price)
        engine.tick()
        cell = store.list_cells(config.strategy_id)[0]
        first_sell = cell.exit_order_id
        exchange.fill(first_sell, cell.sell_price)
        exchange.mark = cell.sell_price
        engine.tick()  # Exchange fee history has not arrived yet; next buy is rearmed.
        rearmed = store.list_cells(config.strategy_id)[0]
        assert rearmed.stage == CellStage.PENDING_ENTRY and rearmed.entry_order_id is not None
        original_qty = exchange.orders[rearmed.entry_order_id].original_qty
        assert store.list_cell_reinvestments(config.strategy_id) == []

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        store.save_strategy_trades(config.strategy_id, [
            {"id": 1, "orderId": first_buy, "side": "BUY", "qty": str(original_qty),
             "price": str(cell.buy_price), "realizedPnl": "0", "commission": "0.01",
             "commissionAsset": "USDT", "time": now_ms},
            {"id": 2, "orderId": first_sell, "side": "SELL", "qty": str(original_qty),
             "price": str(cell.sell_price), "realizedPnl": "0", "commission": "0.01",
             "commissionAsset": "USDT", "time": now_ms + 1},
        ])
        exchange.get_user_trades = lambda symbol, **kwargs: []
        engine.tick()

        after = store.list_cells(config.strategy_id)[0]
        assert len(store.list_cell_reinvestments(config.strategy_id)) == 1
        assert after.budget_usdt == Decimal("100") + cell.buy_price * exchange.filters.step_size
        assert after.entry_order_id is not None
        assert exchange.orders[after.entry_order_id].original_qty == original_qty + exchange.filters.step_size
        assert exchange.calls.get("place_market_order", 0) == 0


def test_triggered_grid_accepts_funded_downside_without_full_budget() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteStore(Path(directory) / "cash-preview.sqlite3")
        exchange = FakeExchange(Decimal("100"))
        exchange.available_balance = Decimal("350")
        config = GridService(store).create_fixed(
            "SOXLUSDT", Decimal("80"), Decimal("120"), 4,
            Decimal("400"), Decimal("0.01"),
            fixed_entry_mode=FixedEntryMode.PRICE_TRIGGERED,
        )
        store.mark_started(config.strategy_id)
        FixedGridEngine(store, exchange, config.strategy_id).tick()
        assert len(exchange.get_open_orders(config.symbol)) == 3
        assert exchange.get_positions() == []
        assert exchange.calls.get("place_market_order", 0) == 0

        with tempfile.TemporaryDirectory() as lower_directory:
            low_store = SQLiteStore(Path(lower_directory) / "underfunded.sqlite3")
            low_exchange = FakeExchange(Decimal("100"))
            low_exchange.available_balance = Decimal("303")
            low_config = GridService(low_store).create_fixed(
                "SOXLUSDT", Decimal("80"), Decimal("120"), 4,
                Decimal("400"), Decimal("0.01"),
                fixed_entry_mode=FixedEntryMode.PRICE_TRIGGERED,
            )
            low_store.mark_started(low_config.strategy_id)
            with pytest.raises(ValueError, match="triggered downside requirement"):
                FixedGridEngine(low_store, low_exchange, low_config.strategy_id).tick()
            assert low_exchange.get_open_orders(low_config.symbol) == []


def test_triggered_grid_waits_for_cash_without_stopping_or_overplacing() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteStore(Path(directory) / "cash-gate.sqlite3")
        exchange = FakeExchange(Decimal("100"))
        exchange.available_balance = Decimal("400")
        config = GridService(store).create_fixed(
            "SOXLUSDT", Decimal("80"), Decimal("120"), 4,
            Decimal("400"), Decimal("0.01"),
            fixed_entry_mode=FixedEntryMode.PRICE_TRIGGERED,
        )
        store.mark_started(config.strategy_id)
        engine = FixedGridEngine(store, exchange, config.strategy_id)
        engine.tick()
        for order in exchange.get_open_orders(config.symbol):
            exchange.cancel_order(config.symbol, order.order_id)
        exchange.available_balance = Decimal("250")
        engine.tick()
        assert len(exchange.get_open_orders(config.symbol)) == 2
        assert store.get_strategy(config.strategy_id).status == StrategyStatus.RUNNING
        assert any(event["event_type"] == "TRIGGERED_ENTRY_FUNDS_WAIT"
                   for event in store.list_events(config.strategy_id))

        exchange.available_balance = Decimal("50")
        engine.tick()
        assert len(exchange.get_open_orders(config.symbol)) == 2
        exchange.available_balance = Decimal("200")
        engine.tick()
        assert len(exchange.get_open_orders(config.symbol)) == 3
        assert any(event["event_type"] == "TRIGGERED_ENTRY_FUNDS_AVAILABLE"
                   for event in store.list_events(config.strategy_id))


def test_triggered_grid_margin_rejection_waits_for_next_cycle() -> None:
    class OnceInsufficient(FakeExchange):
        rejected = False

        def place_limit_order(self, *args, **kwargs):
            if not self.rejected:
                self.rejected = True
                raise BinanceAPIError("Binance -2019: Margin is insufficient")
            return super().place_limit_order(*args, **kwargs)

    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteStore(Path(directory) / "margin-reject.sqlite3")
        exchange = OnceInsufficient(Decimal("100"))
        exchange.available_balance = Decimal("400")
        config = GridService(store).create_fixed(
            "SOXLUSDT", Decimal("80"), Decimal("120"), 4,
            Decimal("400"), Decimal("0.01"),
            fixed_entry_mode=FixedEntryMode.PRICE_TRIGGERED,
        )
        store.mark_started(config.strategy_id)
        engine = FixedGridEngine(store, exchange, config.strategy_id)
        engine.tick()
        assert store.get_strategy(config.strategy_id).status == StrategyStatus.RUNNING
        assert exchange.get_open_orders(config.symbol) == []
        assert any(event["event_type"] == "TRIGGERED_ENTRY_FUNDS_WAIT"
                   for event in store.list_events(config.strategy_id))
        engine.tick()
        assert exchange.get_open_orders(config.symbol)
