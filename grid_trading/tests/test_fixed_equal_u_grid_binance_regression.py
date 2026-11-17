from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from grid_server.application.fixed_engine import FixedGridEngine
from grid_server.application.service import GridService
from grid_server.domain import (
    CellStage, FuturesMarket, GridKind, Mode, OrderSide, OrderSnapshot,
    OrderStatus, StrategyConfig,
)
from grid_server.domain.grid import build_cells, fixed_cell_budgets, fixed_grid_ratio, round_down
from grid_server.infrastructure.binance import BinanceFuturesExchange
from grid_server.interfaces.api import create_app
from grid_server.ports.exchange import ExchangeExecutionUnknownError
from grid_server.shared.config import fixed_grid_endpoint_allowed
from grid_server.runtime.scheduler import StrategyScheduler
from grid_server.store import SQLiteStore

from tests.fakes import FakeExchange



class TestBinanceGridRegression(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.store = SQLiteStore(Path(self.tempdir.name) / "fixed.sqlite3")
        self.exchange = FakeExchange(Decimal("100"))

    def make_engine(self) -> FixedGridEngine:
        config = StrategyConfig(
            strategy_id="fixed-sol",
            symbol="SOLUSDT",
            mode=Mode.LONG,
            anchor_price=Decimal("120"),
            grid_ratio=fixed_grid_ratio(Decimal("80"), Decimal("120"), 4),
            grid_count=4,
            order_usdt=Decimal("100"),
            market_type=FuturesMarket.USDM,
            leverage=1,
            move_grid=False,
            grid_kind=GridKind.FIXED_EQUAL_USDT,
            lower_price=Decimal("80"),
            upper_price=Decimal("120"),
            grid_budget_usdt=Decimal("400"),
        )
        self.store.create_strategy(config)
        self.store.replace_cells(config.strategy_id, build_cells(config, Decimal("0.01")))
        self.store.mark_started(config.strategy_id)
        return FixedGridEngine(self.store, self.exchange, config.strategy_id)

    def test_reinvest_percent_is_editable_without_changing_the_grid(self) -> None:
        engine = self.make_engine()
        client = TestClient(create_app(self.store.path, lambda market=FuturesMarket.USDM: self.exchange))
        endpoint = f"/strategies/{engine.strategy_id}/auto-reinvest"
        changed = client.post(endpoint, json={"enabled": True, "auto_reinvest_percent": "35"})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()["auto_reinvest_percent"], "35")
        self.assertEqual(client.get("/strategies").json()[0]["auto_reinvest_percent"], "35")
        paused = client.post(endpoint, json={"enabled": False})
        self.assertEqual(paused.status_code, 200, paused.text)
        self.assertEqual(paused.json()["auto_reinvest_percent"], "35")
        self.assertEqual(client.post(endpoint, json={"enabled": True,
                                                     "auto_reinvest_percent": "101"}).status_code, 422)
        self.assertEqual(client.post(endpoint, json={"enabled": True,
                                                     "auto_reinvest_percent": "NaN"}).status_code, 422)

    def test_new_reinvest_ledger_excludes_old_cycles_and_spending(self) -> None:
        engine = self.make_engine()
        cell = self.store.list_cells(engine.strategy_id)[0]
        step = self.exchange.filters.step_size
        unit = cell.buy_price * step
        cell.cycle_count = 1
        self.store.save_cell(cell)
        with self.store.connection() as conn:
            conn.execute(
                """INSERT INTO cell_reinvestments(
                    strategy_id, cell_id, cycle_count, quantity_step,
                    amount_usdt, profit_snapshot_usdt, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (engine.strategy_id, cell.cell_id, 1, str(step),
                 str(unit), str(unit * 3), "2026-01-01T00:00:00+00:00"),
            )
        self.assertTrue(self.store.ensure_reinvest_cutover(engine.strategy_id))
        self.assertFalse(self.store.ensure_reinvest_cutover(engine.strategy_id))
        self.assertFalse(self.store.record_cell_reinvest_earning(
            engine.strategy_id, cell.cell_id, 1, unit * 3, Decimal("100")
        ))
        self.store.set_auto_reinvest_settings(
            engine.strategy_id, enabled=True, auto_reinvest_percent=Decimal("50")
        )
        cell.cycle_count = 2
        self.store.save_cell(cell)
        self.assertTrue(self.store.record_cell_reinvest_earning(
            engine.strategy_id, cell.cell_id, 2, unit * Decimal("1.5"), Decimal("50")
        ))
        self.assertFalse(self.store.apply_cell_reinvestment(
            engine.strategy_id, cell.cell_id, 2, step, unit * 4
        ))
        self.store.set_auto_reinvest_settings(
            engine.strategy_id, enabled=True, auto_reinvest_percent=Decimal("100")
        )
        cell.cycle_count = 3
        self.store.save_cell(cell)
        self.assertTrue(self.store.record_cell_reinvest_earning(
            engine.strategy_id, cell.cell_id, 3, unit * Decimal("0.3"), Decimal("100")
        ))
        self.assertTrue(self.store.apply_cell_reinvestment(
            engine.strategy_id, cell.cell_id, 3, step, unit * 5
        ))
        entries = self.store.list_cell_reinvestments(engine.strategy_id)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[-1]["cycle_count"], 3)
        earnings = self.store.list_cell_reinvest_earnings(engine.strategy_id)
        self.assertEqual([Decimal(row["eligible_profit_usdt"]) for row in earnings],
                         [unit * Decimal("0.75"), unit * Decimal("0.3")])
        cell = next(item for item in self.store.list_cells(engine.strategy_id)
                    if item.cell_id == cell.cell_id)
        cell.cycle_count = 4
        self.store.save_cell(cell)
        self.assertTrue(self.store.record_cell_reinvest_earning(
            engine.strategy_id, cell.cell_id, 4, -unit * Decimal("0.5"), Decimal("100")
        ))
        self.assertFalse(self.store.apply_cell_reinvestment(
            engine.strategy_id, cell.cell_id, 4, step, unit * 5
        ))
        cell.cycle_count = 5
        self.store.save_cell(cell)
        self.assertTrue(self.store.record_cell_reinvest_earning(
            engine.strategy_id, cell.cell_id, 5, unit * 10, Decimal("0")
        ))
        self.assertFalse(self.store.apply_cell_reinvestment(
            engine.strategy_id, cell.cell_id, 5, step, unit * 15
        ))
        other = self.store.list_cells(engine.strategy_id)[1]
        other_unit = other.buy_price * step
        other.cycle_count = 1
        self.store.save_cell(other)
        self.assertTrue(self.store.record_cell_reinvest_earning(
            engine.strategy_id, other.cell_id, 1, other_unit * 2, Decimal("50")
        ))
        other.cycle_count = 2
        self.store.save_cell(other)
        self.assertTrue(self.store.record_cell_reinvest_earning(
            engine.strategy_id, other.cell_id, 2, -other_unit * 2, Decimal("0")
        ))
        self.assertFalse(self.store.apply_cell_reinvestment(
            engine.strategy_id, other.cell_id, 2, step, Decimal("100")
        ))

    def test_completed_cycle_keeps_its_percent_through_delayed_partial_fees(self) -> None:
        engine = self.make_engine()
        engine.tick()
        cell = self.store.list_cells(engine.strategy_id)[0]
        entry = self.exchange.orders[cell.entry_order_id]
        self.exchange.fill(entry.order_id, cell.buy_price)
        engine.tick()
        cell = next(item for item in self.store.list_cells(engine.strategy_id)
                    if item.cell_id == cell.cell_id)
        exit_order = self.exchange.orders[cell.exit_order_id]
        self.store.set_auto_reinvest_settings(
            engine.strategy_id, enabled=True, auto_reinvest_percent=Decimal("50")
        )
        self.exchange.fill(exit_order.order_id, cell.sell_price)
        self.exchange.get_user_trades = lambda symbol, **kwargs: []
        engine.tick()
        closed = [event for event in self.store.list_events(engine.strategy_id)
                  if event["cell_id"] == cell.cell_id
                  and event["event_type"] == "CYCLE_CLOSED"][-1]
        self.assertEqual(closed["payload"]["auto_reinvest_percent"], "50")
        self.store.set_auto_reinvest_settings(
            engine.strategy_id, enabled=True, auto_reinvest_percent=Decimal("100")
        )

        seed = self.store.get_capital_action(f"seed:{engine.strategy_id}")
        first_sell_qty = exit_order.original_qty / Decimal("2")
        last_sell_qty = exit_order.original_qty - first_sell_qty
        def fill(trade_id, order_id, side, qty, price, fee):
            return {
                "id": trade_id, "orderId": order_id, "side": side,
                "qty": str(qty), "price": str(price), "realizedPnl": "0",
                "commission": str(fee), "commissionAsset": "USDT",
                "time": 1794480000000 + trade_id,
            }
        self.store.save_strategy_trades(engine.strategy_id, [
            fill(1, seed["order_id"], "BUY", seed["executed_qty"], self.exchange.mark, "0"),
            fill(2, entry.order_id, "BUY", entry.original_qty, cell.buy_price, "0.01"),
            fill(3, exit_order.order_id, "SELL", first_sell_qty, cell.sell_price, "0.02"),
        ])
        engine.tick()
        self.assertEqual(self.store.list_cell_reinvest_earnings(engine.strategy_id), [])
        self.store.save_strategy_trades(engine.strategy_id, [
            fill(4, exit_order.order_id, "SELL", last_sell_qty, cell.sell_price, "0.03"),
        ])
        engine.tick()
        earnings = self.store.list_cell_reinvest_earnings(engine.strategy_id)
        self.assertEqual(len(earnings), 1)
        expected_net = (exit_order.original_qty * (cell.sell_price - cell.buy_price)
                        - Decimal("0.01") - Decimal("0.02") - Decimal("0.03"))
        self.assertEqual(Decimal(earnings[0]["net_profit_usdt"]), expected_net)
        self.assertEqual(Decimal(earnings[0]["eligible_profit_usdt"]), expected_net / 2)
        self.assertEqual(earnings[0]["auto_reinvest_percent"], "50")

    def test_reinvest_waits_for_fee_on_prior_partly_filled_exit(self) -> None:
        engine = self.make_engine()
        engine.tick()
        cell = self.store.list_cells(engine.strategy_id)[0]
        entry = self.exchange.orders[cell.entry_order_id]
        self.exchange.fill(entry.order_id, cell.buy_price)
        engine.tick()
        cell = next(item for item in self.store.list_cells(engine.strategy_id)
                    if item.cell_id == cell.cell_id)
        first_exit = self.exchange.orders[cell.exit_order_id]
        partial_qty = first_exit.original_qty / Decimal("2")
        self.exchange.partial_fill(first_exit.order_id, partial_qty, cell.sell_price)
        engine._sync_cell(cell)
        self.exchange.cancel_order(engine._config().symbol, first_exit.order_id)
        cell = next(item for item in self.store.list_cells(engine.strategy_id)
                    if item.cell_id == cell.cell_id)
        engine._sync_cell(cell)
        cell = next(item for item in self.store.list_cells(engine.strategy_id)
                    if item.cell_id == cell.cell_id)
        self.assertEqual(cell.stage, CellStage.MANUAL_REVIEW)
        cell.stage = CellStage.PENDING_EXIT
        self.store.save_cell(cell)
        engine._ensure_exit(cell)
        second_exit = self.exchange.orders[cell.exit_order_id]
        self.exchange.fill(second_exit.order_id, cell.sell_price)
        engine._sync_cell(cell)
        self.store.set_auto_reinvest_enabled(engine.strategy_id, True)
        self.exchange.get_user_trades = lambda symbol, **kwargs: []
        seed = self.store.get_capital_action(f"seed:{engine.strategy_id}")
        def fill(trade_id, order_id, side, qty, price, fee):
            return {
                "id": trade_id, "orderId": order_id, "side": side,
                "qty": str(qty), "price": str(price), "realizedPnl": "0",
                "commission": str(fee), "commissionAsset": "USDT",
                "time": 1794480000000 + trade_id,
            }
        self.store.save_strategy_trades(engine.strategy_id, [
            fill(1, seed["order_id"], "BUY", seed["executed_qty"], self.exchange.mark, "0"),
            fill(2, entry.order_id, "BUY", entry.original_qty, cell.buy_price, "0.01"),
            fill(4, second_exit.order_id, "SELL", second_exit.original_qty, cell.sell_price, "0.02"),
        ])
        engine._reinvest_closed_cells()
        self.assertEqual(self.store.list_cell_reinvest_earnings(engine.strategy_id), [])
        self.store.save_strategy_trades(engine.strategy_id, [
            fill(3, first_exit.order_id, "SELL", partial_qty, cell.sell_price, "0.03"),
        ])
        engine._reinvest_closed_cells()
        earnings = self.store.list_cell_reinvest_earnings(engine.strategy_id)
        self.assertEqual(len(earnings), 1)
        expected = (first_exit.original_qty * (cell.sell_price - cell.buy_price)
                    - Decimal("0.06"))
        self.assertEqual(Decimal(earnings[0]["net_profit_usdt"]), expected)

    def _make_large_window_engine(self) -> tuple[StrategyConfig, FixedGridEngine]:
        self.exchange.mark = Decimal("60.23")
        self.exchange.filters = replace(
            self.exchange.filters,
            step_size=Decimal("0.01"), min_qty=Decimal("0.01"),
            min_notional=Decimal("5"), contract_type="TRADIFI_PERPETUAL",
        )
        config = GridService(self.store, SimpleNamespace()).create_fixed(
            "SOXLUSDT", Decimal("30"), Decimal("150"), 323,
            Decimal("5000"), Decimal("0.01"),
        )
        self.store.mark_started(config.strategy_id)
        return config, FixedGridEngine(self.store, self.exchange, config.strategy_id)

    def test_full_window_replaces_far_sell_with_better_price_after_mark_moves(self) -> None:
        config, engine = self._make_large_window_engine()
        engine.tick()
        before = self.store.list_cells(config.strategy_id)
        initial_exit_ids = {
            cell.index for cell in before if cell.exit_order_id is not None
        }
        self.assertEqual(len(initial_exit_ids), engine.ORDERS_PER_SIDE)

        # The existing ten orders remain open, but their price ranking changes.
        self.exchange.mark = Decimal("62")
        expected = {
            cell.index for cell in sorted(
                (cell for cell in before if cell.stage == CellStage.PENDING_EXIT),
                key=lambda cell: (abs(cell.sell_price - self.exchange.mark), cell.index),
            )[:engine.ORDERS_PER_SIDE]
        }
        self.assertNotEqual(expected, initial_exit_ids)
        engine.tick()

        after = self.store.list_cells(config.strategy_id)
        actual = {cell.index for cell in after if cell.exit_order_id is not None}
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), engine.ORDERS_PER_SIDE)

    def test_price_band_ineligible_cells_do_not_use_window_slots(self) -> None:
        config, engine = self._make_large_window_engine()
        candidates = [
            cell for cell in self.store.list_cells(config.strategy_id)
            if cell.sell_price > self.exchange.mark
        ]
        closest = sorted(
            candidates, key=lambda cell: (abs(cell.sell_price - self.exchange.mark), cell.index)
        )
        blocked_prices = {cell.sell_price for cell in closest[:2]}
        ready = engine._order_is_ready

        def in_price_band(side: OrderSide, price: Decimal) -> bool:
            return price not in blocked_prices and ready(side, price)

        with patch.object(engine, "_order_is_ready", side_effect=in_price_band):
            engine.tick()

        after = self.store.list_cells(config.strategy_id)
        actual = {cell.index for cell in after if cell.exit_order_id is not None}
        expected = {cell.index for cell in closest[2:2 + engine.ORDERS_PER_SIDE]}
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), engine.ORDERS_PER_SIDE)

    def test_window_replaces_orders_that_later_leave_price_band(self) -> None:
        config, engine = self._make_large_window_engine()
        engine.tick()
        before = self.store.list_cells(config.strategy_id)
        active = sorted(
            (cell for cell in before if cell.exit_order_id is not None),
            key=lambda cell: (abs(cell.sell_price - self.exchange.mark), cell.index),
        )
        blocked_prices = {cell.sell_price for cell in active[:2]}
        ready = engine._order_is_ready

        def in_price_band(side: OrderSide, price: Decimal) -> bool:
            return price not in blocked_prices and ready(side, price)

        with patch.object(engine, "_order_is_ready", side_effect=in_price_band):
            engine.tick()

        after = self.store.list_cells(config.strategy_id)
        expected = {
            cell.index for cell in sorted(
                (cell for cell in before if cell.stage == CellStage.PENDING_EXIT
                 and cell.sell_price not in blocked_prices),
                key=lambda cell: (abs(cell.sell_price - self.exchange.mark), cell.index),
            )[:engine.ORDERS_PER_SIDE]
        }
        actual = {cell.index for cell in after if cell.exit_order_id is not None}
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), engine.ORDERS_PER_SIDE)

    def test_filled_buy_keeps_paired_sell_when_price_ranking_rebalances(self) -> None:
        config, engine = self._make_large_window_engine()
        engine.tick()
        buy = next(
            cell for cell in self.store.list_cells(config.strategy_id)
            if cell.entry_order_id is not None
        )
        self.exchange.fill(buy.entry_order_id)
        self.exchange.mark = Decimal("62")

        engine.tick()

        paired = self.store.get_cell(config.strategy_id, buy.cell_id)
        self.assertEqual(paired.stage, CellStage.PENDING_EXIT)
        self.assertIsNotNone(paired.exit_order_id)

    def test_previously_deferred_held_exit_is_placed_after_gap_rebalance(self) -> None:
        self.exchange.mark = Decimal("60.23")
        self.exchange.filters = replace(
            self.exchange.filters,
            step_size=Decimal("0.01"), min_qty=Decimal("0.01"),
            min_notional=Decimal("5"), contract_type="TRADIFI_PERPETUAL",
            percent_price_up=Decimal("1.03"), percent_price_down=Decimal("0.97"),
        )
        service = GridService(self.store, SimpleNamespace())
        config = service.create_fixed(
            "SOXLUSDT", Decimal("30"), Decimal("150"), 323,
            Decimal("5000"), Decimal("0.01"),
        )
        self.store.mark_started(config.strategy_id)
        engine = FixedGridEngine(self.store, self.exchange, config.strategy_id)
        engine.tick()
        cell = next(
            item for item in self.store.list_cells(config.strategy_id)
            if item.exit_order_id is not None
        )
        old_id = cell.exit_order_id
        self.exchange.cancel_order(config.symbol, old_id)
        cell.exit_order_id = None
        cell.exit_client_id = ""
        self.store.save_cell(cell)
        self.exchange.mark = cell.sell_price + Decimal("0.10")
        before = sum(
            event["event_type"] == "ORDER_WINDOW_REBALANCED"
            for event in self.store.list_events(config.strategy_id)
        )

        engine.tick()

        updated = self.store.get_cell(config.strategy_id, cell.cell_id)
        self.assertIsNotNone(updated.exit_order_id)
        self.assertNotEqual(updated.exit_order_id, old_id)
        self.assertEqual(
            sum(event["event_type"] == "ORDER_WINDOW_REBALANCED"
                for event in self.store.list_events(config.strategy_id)),
            before + 1,
        )
