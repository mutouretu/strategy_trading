from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from gridtrader.domain import CellStage, Mode, OrderSide, StrategyStatus, SymbolFilters
from gridtrader.position_coordinator import PositionCoordinator
from gridtrader.scheduler import StrategyScheduler
from gridtrader.service import GridService
from gridtrader.snapshot_exchange import SnapshotExchange
from gridtrader.store import SQLiteStore

from tests.fakes import FakeExchange


class PositionPoolCorrectionMatrixTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tempdir.name) / "matrix.sqlite3")
        self.exchange = FakeExchange(Decimal("105"))
        service = GridService(self.store, MagicMock())
        self.strategy_ids: list[str] = []
        for _ in range(2):
            config = service.create(
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
            self.strategy_ids.append(config.strategy_id)
            self.store.mark_started(config.strategy_id)
        self.scheduler = StrategyScheduler(
            self.store,
            self.exchange,
            reconcile_interval_sec=5.0,
        )
        self.scheduler.run_once(now=0)
        for strategy_id in self.strategy_ids:
            self.exchange.fill(self.store.list_cells(strategy_id)[0].entry_order_id)
        self.scheduler.run_once(now=50)

    def tearDown(self):
        self.tempdir.cleanup()

    def cells(self):
        return [self.store.list_cells(strategy_id)[0] for strategy_id in self.strategy_ids]

    def pool(self):
        return next(
            item
            for item in self.store.list_position_pools()
            if item["symbol"] == "BTCUSDT" and item["position_side"] == "LONG"
        )

    def test_consistent_pool_is_idempotent(self):
        placed_before = len(self.exchange.placed)
        canceled_before = self.exchange.calls.get("cancel_order", 0)
        exit_ids_before = [cell.exit_order_id for cell in self.cells()]

        self.scheduler.run_once(now=100)
        self.scheduler.run_once(now=105)

        self.assertEqual(len(self.exchange.placed), placed_before)
        self.assertEqual(self.exchange.calls.get("cancel_order", 0), canceled_before)
        self.assertEqual([cell.exit_order_id for cell in self.cells()], exit_ids_before)
        self.assertEqual(self.pool()["status"], "consistent")
        self.assertEqual(Decimal(self.pool()["actual_qty"]), Decimal("2.000"))
        self.assertEqual(Decimal(self.pool()["logical_qty"]), Decimal("2.000"))

    def test_external_entry_order_does_not_reserve_close_resources(self):
        self.exchange.place_limit_order(
            "BTCUSDT",
            OrderSide.BUY,
            "LONG",
            Decimal("0.500"),
            Decimal("90"),
            "external-entry",
        )

        self.scheduler.run_once(now=100)

        self.assertEqual([cell.open_qty for cell in self.cells()], [Decimal("1.000")] * 2)
        self.assertEqual(self.pool()["external_reserved_qty"], "0")
        self.assertEqual(self.pool()["status"], "consistent")

    def test_external_close_larger_than_position_preserves_ownership_and_reports_order_excess(self):
        external_id = self.exchange.place_limit_order(
            "BTCUSDT",
            OrderSide.SELL,
            "LONG",
            Decimal("3.000"),
            Decimal("120"),
            "external-close",
        )

        self.scheduler.run_once(now=100)

        self.assertTrue(all(cell.open_qty == Decimal("1.000") for cell in self.cells()))
        self.assertTrue(all(cell.stage == CellStage.MANUAL_REVIEW for cell in self.cells()))
        self.assertEqual(self.exchange.orders[external_id].status.value, "NEW")
        self.assertEqual(Decimal(self.pool()["actual_qty"]), Decimal("2.000"))
        self.assertEqual(Decimal(self.pool()["logical_qty"]), Decimal("2.000"))
        self.assertEqual(Decimal(self.pool()["external_reserved_qty"]), Decimal("3.000"))
        self.assertEqual(Decimal(self.pool()["shortage_qty"]), Decimal("0"))
        self.assertEqual(self.pool()["status"], "order_excess")

    def test_stopped_group_shortage_is_reported_without_mutating_stopped_cell(self):
        stopped_id, running_id = self.strategy_ids
        stopped_before = self.store.list_cells(stopped_id)[0]
        self.store.mark_runtime_stopped(stopped_id)
        self.store.set_status(stopped_id, StrategyStatus.STOPPED)
        self.exchange.set_position("BTCUSDT", "LONG", Decimal("0.400"))

        self.scheduler.run_once(now=100)
        self.scheduler.run_once(now=105)

        stopped = self.store.list_cells(stopped_id)[0]
        running = self.store.list_cells(running_id)[0]
        self.assertEqual(stopped.open_qty, Decimal("1.000"))
        self.assertEqual(stopped.exit_order_id, stopped_before.exit_order_id)
        self.assertEqual(running.open_qty, Decimal("0"))
        self.assertEqual(running.stage, CellStage.UNTRIGGERED)
        self.assertEqual(Decimal(self.pool()["actual_qty"]), Decimal("0.400"))
        self.assertEqual(Decimal(self.pool()["logical_qty"]), Decimal("1.000"))
        self.assertEqual(Decimal(self.pool()["shortage_qty"]), Decimal("0.600"))
        self.assertEqual(self.pool()["status"], "shortage")

    def test_cancel_failure_does_not_rewrite_cells_and_marks_pool_error(self):
        cells_before = self.cells()
        self.exchange.set_position("BTCUSDT", "LONG", Decimal("1.400"))

        def fail_cancel(_symbol: str, _order_id: int) -> None:
            raise RuntimeError("simulated cancel failure")

        self.exchange.cancel_order = fail_cancel  # type: ignore[method-assign]
        self.scheduler.run_once(now=100)
        self.scheduler.run_once(now=105)

        cells_after = self.cells()
        self.assertEqual(
            [(cell.open_qty, cell.exit_order_id) for cell in cells_after],
            [(cell.open_qty, cell.exit_order_id) for cell in cells_before],
        )
        self.assertEqual(self.pool()["status"], "error")
        events = [
            event["event_type"]
            for strategy_id in self.strategy_ids
            for event in self.store.list_events(strategy_id)
        ]
        self.assertIn("POSITION_RECONCILE_CANCEL_FAILED", events)

    def test_below_minimum_allocation_is_not_ordered_and_requires_review(self):
        self.exchange.filters = SymbolFilters(
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.1"),
            min_qty=Decimal("0.5"),
            min_notional=Decimal("0"),
        )
        self.exchange.set_position("BTCUSDT", "LONG", Decimal("1.4"))
        # Symbol rules are cached for the scheduler lifetime. A restarted
        # scheduler reloads them, matching how an exchange rule change is
        # picked up operationally.
        self.scheduler = StrategyScheduler(
            self.store,
            self.exchange,
            reconcile_interval_sec=5.0,
        )

        self.scheduler.run_once(now=100)
        self.scheduler.run_once(now=105)

        cells = self.cells()
        reviewed = next(cell for cell in cells if cell.open_qty == Decimal("0.4"))
        protected = next(cell for cell in cells if cell.open_qty == Decimal("1.0"))
        self.assertEqual(reviewed.stage, CellStage.MANUAL_REVIEW)
        self.assertIsNone(reviewed.exit_order_id)
        self.assertEqual(protected.stage, CellStage.PENDING_EXIT)
        self.assertIsNotNone(protected.exit_order_id)
        self.assertEqual(self.pool()["status"], "manual_review")
        events = [
            event["event_type"] for event in self.store.list_events(reviewed.strategy_id)
        ]
        self.assertIn("POSITION_RESOURCE_DUST", events)


class PositionPoolIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tempdir.name) / "isolation.sqlite3")
        self.exchange = FakeExchange(Decimal("105"))

    def tearDown(self):
        self.tempdir.cleanup()

    def test_position_without_any_logical_cell_is_unassigned(self):
        self.exchange.set_position("BTCUSDT", "LONG", Decimal("0.750"))
        snapshot = SnapshotExchange(self.exchange)
        snapshot.begin_cycle()

        result = PositionCoordinator(self.store, snapshot, "matrix-run").reconcile({})

        self.assertEqual(result.repaired_exits, 0)
        self.assertEqual(result.released_cells, 0)
        pool = self.store.list_position_pools()[0]
        self.assertEqual(Decimal(pool["actual_qty"]), Decimal("0.750"))
        self.assertEqual(Decimal(pool["logical_qty"]), Decimal("0"))
        self.assertEqual(Decimal(pool["unassigned_qty"]), Decimal("0.750"))
        self.assertEqual(pool["status"], "unassigned")

    def test_long_and_short_on_same_symbol_use_independent_pools(self):
        service = GridService(self.store, MagicMock())
        configs = [
            service.create(
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
            ),
            service.create(
                "BTCUSDT",
                Mode.SHORT,
                Decimal("100"),
                Decimal("0.10"),
                1,
                Decimal("100"),
                3,
                Decimal("0.01"),
                poll_interval_sec=50.0,
                move_grid=False,
            ),
        ]
        for config in configs:
            self.store.mark_started(config.strategy_id)
        scheduler = StrategyScheduler(
            self.store,
            self.exchange,
            reconcile_interval_sec=5.0,
        )
        scheduler.run_once(now=0)
        for config in configs:
            self.exchange.fill(self.store.list_cells(config.strategy_id)[0].entry_order_id)
        scheduler.run_once(now=50)

        pools = {
            item["position_side"]: item for item in self.store.list_position_pools()
        }
        self.assertEqual(set(pools), {"LONG", "SHORT"})
        self.assertEqual(pools["LONG"]["status"], "consistent")
        self.assertEqual(pools["SHORT"]["status"], "consistent")
        exits = self.exchange.get_open_orders("BTCUSDT")
        self.assertTrue(
            any(order.position_side == "LONG" and order.side == OrderSide.SELL for order in exits)
        )
        self.assertTrue(
            any(order.position_side == "SHORT" and order.side == OrderSide.BUY for order in exits)
        )

    def test_soft_deleted_cells_do_not_reserve_position_resources(self):
        service = GridService(self.store, MagicMock())
        configs = [
            service.create(
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
            for _ in range(2)
        ]
        for config in configs:
            self.store.mark_started(config.strategy_id)
        scheduler = StrategyScheduler(
            self.store,
            self.exchange,
            reconcile_interval_sec=5.0,
        )
        scheduler.run_once(now=0)
        for config in configs:
            self.exchange.fill(
                self.store.list_cells(config.strategy_id)[0].entry_order_id
            )
        scheduler.run_once(now=50)

        deleted, survivor = configs
        deleted_cell = self.store.list_cells(deleted.strategy_id)[0]
        self.exchange.cancel_order("BTCUSDT", deleted_cell.exit_order_id)
        self.store.soft_delete_strategy(deleted.strategy_id)
        # The operator has manually closed the deleted group's real position.
        # Its retained SQLite audit row must no longer poison the shared pool.
        self.exchange.set_position("BTCUSDT", "LONG", Decimal("1.000"))

        scheduler.run_once(now=100)

        retained_audit_cell = self.store.list_cells(deleted.strategy_id)[0]
        active_cell = self.store.list_cells(survivor.strategy_id)[0]
        pool = self.store.list_position_pools()[0]
        self.assertEqual(retained_audit_cell.open_qty, Decimal("1.000"))
        self.assertEqual(active_cell.open_qty, Decimal("1.000"))
        self.assertEqual(active_cell.stage, CellStage.PENDING_EXIT)
        self.assertEqual(Decimal(pool["actual_qty"]), Decimal("1.000"))
        self.assertEqual(Decimal(pool["logical_qty"]), Decimal("1.000"))
        self.assertEqual(pool["status"], "consistent")


if __name__ == "__main__":
    unittest.main()
