from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from gridtrader.domain import CellStage, Mode, OrderSide, StrategyStatus
from gridtrader.scheduler import StrategyScheduler
from gridtrader.service import GridService
from gridtrader.store import SQLiteStore

from tests.fakes import FakeExchange


class ReconciliationLowLevelTests(unittest.TestCase):
    """End-to-end low-level tests for one scheduler reconciliation boundary."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tempdir.name) / "reconciliation-llt.sqlite3")
        self.exchange = FakeExchange(Decimal("105"))
        self.service = GridService(self.store, MagicMock())

    def tearDown(self):
        self.tempdir.cleanup()

    def prepare_groups(self, total: int, filled: int):
        strategy_ids: list[str] = []
        for _ in range(total):
            config = self.service.create(
                "BTCUSDT",
                Mode.LONG,
                Decimal("110"),
                Decimal("0.10"),
                1,
                Decimal("100"),
                3,
                Decimal("0.01"),
                poll_interval_sec=50.0,
                move_grid=False,
            )
            strategy_ids.append(config.strategy_id)
            self.store.mark_started(config.strategy_id)
        scheduler = StrategyScheduler(
            self.store,
            self.exchange,
            reconcile_interval_sec=5.0,
        )
        scheduler.run_once(now=0)
        for strategy_id in strategy_ids[:filled]:
            self.exchange.fill(self.store.list_cells(strategy_id)[0].entry_order_id)
        if filled:
            scheduler.run_once(now=50)
        return scheduler, strategy_ids

    def cells(self, strategy_ids: list[str]):
        return [self.store.list_cells(strategy_id)[0] for strategy_id in strategy_ids]

    def test_manual_cancel_all_mixed_entries_and_exits_converges_in_one_poll(self):
        scheduler, strategy_ids = self.prepare_groups(total=3, filled=2)
        before = list(self.exchange.get_open_orders("BTCUSDT"))
        self.assertEqual(len(before), 3)
        self.assertEqual(sum(order.side == OrderSide.BUY for order in before), 1)
        self.assertEqual(sum(order.side == OrderSide.SELL for order in before), 2)
        old_ids = {order.order_id for order in before}
        for order in before:
            self.exchange.cancel_order("BTCUSDT", order.order_id)

        scheduler.run_once(now=100)

        cells = self.cells(strategy_ids)
        self.assertEqual(sum(cell.stage == CellStage.PENDING_ENTRY for cell in cells), 1)
        self.assertEqual(sum(cell.stage == CellStage.PENDING_EXIT for cell in cells), 2)
        restored = self.exchange.get_open_orders("BTCUSDT")
        self.assertEqual(len(restored), 3)
        self.assertTrue(all(order.order_id not in old_ids for order in restored))
        self.assertEqual(sum(order.side == OrderSide.BUY for order in restored), 1)
        self.assertEqual(
            sum(
                (
                    order.original_qty - order.executed_qty
                    for order in restored
                    if order.side == OrderSide.SELL
                ),
                Decimal("0"),
            ),
            Decimal("2.000"),
        )

        placed_after_repair = len(self.exchange.placed)
        restored_ids = {order.order_id for order in restored}
        scheduler.run_once(now=105)
        self.assertEqual(len(self.exchange.placed), placed_after_repair)
        self.assertEqual(
            {order.order_id for order in self.exchange.get_open_orders("BTCUSDT")},
            restored_ids,
        )

    def test_manual_delete_all_exits_after_partial_position_close_resizes_exactly(self):
        scheduler, strategy_ids = self.prepare_groups(total=2, filled=2)
        for order in list(self.exchange.get_open_orders("BTCUSDT")):
            self.exchange.cancel_order("BTCUSDT", order.order_id)
        # Simulates a manual market close of 0.600 outside the grid service.
        self.exchange.set_position("BTCUSDT", "LONG", Decimal("1.400"))

        scheduler.run_once(now=100)
        scheduler.run_once(now=105)

        cells = self.cells(strategy_ids)
        self.assertEqual(
            sorted(cell.open_qty for cell in cells),
            [Decimal("0.400"), Decimal("1.000")],
        )
        exits = self.exchange.get_open_orders("BTCUSDT")
        self.assertEqual(
            sum((order.original_qty - order.executed_qty for order in exits), Decimal("0")),
            Decimal("1.400"),
        )
        self.assertTrue(all(cell.stage == CellStage.PENDING_EXIT for cell in cells))

    def test_unknown_missing_exit_is_restored_from_real_position(self):
        scheduler, strategy_ids = self.prepare_groups(total=1, filled=1)
        before = self.cells(strategy_ids)[0]
        old_exit_id = before.exit_order_id
        self.exchange.forget(old_exit_id)

        scheduler.run_once(now=100)

        restored = self.cells(strategy_ids)[0]
        self.assertEqual(restored.stage, CellStage.PENDING_EXIT)
        self.assertEqual(restored.open_qty, Decimal("1.000"))
        self.assertIsNotNone(restored.exit_order_id)
        self.assertNotEqual(restored.exit_order_id, old_exit_id)
        event_types = [
            event["event_type"] for event in self.store.list_events(strategy_ids[0])
        ]
        self.assertIn("EXIT_MISSING", event_types)
        self.assertIn("POSITION_EXIT_RESTORED", event_types)

    def test_filled_but_unqueryable_entry_is_not_duplicated_and_position_is_unassigned(self):
        scheduler, strategy_ids = self.prepare_groups(total=1, filled=0)
        pending = self.cells(strategy_ids)[0]
        old_entry_id = pending.entry_order_id
        self.exchange.fill(old_entry_id)
        self.exchange.forget(old_entry_id)
        placed_before = len(self.exchange.placed)

        scheduler.run_once(now=50)

        reviewed = self.cells(strategy_ids)[0]
        self.assertEqual(reviewed.stage, CellStage.MANUAL_REVIEW)
        self.assertEqual(reviewed.entry_order_id, old_entry_id)
        self.assertEqual(reviewed.open_qty, Decimal("0"))
        self.assertEqual(len(self.exchange.placed), placed_before)
        pool = self.store.list_position_pools()[0]
        self.assertEqual(Decimal(pool["actual_qty"]), Decimal("1.000"))
        self.assertEqual(Decimal(pool["logical_qty"]), Decimal("0"))
        self.assertEqual(Decimal(pool["unassigned_qty"]), Decimal("1.000"))
        self.assertEqual(pool["status"], "unassigned")

    def test_position_snapshot_failure_defers_repair_without_rewriting_then_recovers(self):
        scheduler, strategy_ids = self.prepare_groups(total=1, filled=1)
        target = self.cells(strategy_ids)[0]
        self.exchange.cancel_order("BTCUSDT", target.exit_order_id)
        original_get_positions = self.exchange.get_positions

        def fail_positions():
            raise RuntimeError("simulated position snapshot failure")

        self.exchange.get_positions = fail_positions  # type: ignore[method-assign]
        scheduler.run_once(now=100)

        deferred = self.cells(strategy_ids)[0]
        self.assertEqual(deferred.open_qty, Decimal("1.000"))
        self.assertEqual(deferred.stage, CellStage.MANUAL_REVIEW)
        self.assertIsNone(deferred.exit_order_id)
        self.assertIn("position snapshot failure", scheduler.last_reconcile_error)

        self.exchange.get_positions = original_get_positions  # type: ignore[method-assign]
        scheduler.run_once(now=105)
        recovered = self.cells(strategy_ids)[0]
        self.assertEqual(recovered.stage, CellStage.PENDING_EXIT)
        self.assertIsNotNone(recovered.exit_order_id)
        self.assertEqual(scheduler.last_reconcile_error, "")

    def test_fresh_entry_fill_is_not_released_by_same_cycle_stale_position(self):
        scheduler, strategy_ids = self.prepare_groups(total=1, filled=0)
        scheduler.position_settlement_grace_sec = 15.0
        strategy_id = strategy_ids[0]
        pending = self.cells(strategy_ids)[0]
        self.exchange.fill(pending.entry_order_id)
        # Binance Testnet can report the order as FILLED a few seconds before
        # positionRisk reflects it. Reproduce that cross-endpoint lag.
        self.exchange.set_position("BTCUSDT", "LONG", Decimal("0"))

        scheduler.run_once(now=50)

        protected = self.store.list_cells(strategy_id)[0]
        protected_exit_id = protected.exit_order_id
        self.assertEqual(protected.stage, CellStage.PENDING_EXIT)
        self.assertEqual(protected.open_qty, Decimal("1.000"))
        self.assertIsNotNone(protected_exit_id)
        self.assertEqual(
            self.exchange.orders[protected_exit_id].status.value,
            "NEW",
        )
        pending_pool = self.store.list_position_pools()[0]
        self.assertEqual(Decimal(pending_pool["actual_qty"]), Decimal("0"))
        self.assertEqual(Decimal(pending_pool["logical_qty"]), Decimal("1.000"))
        self.assertEqual(pending_pool["status"], "settling")

        # A second scheduler cycle can still see the old position snapshot.
        # The persisted fill timestamp keeps the exit protected across cycles.
        scheduler.run_once(now=55)
        still_protected = self.store.list_cells(strategy_id)[0]
        self.assertEqual(still_protected.open_qty, Decimal("1.000"))
        self.assertEqual(still_protected.exit_order_id, protected_exit_id)
        self.assertEqual(self.store.list_position_pools()[0]["status"], "settling")

        self.exchange.set_position("BTCUSDT", "LONG", Decimal("1.000"))
        scheduler.run_once(now=60)

        settled = self.store.list_cells(strategy_id)[0]
        self.assertEqual(settled.open_qty, Decimal("1.000"))
        self.assertEqual(settled.exit_order_id, protected_exit_id)
        self.assertEqual(self.store.list_position_pools()[0]["status"], "consistent")

    def test_exit_repair_failure_marks_pool_error_and_retries_next_poll(self):
        scheduler, strategy_ids = self.prepare_groups(total=1, filled=1)
        target = self.cells(strategy_ids)[0]
        self.exchange.cancel_order("BTCUSDT", target.exit_order_id)
        original_place = self.exchange.place_limit_order

        def fail_sell(symbol, side, position_side, quantity, price, client_order_id):
            if side == OrderSide.SELL and position_side == "LONG":
                raise RuntimeError("simulated exit placement failure")
            return original_place(symbol, side, position_side, quantity, price, client_order_id)

        self.exchange.place_limit_order = fail_sell  # type: ignore[method-assign]
        scheduler.run_once(now=100)

        failed = self.cells(strategy_ids)[0]
        self.assertEqual(failed.stage, CellStage.MANUAL_REVIEW)
        self.assertIsNone(failed.exit_order_id)
        self.assertEqual(self.store.list_position_pools()[0]["status"], "error")
        events = [
            event["event_type"] for event in self.store.list_events(strategy_ids[0])
        ]
        self.assertIn("POSITION_EXIT_REPAIR_FAILED", events)

        self.exchange.place_limit_order = original_place  # type: ignore[method-assign]
        scheduler.run_once(now=105)
        recovered = self.cells(strategy_ids)[0]
        self.assertEqual(recovered.stage, CellStage.PENDING_EXIT)
        self.assertIsNotNone(recovered.exit_order_id)
        self.assertEqual(self.store.list_position_pools()[0]["status"], "consistent")

    def test_stopped_group_deleted_exit_is_flagged_but_not_recreated(self):
        scheduler, strategy_ids = self.prepare_groups(total=2, filled=2)
        stopped_id, running_id = strategy_ids
        stopped = self.store.list_cells(stopped_id)[0]
        running = self.store.list_cells(running_id)[0]
        self.store.set_status(stopped_id, StrategyStatus.STOPPED)
        self.store.mark_runtime_stopped(stopped_id)
        self.exchange.cancel_order("BTCUSDT", stopped.exit_order_id)

        scheduler.run_once(now=100)

        stopped_after = self.store.list_cells(stopped_id)[0]
        running_after = self.store.list_cells(running_id)[0]
        self.assertEqual(stopped_after.exit_order_id, stopped.exit_order_id)
        self.assertEqual(
            self.exchange.orders[stopped.exit_order_id].status.value,
            "CANCELED",
        )
        self.assertEqual(running_after.exit_order_id, running.exit_order_id)
        self.assertEqual(self.store.list_position_pools()[0]["status"], "manual_review")

    def test_manual_cancel_mixed_short_entry_and_exit_restores_correct_sides(self):
        strategy_ids: list[str] = []
        for _ in range(2):
            config = self.service.create(
                "ETHUSDT",
                Mode.SHORT,
                Decimal("100"),
                Decimal("0.10"),
                1,
                Decimal("100"),
                3,
                Decimal("0.01"),
                poll_interval_sec=50.0,
                move_grid=False,
            )
            strategy_ids.append(config.strategy_id)
            self.store.mark_started(config.strategy_id)
        scheduler = StrategyScheduler(
            self.store,
            self.exchange,
            reconcile_interval_sec=5.0,
        )
        scheduler.run_once(now=0)
        self.exchange.fill(self.store.list_cells(strategy_ids[0])[0].entry_order_id)
        scheduler.run_once(now=50)
        for order in list(self.exchange.get_open_orders("ETHUSDT")):
            self.exchange.cancel_order("ETHUSDT", order.order_id)

        scheduler.run_once(now=100)

        cells = self.cells(strategy_ids)
        self.assertEqual(sum(cell.stage == CellStage.PENDING_EXIT for cell in cells), 1)
        self.assertEqual(sum(cell.stage == CellStage.PENDING_ENTRY for cell in cells), 1)
        orders = self.exchange.get_open_orders("ETHUSDT")
        self.assertEqual(len(orders), 2)
        self.assertTrue(
            any(order.side == OrderSide.BUY and order.position_side == "SHORT" for order in orders)
        )
        self.assertTrue(
            any(order.side == OrderSide.SELL and order.position_side == "SHORT" for order in orders)
        )


if __name__ == "__main__":
    unittest.main()
