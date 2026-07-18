from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from gridtrader.domain import CellStage, Mode, OrderStatus, StrategyConfig
from gridtrader.engine import TradingEngine
from gridtrader.store import SQLiteStore

from tests.fakes import FakeExchange


class OrderConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tempdir.name) / "consistency.sqlite3")
        self.exchange = FakeExchange(Decimal("105"))

    def tearDown(self):
        self.tempdir.cleanup()

    def add_group(self, strategy_id: str, anchor: str = "110") -> TradingEngine:
        config = StrategyConfig(
            strategy_id=strategy_id,
            symbol="BTCUSDT",
            mode=Mode.LONG,
            anchor_price=Decimal(anchor),
            grid_ratio=Decimal("0.10"),
            grid_count=1,
            order_usdt=Decimal("100"),
            move_grid=False,
        )
        self.store.create_strategy(config)
        return TradingEngine(self.store, self.exchange, strategy_id, run_id=f"run-{strategy_id}")

    def test_cancel_all_orders_restores_each_running_group_independently(self):
        first = self.add_group("btc-long-a")
        second = self.add_group("btc-long-b")
        first.tick()
        second.tick()
        original_open = self.exchange.get_open_orders("BTCUSDT")
        self.assertEqual(len(original_open), 2)
        self.assertEqual(len({order.client_order_id for order in original_open}), 2)

        for order in original_open:
            self.exchange.cancel_order("BTCUSDT", order.order_id)

        first.tick()
        second.tick()
        restored = self.exchange.get_open_orders("BTCUSDT")
        self.assertEqual(len(restored), 2)
        self.assertEqual(len({order.client_order_id for order in restored}), 2)
        self.assertTrue(all(order.order_id not in {item.order_id for item in original_open} for order in restored))

    def test_ambiguous_missing_single_entry_requires_manual_review(self):
        engine = self.add_group("btc-long-single")
        engine.tick()
        cell = self.store.list_cells("btc-long-single")[0]
        old_order_id = cell.entry_order_id
        self.exchange.forget(old_order_id)

        engine.tick()
        reviewed = self.store.list_cells("btc-long-single")[0]
        self.assertEqual(reviewed.stage, CellStage.MANUAL_REVIEW)
        self.assertIsNone(reviewed.entry_order_id)
        self.assertEqual(self.exchange.get_open_orders("BTCUSDT"), [])
        self.assertIn("ENTRY_MISSING", [event["event_type"] for event in self.store.list_events("btc-long-single")])

    def test_known_canceled_entry_is_recreated_after_price_crosses_below_it(self):
        engine = self.add_group("btc-long-crossed")
        engine.tick()
        cell = self.store.list_cells("btc-long-crossed")[0]
        old_order_id = cell.entry_order_id
        self.exchange.cancel_order("BTCUSDT", old_order_id)
        # The BUY entry at 100 was already armed. Even though price is now 95,
        # cancellation recovery must not apply the original >=100 trigger again.
        self.exchange.mark = Decimal("95")

        engine.tick()
        restored = self.store.list_cells("btc-long-crossed")[0]
        self.assertEqual(restored.stage, CellStage.PENDING_ENTRY)
        self.assertIsNotNone(restored.entry_order_id)
        self.assertNotEqual(restored.entry_order_id, old_order_id)
        restored_order_id = restored.entry_order_id
        placed_after_repair = len(self.exchange.placed)

        engine.tick()
        stable = self.store.list_cells("btc-long-crossed")[0]
        self.assertEqual(stable.entry_order_id, restored_order_id)
        self.assertEqual(len(self.exchange.placed), placed_after_repair)

    def test_active_partial_entry_cancels_remainder_and_protects_filled_quantity(self):
        engine = self.add_group("btc-active-partial-entry")
        engine.tick()
        entry = self.store.list_cells("btc-active-partial-entry")[0]
        entry_id = entry.entry_order_id
        self.exchange.partial_fill(entry_id, Decimal("0.400"))

        engine.tick()

        protected = self.store.list_cells("btc-active-partial-entry")[0]
        self.assertEqual(self.exchange.orders[entry_id].status, OrderStatus.CANCELED)
        self.assertEqual(protected.stage, CellStage.PENDING_EXIT)
        self.assertEqual(protected.open_qty, Decimal("0.400"))
        self.assertIsNotNone(protected.exit_order_id)
        exit_order = self.exchange.orders[protected.exit_order_id]
        self.assertEqual(exit_order.original_qty, Decimal("0.400"))
        self.assertEqual(exit_order.side.value, "SELL")
        event_types = [
            event["event_type"]
            for event in self.store.list_events("btc-active-partial-entry")
        ]
        self.assertIn("ENTRY_PARTIAL_FINALIZED", event_types)

    def test_missing_single_exit_order_requires_manual_review_and_is_not_recreated(self):
        engine = self.add_group("btc-long-exit")
        engine.tick()
        entry = self.store.list_cells("btc-long-exit")[0]
        self.exchange.fill(entry.entry_order_id)
        engine.tick()
        pending_exit = self.store.list_cells("btc-long-exit")[0]
        old_exit_id = pending_exit.exit_order_id
        open_qty = pending_exit.open_qty
        self.exchange.forget(old_exit_id)

        engine.tick()
        restored = self.store.list_cells("btc-long-exit")[0]
        self.assertEqual(restored.stage, CellStage.MANUAL_REVIEW)
        self.assertEqual(restored.open_qty, open_qty)
        self.assertIsNone(restored.exit_order_id)
        self.assertEqual(len(self.exchange.get_open_orders("BTCUSDT")), 0)
        self.assertIn("EXIT_MISSING", [event["event_type"] for event in self.store.list_events("btc-long-exit")])

    def test_canceled_exit_order_requires_manual_review_and_is_not_recreated(self):
        engine = self.add_group("btc-long-canceled-exit")
        engine.tick()
        entry = self.store.list_cells("btc-long-canceled-exit")[0]
        self.exchange.fill(entry.entry_order_id)
        engine.tick()
        pending_exit = self.store.list_cells("btc-long-canceled-exit")[0]
        self.exchange.cancel_order("BTCUSDT", pending_exit.exit_order_id)

        engine.tick()
        reviewed = self.store.list_cells("btc-long-canceled-exit")[0]
        self.assertEqual(reviewed.stage, CellStage.MANUAL_REVIEW)
        self.assertIsNone(reviewed.exit_order_id)
        self.assertGreater(reviewed.open_qty, 0)
        self.assertEqual(len(self.exchange.get_open_orders("BTCUSDT")), 0)

    def test_partially_filled_entry_canceled_places_exit_only_for_filled_quantity(self):
        engine = self.add_group("btc-partial-entry")
        engine.tick()
        entry = self.store.list_cells("btc-partial-entry")[0]
        filled_qty = Decimal("0.400")
        self.exchange.partial_fill(entry.entry_order_id, filled_qty)
        self.exchange.cancel_order("BTCUSDT", entry.entry_order_id)

        engine.tick()
        cell = self.store.list_cells("btc-partial-entry")[0]
        self.assertEqual(cell.stage, CellStage.PENDING_EXIT)
        self.assertEqual(cell.open_qty, filled_qty)
        self.assertIsNotNone(cell.exit_order_id)
        open_orders = self.exchange.get_open_orders("BTCUSDT")
        self.assertEqual(len(open_orders), 1)
        self.assertEqual(open_orders[0].order_id, cell.exit_order_id)
        self.assertEqual(open_orders[0].original_qty, filled_qty)

    def test_partially_filled_exit_canceled_tracks_remainder_for_manual_review(self):
        engine = self.add_group("btc-partial-exit")
        engine.tick()
        entry = self.store.list_cells("btc-partial-exit")[0]
        self.exchange.fill(entry.entry_order_id)
        engine.tick()
        pending_exit = self.store.list_cells("btc-partial-exit")[0]
        original_qty = pending_exit.open_qty
        filled_qty = Decimal("0.400")
        self.exchange.partial_fill(pending_exit.exit_order_id, filled_qty)
        self.exchange.cancel_order("BTCUSDT", pending_exit.exit_order_id)

        engine.tick()
        reviewed = self.store.list_cells("btc-partial-exit")[0]
        self.assertEqual(reviewed.stage, CellStage.MANUAL_REVIEW)
        self.assertEqual(reviewed.open_qty, original_qty - filled_qty)
        self.assertIsNone(reviewed.exit_order_id)
        self.assertEqual(len(self.exchange.get_open_orders("BTCUSDT")), 0)

    def test_open_partial_exit_fill_is_applied_once_before_final_cancel(self):
        engine = self.add_group("btc-live-partial-exit")
        engine.tick()
        entry = self.store.list_cells("btc-live-partial-exit")[0]
        self.exchange.fill(entry.entry_order_id)
        engine.tick()
        pending_exit = self.store.list_cells("btc-live-partial-exit")[0]
        exit_id = pending_exit.exit_order_id
        self.exchange.partial_fill(exit_id, Decimal("0.400"))

        engine.tick()
        partial = self.store.list_cells("btc-live-partial-exit")[0]
        self.assertEqual(partial.open_qty, Decimal("0.600"))
        self.assertEqual(partial.exit_executed_qty, Decimal("0.400"))

        self.exchange.cancel_order("BTCUSDT", exit_id)
        engine.tick()
        ended = self.store.list_cells("btc-live-partial-exit")[0]
        self.assertEqual(ended.open_qty, Decimal("0.600"))
        self.assertEqual(ended.stage, CellStage.MANUAL_REVIEW)

    def test_stopped_group_does_not_restore_but_other_running_group_does(self):
        stopped = self.add_group("btc-stopped")
        running = self.add_group("btc-running")
        stopped.tick()
        running.tick()
        stopped_order = self.store.list_cells("btc-stopped")[0].entry_order_id
        running_order = self.store.list_cells("btc-running")[0].entry_order_id
        self.exchange.cancel_order("BTCUSDT", stopped_order)
        self.exchange.cancel_order("BTCUSDT", running_order)

        # No tick for the stopped group: only the still-running group repairs its own order.
        running.tick()
        open_orders = self.exchange.get_open_orders("BTCUSDT")
        self.assertEqual(len(open_orders), 1)
        self.assertEqual(open_orders[0].order_id, self.store.list_cells("btc-running")[0].entry_order_id)
        stopped_snapshot = self.exchange.orders[stopped_order]
        self.assertEqual(stopped_snapshot.status, OrderStatus.CANCELED)

    def test_cancel_all_restores_entries_but_sends_existing_exits_to_manual_review(self):
        entry_group = self.add_group("btc-entry-group")
        exit_group = self.add_group("btc-exit-group")
        entry_group.tick()
        exit_group.tick()

        exit_cell = self.store.list_cells("btc-exit-group")[0]
        self.exchange.fill(exit_cell.entry_order_id)
        exit_group.tick()
        exit_cell = self.store.list_cells("btc-exit-group")[0]
        self.assertEqual(exit_cell.stage, CellStage.PENDING_EXIT)

        for order in list(self.exchange.get_open_orders("BTCUSDT")):
            self.exchange.cancel_order("BTCUSDT", order.order_id)

        entry_group.tick()
        exit_group.tick()
        entry_cell = self.store.list_cells("btc-entry-group")[0]
        reviewed_exit = self.store.list_cells("btc-exit-group")[0]
        self.assertEqual(entry_cell.stage, CellStage.PENDING_ENTRY)
        self.assertEqual(reviewed_exit.stage, CellStage.MANUAL_REVIEW)
        self.assertIsNone(reviewed_exit.exit_order_id)
        open_orders = self.exchange.get_open_orders("BTCUSDT")
        self.assertEqual(len(open_orders), 1)
        self.assertEqual(open_orders[0].order_id, entry_cell.entry_order_id)


if __name__ == "__main__":
    unittest.main()
