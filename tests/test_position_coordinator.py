from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from grid_server.domain import CellStage, Mode, OrderSide, StrategyStatus
from grid_server.scheduler import StrategyScheduler
from grid_server.service import GridService
from grid_server.store import SQLiteStore

from tests.fakes import FakeExchange


class PositionCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tempdir.name) / "positions.sqlite3")
        self.service = GridService(self.store, MagicMock())
        self.exchange = FakeExchange(Decimal("105"))
        self.strategy_ids: list[str] = []
        for _ in range(2):
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
            self.strategy_ids.append(config.strategy_id)
            self.store.mark_started(config.strategy_id)
        self.scheduler = StrategyScheduler(
            self.store,
            self.exchange,
            reconcile_interval_sec=5.0,
        )
        self.scheduler.run_once(now=0)
        for strategy_id in self.strategy_ids:
            entry_id = self.store.list_cells(strategy_id)[0].entry_order_id
            self.exchange.fill(entry_id)
        self.scheduler.run_once(now=50)

    def tearDown(self):
        self.tempdir.cleanup()

    def cells(self):
        return [self.store.list_cells(strategy_id)[0] for strategy_id in self.strategy_ids]

    def test_canceled_exit_is_restored_when_aggregate_position_is_sufficient(self):
        target = self.cells()[0]
        old_exit_id = target.exit_order_id
        self.exchange.cancel_order("BTCUSDT", old_exit_id)

        self.scheduler.run_once(now=100)
        restored = self.store.list_cells(target.strategy_id)[0]
        self.assertEqual(restored.stage, CellStage.PENDING_EXIT)
        self.assertIsNotNone(restored.exit_order_id)
        self.assertNotEqual(restored.exit_order_id, old_exit_id)
        self.assertEqual(restored.open_qty, Decimal("1.000"))
        event_types = [
            event["event_type"] for event in self.store.list_events(target.strategy_id)
        ]
        self.assertIn("POSITION_EXIT_RESTORED", event_types)

    def test_position_shortage_resizes_last_allocated_cell(self):
        self.exchange.set_position("BTCUSDT", "LONG", Decimal("1.400"))

        self.scheduler.run_once(now=100)
        self.assertEqual(
            [cell.open_qty for cell in self.cells()],
            [Decimal("1.000"), Decimal("1.000")],
        )
        self.assertEqual(self.store.list_position_pools()[0]["status"], "shortage_pending")

        self.scheduler.run_once(now=105)
        cells = self.cells()
        self.assertEqual(sorted(cell.open_qty for cell in cells), [Decimal("0.400"), Decimal("1.000")])
        self.assertTrue(all(cell.stage == CellStage.PENDING_EXIT for cell in cells))
        exit_orders = [
            order
            for order in self.exchange.get_open_orders("BTCUSDT")
            if order.side == OrderSide.SELL
        ]
        self.assertEqual(
            sum((order.original_qty - order.executed_qty for order in exit_orders), Decimal("0")),
            Decimal("1.400"),
        )
        pool = self.store.list_position_pools()[0]
        self.assertEqual(pool["status"], "consistent")
        self.assertEqual(pool["actual_qty"], "1.400")
        self.assertEqual(pool["logical_qty"], "1.400")

    def test_transient_position_shortage_never_rewrites_cell_ownership(self):
        original_exit_ids = [cell.exit_order_id for cell in self.cells()]
        self.exchange.set_position("BTCUSDT", "LONG", Decimal("1.400"))

        self.scheduler.run_once(now=100)

        pending = self.cells()
        self.assertEqual([cell.open_qty for cell in pending], [Decimal("1.000")] * 2)
        self.assertEqual([cell.exit_order_id for cell in pending], original_exit_ids)
        self.assertEqual(self.store.list_position_pools()[0]["status"], "shortage_pending")

        self.exchange.set_position("BTCUSDT", "LONG", Decimal("2.000"))
        self.scheduler.run_once(now=105)

        recovered = self.cells()
        self.assertEqual([cell.open_qty for cell in recovered], [Decimal("1.000")] * 2)
        self.assertEqual([cell.exit_order_id for cell in recovered], original_exit_ids)
        pool = self.store.list_position_pools()[0]
        self.assertEqual(pool["status"], "consistent")
        self.assertEqual(pool["unassigned_qty"], "0")
        self.assertEqual(pool["shortage_qty"], "0")

    def test_position_shortage_uses_price_distance_even_when_near_exit_is_active(self):
        farther, nearer = self.cells()
        farther.buy_price = Decimal("70")
        farther.sell_price = Decimal("77")
        self.store.save_cell(farther)
        active_exit_id = nearer.exit_order_id
        self.exchange.cancel_order("BTCUSDT", farther.exit_order_id)
        self.exchange.set_position("BTCUSDT", "LONG", Decimal("1.400"))

        self.scheduler.run_once(now=100)
        self.scheduler.run_once(now=105)

        farther_after = self.store.list_cells(farther.strategy_id)[0]
        nearer_after = self.store.list_cells(nearer.strategy_id)[0]
        self.assertEqual(farther_after.open_qty, Decimal("1.000"))
        self.assertEqual(nearer_after.open_qty, Decimal("0.400"))
        self.assertNotEqual(nearer_after.exit_order_id, active_exit_id)
        self.assertEqual(
            self.exchange.orders[nearer_after.exit_order_id].original_qty,
            Decimal("0.400"),
        )
        self.assertIsNotNone(farther_after.exit_order_id)
        self.assertEqual(
            self.exchange.orders[farther_after.exit_order_id].original_qty,
            Decimal("1.000"),
        )

    def test_fill_during_cancel_replans_from_fresh_position(self):
        self.exchange.set_position("BTCUSDT", "LONG", Decimal("1.400"))
        original_cancel = self.exchange.cancel_order
        raced = False

        def fill_then_cancel(symbol: str, order_id: int) -> None:
            nonlocal raced
            if not raced:
                raced = True
                self.exchange.partial_fill(order_id, Decimal("0.200"))
            original_cancel(symbol, order_id)

        self.exchange.cancel_order = fill_then_cancel  # type: ignore[method-assign]
        self.scheduler.run_once(now=100)
        self.scheduler.run_once(now=105)
        self.scheduler.run_once(now=110)

        cells = self.cells()
        self.assertEqual(sorted(cell.open_qty for cell in cells), [Decimal("0.200"), Decimal("1.000")])
        exits = [
            order
            for order in self.exchange.get_open_orders("BTCUSDT")
            if order.side == OrderSide.SELL
        ]
        self.assertEqual(
            sum((order.original_qty - order.executed_qty for order in exits), Decimal("0")),
            Decimal("1.200"),
        )
        pool = self.store.list_position_pools()[0]
        self.assertEqual(pool["actual_qty"], "1.200")
        self.assertEqual(pool["status"], "consistent")

    def test_zero_position_releases_cells_then_rearms_running_groups(self):
        self.exchange.set_position("BTCUSDT", "LONG", Decimal("0"))

        self.scheduler.run_once(now=100)
        self.scheduler.run_once(now=105)
        released = self.cells()
        self.assertTrue(all(cell.stage == CellStage.UNTRIGGERED for cell in released))
        self.assertTrue(all(cell.open_qty == 0 for cell in released))
        self.assertEqual(self.exchange.get_open_orders("BTCUSDT"), [])

        self.scheduler.run_once(now=106)
        rearmed = self.cells()
        self.assertTrue(all(cell.stage == CellStage.PENDING_ENTRY for cell in rearmed))
        self.assertEqual(len(self.exchange.get_open_orders("BTCUSDT")), 2)

    def test_stopped_group_reserves_its_position_without_being_mutated(self):
        stopped_id, running_id = self.strategy_ids
        stopped_before = self.store.list_cells(stopped_id)[0]
        stopped_exit_id = stopped_before.exit_order_id
        self.store.mark_runtime_stopped(stopped_id)
        self.store.set_status(stopped_id, StrategyStatus.STOPPED)
        self.exchange.set_position("BTCUSDT", "LONG", Decimal("1.400"))

        self.scheduler.run_once(now=100)
        self.scheduler.run_once(now=105)
        stopped = self.store.list_cells(stopped_id)[0]
        running = self.store.list_cells(running_id)[0]
        self.assertEqual(stopped.open_qty, Decimal("1.000"))
        self.assertEqual(stopped.exit_order_id, stopped_exit_id)
        self.assertEqual(self.exchange.orders[stopped_exit_id].status.value, "NEW")
        self.assertEqual(running.open_qty, Decimal("0.400"))
        self.assertEqual(running.stage, CellStage.PENDING_EXIT)

    def test_external_close_order_reserves_position_before_grid_cells(self):
        external_id = self.exchange.place_limit_order(
            "BTCUSDT",
            OrderSide.SELL,
            "LONG",
            Decimal("0.500"),
            Decimal("120"),
            "manual-close",
        )

        self.scheduler.run_once(now=100)
        # The pending external order reduces grid exit coverage but does not
        # shrink logical cell ownership until it actually fills.
        self.assertEqual(
            sorted(cell.open_qty for cell in self.cells()),
            [Decimal("1.000"), Decimal("1.000")],
        )
        grid_exits = [
            order
            for order in self.exchange.get_open_orders("BTCUSDT")
            if order.order_id != external_id and order.side == OrderSide.SELL
        ]
        self.assertEqual(
            sum((order.original_qty - order.executed_qty for order in grid_exits), Decimal("0")),
            Decimal("1.500"),
        )
        pool = self.store.list_position_pools()[0]
        self.assertEqual(pool["logical_qty"], "2.00")
        self.assertEqual(pool["external_reserved_qty"], "0.500")
        self.assertEqual(pool["status"], "consistent")

    def test_canceled_external_close_returns_exit_capacity_to_original_cell(self):
        external_id = self.exchange.place_limit_order(
            "BTCUSDT",
            OrderSide.SELL,
            "LONG",
            Decimal("0.500"),
            Decimal("120"),
            "manual-close-cancelable",
        )
        self.scheduler.run_once(now=100)
        self.exchange.cancel_order("BTCUSDT", external_id)

        self.scheduler.run_once(now=105)

        cells = self.cells()
        self.assertEqual([cell.open_qty for cell in cells], [Decimal("1.000")] * 2)
        grid_exits = [
            order
            for order in self.exchange.get_open_orders("BTCUSDT")
            if order.side == OrderSide.SELL
        ]
        self.assertEqual(
            sum((order.original_qty - order.executed_qty for order in grid_exits), Decimal("0")),
            Decimal("2.000"),
        )
        self.assertTrue(all(cell.stage == CellStage.PENDING_EXIT for cell in cells))

    def test_partial_external_close_shrinks_ownership_only_by_executed_quantity(self):
        external_id = self.exchange.place_limit_order(
            "BTCUSDT",
            OrderSide.SELL,
            "LONG",
            Decimal("0.500"),
            Decimal("120"),
            "manual-close-partial",
        )
        self.exchange.partial_fill(external_id, Decimal("0.200"))

        self.scheduler.run_once(now=100)
        self.scheduler.run_once(now=105)

        self.assertEqual(
            sorted(cell.open_qty for cell in self.cells()),
            [Decimal("0.800"), Decimal("1.000")],
        )
        grid_exits = [
            order
            for order in self.exchange.get_open_orders("BTCUSDT")
            if order.order_id != external_id and order.side == OrderSide.SELL
        ]
        self.assertEqual(
            sum((order.original_qty - order.executed_qty for order in grid_exits), Decimal("0")),
            Decimal("1.500"),
        )
        pool = self.store.list_position_pools()[0]
        self.assertEqual(pool["actual_qty"], "1.800")
        self.assertEqual(pool["logical_qty"], "1.800")
        self.assertEqual(pool["external_reserved_qty"], "0.300")

    def test_resized_grid_exit_fill_does_not_falsely_close_whole_cell(self):
        external_id = self.exchange.place_limit_order(
            "BTCUSDT",
            OrderSide.SELL,
            "LONG",
            Decimal("0.500"),
            Decimal("120"),
            "manual-close-reservation",
        )
        self.scheduler.run_once(now=100)
        resized_cell = next(
            cell
            for cell in self.cells()
            if self.exchange.orders[cell.exit_order_id].original_qty == Decimal("0.500")
        )
        resized_exit_id = resized_cell.exit_order_id

        self.exchange.fill(resized_exit_id)
        self.scheduler.run_once(now=105)

        remaining = self.store.list_cells(resized_cell.strategy_id)[0]
        self.assertEqual(remaining.open_qty, Decimal("0.500"))
        self.assertEqual(remaining.cycle_count, 0)
        self.assertEqual(remaining.stage, CellStage.MANUAL_REVIEW)
        self.assertIsNone(remaining.exit_order_id)
        self.assertEqual(self.exchange.orders[external_id].status.value, "NEW")
        pool = self.store.list_position_pools()[0]
        self.assertEqual(pool["actual_qty"], "1.500")
        self.assertEqual(pool["logical_qty"], "1.500")
        self.assertEqual(pool["status"], "consistent")

    def test_short_pool_is_synchronized_between_slow_strategy_scans(self):
        short_ids = []
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
            short_ids.append(config.strategy_id)
            self.store.mark_started(config.strategy_id)

        # The new strategies place SELL entries at their own scan time.
        self.scheduler.run_once(now=51)
        for strategy_id in short_ids:
            entry_id = self.store.list_cells(strategy_id)[0].entry_order_id
            self.exchange.fill(entry_id)

        # Their next price scan is not due, but the account reconciliation cycle
        # sees the fills and creates BUY exits.
        self.scheduler.run_once(now=55)
        self.exchange.set_position("ETHUSDT", "SHORT", Decimal("1.400"))
        self.scheduler.run_once(now=60)
        self.scheduler.run_once(now=65)

        cells = [self.store.list_cells(strategy_id)[0] for strategy_id in short_ids]
        self.assertEqual(sorted(cell.open_qty for cell in cells), [Decimal("0.491"), Decimal("0.909")])
        exits = [
            order
            for order in self.exchange.get_open_orders("ETHUSDT")
            if order.side == OrderSide.BUY
        ]
        self.assertEqual(
            sum((order.original_qty - order.executed_qty for order in exits), Decimal("0")),
            Decimal("1.400"),
        )


if __name__ == "__main__":
    unittest.main()
