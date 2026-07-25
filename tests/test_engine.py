from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from grid_server.domain import CellStage, Mode, OrderSide, StrategyConfig
from grid_server.engine import TradingEngine
from grid_server.store import SQLiteStore

from tests.fakes import FakeExchange


class TradingEngineTests(unittest.TestCase):
    def make_engine(self, mode: Mode, anchor: str, mark: str, count: int = 1, move_grid: bool = False):
        tempdir = tempfile.TemporaryDirectory()
        store = SQLiteStore(Path(tempdir.name) / "engine.sqlite3")
        config = StrategyConfig(
            strategy_id=f"test-{mode.value}",
            symbol="BTCUSDT",
            mode=mode,
            anchor_price=Decimal(anchor),
            grid_ratio=Decimal("0.10"),
            grid_count=count,
            order_usdt=Decimal("100"),
            move_grid=move_grid,
        )
        store.create_strategy(config)
        exchange = FakeExchange(Decimal(mark))
        engine = TradingEngine(store, exchange, config.strategy_id, run_id="test-run")
        return tempdir, store, exchange, engine

    def test_long_entry_fill_places_sell_exit_and_closes_cycle(self):
        tempdir, store, exchange, engine = self.make_engine(Mode.LONG, "110", "105")
        self.addCleanup(tempdir.cleanup)

        engine.tick()
        cell = store.list_cells("test-long")[0]
        self.assertEqual(cell.stage, CellStage.PENDING_ENTRY)
        first_entry_client_id = cell.entry_client_id
        self.assertEqual(exchange.placed[-1]["side"], OrderSide.BUY)
        self.assertEqual(exchange.placed[-1]["price"], Decimal("100"))

        exchange.fill(cell.entry_order_id)
        engine.tick()
        cell = store.list_cells("test-long")[0]
        self.assertEqual(cell.stage, CellStage.PENDING_EXIT)
        self.assertEqual(exchange.placed[-1]["side"], OrderSide.SELL)
        self.assertEqual(exchange.placed[-1]["price"], Decimal("110"))

        exchange.fill(cell.exit_order_id)
        engine.tick()
        cell = store.list_cells("test-long")[0]
        self.assertEqual(cell.cycle_count, 1)
        self.assertEqual(cell.stage, CellStage.PENDING_ENTRY)
        self.assertNotEqual(cell.entry_client_id, first_entry_client_id)
        self.assertEqual([event["event_type"] for event in store.list_events("test-long")][-3:], [
            "EXIT_PLACED", "CYCLE_CLOSED", "ENTRY_PLACED"
        ])

    def test_short_entry_fill_places_buy_exit(self):
        tempdir, store, exchange, engine = self.make_engine(Mode.SHORT, "100", "105")
        self.addCleanup(tempdir.cleanup)

        engine.tick()
        cell = store.list_cells("test-short")[0]
        self.assertEqual(exchange.placed[-1]["side"], OrderSide.SELL)
        self.assertEqual(exchange.placed[-1]["price"], Decimal("110"))

        exchange.fill(cell.entry_order_id)
        engine.tick()
        cell = store.list_cells("test-short")[0]
        self.assertEqual(cell.stage, CellStage.PENDING_EXIT)
        self.assertEqual(exchange.placed[-1]["side"], OrderSide.BUY)
        self.assertEqual(exchange.placed[-1]["price"], Decimal("100"))

    def test_long_moving_window_adds_above_and_reclaims_lowest_safe_cell(self):
        tempdir, store, exchange, engine = self.make_engine(Mode.LONG, "110", "121", count=2, move_grid=True)
        self.addCleanup(tempdir.cleanup)

        engine.initialize()
        engine._move_window(Decimal("121"))
        cells = store.list_cells("test-long")
        self.assertEqual(len(cells), 2)
        # At an exact boundary the window advances through that boundary, matching legacy behavior.
        self.assertEqual(cells[0].buy_price, Decimal("110"))
        self.assertEqual(cells[-1].sell_price, Decimal("133.10"))
        self.assertEqual([cell.index for cell in cells], [1, 2])

    def test_long_moving_window_cancels_and_reclaims_farthest_pending_entry(self):
        tempdir, store, exchange, engine = self.make_engine(Mode.LONG, "110", "105", count=2, move_grid=True)
        self.addCleanup(tempdir.cleanup)
        engine.tick()
        cells = store.list_cells("test-long")
        self.assertEqual(cells[0].stage, CellStage.PENDING_ENTRY)
        reclaimed_order_id = cells[0].entry_order_id

        engine._move_window(Decimal("121"))
        moved = store.list_cells("test-long")
        self.assertEqual(len(moved), 2)
        self.assertNotEqual(moved[0].cell_id, cells[0].cell_id)
        self.assertEqual(exchange.orders[reclaimed_order_id].status.value, "CANCELED")
        self.assertIn(
            "window_reclaim_pending_entry",
            [event["payload"]["reason"] for event in store.list_events("test-long") if event["event_type"] == "CELL_REMOVED"],
        )

    def test_moving_window_never_reclaims_position_cell(self):
        tempdir, store, exchange, engine = self.make_engine(Mode.LONG, "110", "105", count=2, move_grid=True)
        self.addCleanup(tempdir.cleanup)
        engine.tick()
        lowest = store.list_cells("test-long")[0]
        exchange.fill(lowest.entry_order_id)
        engine.sync_orders_only()

        engine._move_window(Decimal("121"))

        moved = store.list_cells("test-long")
        protected = next(cell for cell in moved if cell.cell_id == lowest.cell_id)
        self.assertGreater(len(moved), 2)
        self.assertEqual(protected.stage, CellStage.PENDING_EXIT)
        self.assertGreater(protected.open_qty, 0)
        self.assertIsNotNone(protected.exit_order_id)
        anomalies = [
            event for event in store.list_events("test-long")
            if event["event_type"] == "WINDOW_RECLAIM_ANOMALY"
        ]
        self.assertEqual(anomalies[-1]["payload"]["reason"], "unexpected_owned_farthest_cell")

    def test_moving_window_detects_entry_filled_since_previous_poll(self):
        tempdir, store, exchange, engine = self.make_engine(Mode.LONG, "110", "105", count=2, move_grid=True)
        self.addCleanup(tempdir.cleanup)
        engine.tick()
        lowest = store.list_cells("test-long")[0]
        exchange.fill(lowest.entry_order_id)

        engine._move_window(Decimal("121"))

        protected = next(
            cell for cell in store.list_cells("test-long")
            if cell.cell_id == lowest.cell_id
        )
        self.assertGreater(len(store.list_cells("test-long")), 2)
        self.assertEqual(protected.stage, CellStage.PENDING_EXIT)
        self.assertGreater(protected.open_qty, 0)
        self.assertIsNotNone(protected.exit_order_id)
        anomalies = [
            event for event in store.list_events("test-long")
            if event["event_type"] == "WINDOW_RECLAIM_ANOMALY"
        ]
        self.assertEqual(anomalies[-1]["payload"]["reason"], "entry_changed_before_reclaim")

    def test_moving_window_treats_pending_entry_without_order_as_uncertain(self):
        tempdir, store, exchange, engine = self.make_engine(Mode.LONG, "110", "105", count=2, move_grid=True)
        self.addCleanup(tempdir.cleanup)
        engine.initialize()
        lowest = store.list_cells("test-long")[0]
        lowest.stage = CellStage.PENDING_ENTRY
        lowest.entry_order_id = None
        lowest.entry_client_id = ""
        store.save_cell(lowest)

        engine._move_window(Decimal("121"))

        self.assertGreater(len(store.list_cells("test-long")), 2)
        self.assertIn(lowest.cell_id, {cell.cell_id for cell in store.list_cells("test-long")})
        anomalies = [
            event for event in store.list_events("test-long")
            if event["event_type"] == "WINDOW_RECLAIM_ANOMALY"
        ]
        self.assertEqual(anomalies[-1]["payload"]["reason"], "pending_entry_without_order")

    def test_restart_recovers_entry_when_exchange_accepted_but_response_was_lost(self):
        tempdir, store, exchange, engine = self.make_engine(Mode.LONG, "110", "105")
        self.addCleanup(tempdir.cleanup)
        original_place = exchange.place_limit_order

        def accept_then_timeout(*args, **kwargs):
            original_place(*args, **kwargs)
            raise TimeoutError("response lost after exchange accepted order")

        exchange.place_limit_order = accept_then_timeout
        engine.tick()
        self.assertEqual(len(exchange.get_open_orders("BTCUSDT")), 1)
        uncertain = store.list_cells("test-long")[0]
        self.assertEqual(uncertain.stage, CellStage.PENDING_ENTRY)
        self.assertIsNone(uncertain.entry_order_id)
        self.assertTrue(uncertain.entry_client_id)
        self.assertIn(
            "ENTRY_SUBMISSION_UNKNOWN",
            [event["event_type"] for event in store.list_events("test-long")],
        )

        exchange.place_limit_order = original_place
        restarted = TradingEngine(store, exchange, "test-long", run_id="restarted")
        restarted.initialize()

        recovered = store.list_cells("test-long")[0]
        platform_order = exchange.get_open_orders("BTCUSDT")[0]
        self.assertEqual(recovered.stage, CellStage.PENDING_ENTRY)
        self.assertEqual(recovered.entry_order_id, platform_order.order_id)
        self.assertEqual(len(exchange.get_open_orders("BTCUSDT")), 1)

    def test_restart_recovers_entry_when_database_save_failed_after_acceptance(self):
        tempdir, store, exchange, engine = self.make_engine(Mode.LONG, "110", "105")
        self.addCleanup(tempdir.cleanup)
        original_save = store.save_cell
        failed = False

        def fail_first_order_save(cell):
            nonlocal failed
            if not failed and cell.stage == CellStage.PENDING_ENTRY and cell.entry_order_id is not None:
                failed = True
                raise OSError("database write failed after exchange accepted order")
            return original_save(cell)

        store.save_cell = fail_first_order_save
        with self.assertRaises(OSError):
            engine.tick()
        self.assertEqual(len(exchange.get_open_orders("BTCUSDT")), 1)
        uncertain = store.list_cells("test-long")[0]
        self.assertEqual(uncertain.stage, CellStage.PENDING_ENTRY)
        self.assertIsNone(uncertain.entry_order_id)
        self.assertTrue(uncertain.entry_client_id)

        store.save_cell = original_save
        restarted = TradingEngine(store, exchange, "test-long", run_id="restarted")
        restarted.initialize()

        recovered = store.list_cells("test-long")[0]
        platform_order = exchange.get_open_orders("BTCUSDT")[0]
        self.assertEqual(recovered.stage, CellStage.PENDING_ENTRY)
        self.assertEqual(recovered.entry_order_id, platform_order.order_id)
        self.assertEqual(len(exchange.get_open_orders("BTCUSDT")), 1)

    def test_moving_window_keeps_cell_when_cancel_races_partial_fill(self):
        tempdir, store, exchange, engine = self.make_engine(Mode.LONG, "110", "105", count=2, move_grid=True)
        self.addCleanup(tempdir.cleanup)
        engine.tick()
        lowest = store.list_cells("test-long")[0]
        original_cancel = exchange.cancel_order

        def partial_fill_then_cancel(symbol, order_id):
            order = exchange.get_order(symbol, order_id)
            exchange.partial_fill(order_id, order.original_qty / Decimal("2"))
            return original_cancel(symbol, order_id)

        exchange.cancel_order = partial_fill_then_cancel
        engine._move_window(Decimal("121"))

        moved = store.list_cells("test-long")
        protected = next(cell for cell in moved if cell.cell_id == lowest.cell_id)
        self.assertGreater(len(moved), 2)
        self.assertEqual(protected.stage, CellStage.PENDING_EXIT)
        self.assertGreater(protected.open_qty, 0)
        self.assertIsNotNone(protected.exit_order_id)
        anomalies = [
            event for event in store.list_events("test-long")
            if event["event_type"] == "WINDOW_RECLAIM_ANOMALY"
        ]
        self.assertEqual(anomalies[-1]["payload"]["reason"], "cancel_raced_entry_fill")

    def test_moving_window_keeps_cell_when_cancel_fails(self):
        tempdir, store, exchange, engine = self.make_engine(Mode.LONG, "110", "105", count=2, move_grid=True)
        self.addCleanup(tempdir.cleanup)
        engine.tick()
        lowest = store.list_cells("test-long")[0]

        def fail_cancel(symbol, order_id):
            raise RuntimeError("cancel unavailable")

        exchange.cancel_order = fail_cancel
        engine._move_window(Decimal("121"))

        moved = store.list_cells("test-long")
        self.assertGreater(len(moved), 2)
        self.assertEqual(moved[0].cell_id, lowest.cell_id)
        self.assertIn(
            "CELL_RECLAIM_CANCEL_FAILED",
            [event["event_type"] for event in store.list_events("test-long")],
        )

    def test_short_moving_window_cancels_highest_pending_entry(self):
        tempdir, store, exchange, engine = self.make_engine(Mode.SHORT, "100", "105", count=2, move_grid=True)
        self.addCleanup(tempdir.cleanup)
        engine.tick()
        highest = store.list_cells("test-short")[-1]
        self.assertEqual(highest.stage, CellStage.PENDING_ENTRY)
        reclaimed_order_id = highest.entry_order_id

        engine._move_window(Decimal("90"))

        moved = store.list_cells("test-short")
        self.assertEqual(len(moved), 2)
        self.assertNotIn(highest.cell_id, {cell.cell_id for cell in moved})
        self.assertEqual(exchange.orders[reclaimed_order_id].status.value, "CANCELED")

    def test_manual_add_upper_updates_cells_and_configured_grid_count(self):
        tempdir, store, exchange, engine = self.make_engine(Mode.LONG, "110", "105", count=2, move_grid=True)
        self.addCleanup(tempdir.cleanup)
        store.mark_started("test-long")
        engine.tick()
        old_highest = store.list_cells("test-long")[-1]
        action = store.request_cell_action("test-long", "add", "upper")

        engine.process_cell_actions()

        cells = store.list_cells("test-long")
        self.assertEqual(len(cells), 3)
        self.assertEqual(store.get_strategy("test-long").grid_count, 3)
        self.assertEqual(cells[-1].buy_price, old_highest.sell_price)
        self.assertEqual(store.list_cell_actions("test-long")[0]["id"], action["id"])
        self.assertEqual(store.list_cell_actions("test-long")[0]["status"], "completed")

    def test_manual_remove_pending_entry_cancels_order_before_deleting_cell(self):
        tempdir, store, exchange, engine = self.make_engine(Mode.LONG, "110", "105", count=2, move_grid=True)
        self.addCleanup(tempdir.cleanup)
        store.mark_started("test-long")
        engine.tick()
        lowest = store.list_cells("test-long")[0]
        order_id = lowest.entry_order_id
        store.request_cell_action("test-long", "remove", "lower")

        engine.process_cell_actions()

        self.assertEqual(exchange.orders[order_id].status.value, "CANCELED")
        self.assertNotIn(lowest.cell_id, {cell.cell_id for cell in store.list_cells("test-long")})
        self.assertEqual(store.get_strategy("test-long").grid_count, 1)
        self.assertEqual(store.list_cell_actions("test-long")[0]["status"], "completed")

    def test_manual_remove_rejects_boundary_cell_with_position(self):
        tempdir, store, exchange, engine = self.make_engine(Mode.SHORT, "100", "105", count=2, move_grid=True)
        self.addCleanup(tempdir.cleanup)
        store.mark_started("test-short")
        engine.tick()
        highest = store.list_cells("test-short")[-1]
        exchange.fill(highest.entry_order_id)
        engine.sync_orders_only()

        with self.assertRaisesRegex(ValueError, "position or uncertain"):
            store.request_cell_action("test-short", "remove", "upper")

        self.assertEqual(len(store.list_cells("test-short")), 2)
        self.assertGreater(store.list_cells("test-short")[-1].open_qty, 0)

    def test_manual_remove_stops_when_cancel_races_partial_fill(self):
        tempdir, store, exchange, engine = self.make_engine(Mode.LONG, "110", "105", count=2, move_grid=True)
        self.addCleanup(tempdir.cleanup)
        store.mark_started("test-long")
        engine.tick()
        lowest = store.list_cells("test-long")[0]
        original_cancel = exchange.cancel_order

        def partial_fill_then_cancel(symbol, order_id):
            order = exchange.get_order(symbol, order_id)
            exchange.partial_fill(order_id, order.original_qty / Decimal("2"))
            return original_cancel(symbol, order_id)

        exchange.cancel_order = partial_fill_then_cancel
        store.request_cell_action("test-long", "remove", "lower")
        engine.process_cell_actions()

        protected = next(cell for cell in store.list_cells("test-long") if cell.cell_id == lowest.cell_id)
        self.assertEqual(protected.stage, CellStage.PENDING_EXIT)
        self.assertGreater(protected.open_qty, 0)
        self.assertIsNotNone(protected.exit_order_id)
        self.assertEqual(store.get_strategy("test-long").grid_count, 2)
        action = store.list_cell_actions("test-long")[0]
        self.assertEqual(action["status"], "failed")
        self.assertIn("not safely reclaimable", action["message"])

    def test_restart_recovers_open_exit_from_exchange(self):
        tempdir, store, exchange, engine = self.make_engine(Mode.LONG, "110", "105")
        self.addCleanup(tempdir.cleanup)
        engine.tick()
        cell = store.list_cells("test-long")[0]
        exchange.fill(cell.entry_order_id)
        engine.tick()
        exit_id = store.list_cells("test-long")[0].exit_order_id

        restarted = TradingEngine(store, exchange, "test-long", run_id="restarted")
        restarted.initialize()
        recovered = store.list_cells("test-long")[0]
        self.assertEqual(recovered.stage, CellStage.PENDING_EXIT)
        self.assertEqual(recovered.exit_order_id, exit_id)
        self.assertIn("OPEN_ORDER_RECOVERED", [event["event_type"] for event in store.list_events("test-long")])

    def test_missing_exit_is_repaired_from_persisted_open_quantity(self):
        tempdir, store, exchange, engine = self.make_engine(Mode.SHORT, "100", "105")
        self.addCleanup(tempdir.cleanup)
        engine.initialize()
        cell = store.list_cells("test-short")[0]
        cell.stage = CellStage.PENDING_EXIT
        cell.open_qty = Decimal("1")
        cell.exit_order_id = None
        store.save_cell(cell)

        engine.tick()
        repaired = store.list_cells("test-short")[0]
        self.assertIsNotNone(repaired.exit_order_id)
        self.assertEqual(exchange.placed[-1]["side"], OrderSide.BUY)


if __name__ == "__main__":
    unittest.main()
