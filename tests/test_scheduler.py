from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from gridtrader.domain import Mode, StrategyStatus
from gridtrader.scheduler import StrategyScheduler
from gridtrader.service import GridService
from gridtrader.store import SQLiteStore

from tests.fakes import FakeExchange


class StrategySchedulerLoadTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tempdir.name) / "scheduler.sqlite3")
        self.service = GridService(self.store, MagicMock())
        self.exchange = FakeExchange(Decimal("105"))

    def tearDown(self):
        self.tempdir.cleanup()

    def test_fifty_groups_with_five_cells_share_one_symbol_snapshot(self):
        strategy_ids = []
        for _ in range(50):
            config = self.service.create(
                "BTCUSDT",
                Mode.LONG,
                Decimal("110"),
                Decimal("0.10"),
                5,
                Decimal("100"),
                3,
                Decimal("0.01"),
                poll_interval_sec=50.0,
                move_grid=False,
            )
            strategy_ids.append(config.strategy_id)
            self.store.mark_started(config.strategy_id)

        scheduler = StrategyScheduler(self.store, self.exchange, pid=24680)
        self.assertEqual(scheduler.run_once(now=0), 50)
        self.assertEqual(len(scheduler.engines), 50)
        self.assertEqual(len(self.exchange.get_open_orders("BTCUSDT")), 250)

        client_ids = {order.client_order_id for order in self.exchange.get_open_orders("BTCUSDT")}
        self.assertEqual(len(client_ids), 250)
        self.assertEqual(self.exchange.calls["get_mark_price"], 1)
        self.assertEqual(self.exchange.calls["get_symbol_filters"], 1)
        self.assertEqual(self.exchange.calls["set_hedge_mode"], 1)
        self.assertEqual(self.exchange.calls["set_leverage"], 1)
        self.assertEqual(self.exchange.calls["get_open_orders"], 3)

        # Not due yet: no exchange call and no strategy work.
        before = dict(self.exchange.calls)
        self.assertEqual(scheduler.run_once(now=49), 0)
        self.assertEqual(self.exchange.calls, before)

        # On the next due cycle, all 250 still-open orders come from one
        # openOrders snapshot; no per-order query is needed.
        self.assertEqual(scheduler.run_once(now=50), 50)
        self.assertEqual(self.exchange.calls["get_mark_price"], 2)
        self.assertEqual(self.exchange.calls["get_open_orders"], 4)
        self.assertEqual(self.exchange.calls.get("get_order", 0), 0)

        self.assertTrue(all(self.store.get_strategy(item).poll_interval_sec == 50.0 for item in strategy_ids))

    def test_only_due_strategy_runs(self):
        fast = self.service.create(
            "BTCUSDT", Mode.LONG, Decimal("110"), Decimal("0.10"), 1,
            Decimal("100"), 3, Decimal("0.01"), poll_interval_sec=50.0, move_grid=False
        )
        slow = self.service.create(
            "ETHUSDT", Mode.SHORT, Decimal("100"), Decimal("0.10"), 1,
            Decimal("100"), 3, Decimal("0.01"), poll_interval_sec=180.0, move_grid=False
        )
        self.store.mark_started(fast.strategy_id)
        self.store.mark_started(slow.strategy_id)
        scheduler = StrategyScheduler(self.store, self.exchange)

        self.assertEqual(scheduler.run_once(now=0), 2)
        self.assertEqual(scheduler.run_once(now=50), 1)
        self.assertEqual(scheduler.run_once(now=179), 1)
        self.assertEqual(scheduler.run_once(now=180), 1)

    def test_fifty_symbols_query_once_per_symbol_not_once_per_cell(self):
        for index in range(50):
            config = self.service.create(
                f"COIN{index:02d}USDT",
                Mode.LONG,
                Decimal("110"),
                Decimal("0.10"),
                5,
                Decimal("100"),
                3,
                Decimal("0.01"),
                poll_interval_sec=50.0,
                move_grid=False,
            )
            self.store.mark_started(config.strategy_id)

        scheduler = StrategyScheduler(self.store, self.exchange)
        self.assertEqual(scheduler.run_once(now=0), 50)
        self.assertEqual(self.exchange.calls["place_limit_order"], 250)
        self.assertEqual(self.exchange.calls["get_mark_price"], 50)
        self.assertEqual(self.exchange.calls["get_open_orders"], 50)
        self.assertEqual(self.exchange.calls.get("get_order", 0), 0)

        self.assertEqual(scheduler.run_once(now=50), 50)
        self.assertEqual(self.exchange.calls["get_mark_price"], 100)
        self.assertEqual(self.exchange.calls["get_open_orders"], 100)
        self.assertEqual(self.exchange.calls.get("get_order", 0), 0)

    def test_position_rewrite_is_skipped_when_any_strategy_sync_fails(self):
        config = self.service.create(
            "BTCUSDT", Mode.LONG, Decimal("110"), Decimal("0.10"), 1,
            Decimal("100"), 3, Decimal("0.01"), poll_interval_sec=50.0, move_grid=False
        )
        self.store.mark_started(config.strategy_id)

        def fail_mark(_symbol: str):
            raise RuntimeError("market unavailable")

        self.exchange.get_mark_price = fail_mark  # type: ignore[method-assign]
        scheduler = StrategyScheduler(self.store, self.exchange, reconcile_interval_sec=5.0)
        scheduler.run_once(now=0)

        self.assertNotIn("get_positions", self.exchange.calls)
        self.assertIn("position rewrite skipped", scheduler.last_reconcile_error)

    def test_market_request_failure_waits_until_next_poll_before_retrying(self):
        config = self.service.create(
            "BTCUSDT", Mode.LONG, Decimal("110"), Decimal("0.10"), 1,
            Decimal("100"), 3, Decimal("0.01"), poll_interval_sec=50.0, move_grid=False
        )
        self.store.mark_started(config.strategy_id)
        original_mark = self.exchange.get_mark_price
        attempts = 0

        def fail_once(symbol: str):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("HTTP 429 market request")
            return original_mark(symbol)

        self.exchange.get_mark_price = fail_once  # type: ignore[method-assign]
        scheduler = StrategyScheduler(self.store, self.exchange, reconcile_interval_sec=60.0)

        self.assertEqual(scheduler.run_once(now=0), 1)
        self.assertEqual(attempts, 1)
        self.assertEqual(self.store.get_strategy(config.strategy_id).status, StrategyStatus.ERROR)
        incidents = self.store.list_scheduler_incidents()
        self.assertEqual(len(incidents), 1)
        self.assertIsNone(incidents[0]["recovered_at"])
        self.assertEqual(incidents[0]["failure_count"], 1)
        self.assertEqual(scheduler.run_once(now=1), 0)
        self.assertEqual(scheduler.run_once(now=49), 0)
        self.assertEqual(attempts, 1)

        self.assertEqual(scheduler.run_once(now=50), 1)
        self.assertEqual(attempts, 2)
        self.assertEqual(self.store.get_strategy(config.strategy_id).status, StrategyStatus.RUNNING)
        self.assertIsNotNone(self.store.list_cells(config.strategy_id)[0].entry_order_id)
        incident = self.store.list_scheduler_incidents()[0]
        self.assertIsNotNone(incident["recovered_at"])
        event_types = {
            event["event_type"]
            for event in self.store.list_events(config.strategy_id)
        }
        self.assertIn("SCHEDULER_FAILURE_STARTED", event_types)
        self.assertIn("SCHEDULER_RECOVERED", event_types)

    def test_long_loop_pause_is_persisted_as_one_gap(self):
        wall_time = [100.0]
        scheduler = StrategyScheduler(
            self.store,
            self.exchange,
            wall_clock=lambda: wall_time[0],
            gap_threshold_sec=5.0,
        )

        # Normal strategy processing may take longer than the gap threshold;
        # completing the loop moves the idle-gap baseline forward.
        wall_time[0] = 112.5
        scheduler.complete_loop()
        wall_time[0] = 113.0
        scheduler.observe_loop()
        self.assertEqual(self.store.list_scheduler_gaps(), [])

        wall_time[0] = 125.5
        scheduler.observe_loop()

        gaps = self.store.list_scheduler_gaps()
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["gap_seconds"], 12.5)
        self.assertEqual(gaps[0]["active_strategy_count"], 0)

    def test_pending_cell_action_runs_before_regular_poll_is_due(self):
        config = self.service.create(
            "BTCUSDT", Mode.LONG, Decimal("110"), Decimal("0.10"), 1,
            Decimal("100"), 3, Decimal("0.01"), poll_interval_sec=50.0, move_grid=True
        )
        self.store.mark_started(config.strategy_id)
        scheduler = StrategyScheduler(self.store, self.exchange)
        self.assertEqual(scheduler.run_once(now=0), 1)
        self.assertEqual(self.store.get_strategy(config.strategy_id).grid_count, 1)

        self.store.request_cell_action(config.strategy_id, "add", "upper")
        self.assertEqual(scheduler.run_once(now=1), 1)

        self.assertEqual(self.store.get_strategy(config.strategy_id).grid_count, 2)
        self.assertEqual(len(self.store.list_cells(config.strategy_id)), 2)
        self.assertEqual(
            self.store.list_cell_actions(config.strategy_id)[0]["status"],
            "completed",
        )

    def test_delete_during_tick_does_not_terminate_other_strategies(self):
        removed = self.service.create(
            "BTCUSDT", Mode.LONG, Decimal("110"), Decimal("0.10"), 1,
            Decimal("100"), 3, Decimal("0.01"), poll_interval_sec=50.0, move_grid=False
        )
        survivor = self.service.create(
            "ETHUSDT", Mode.LONG, Decimal("110"), Decimal("0.10"), 1,
            Decimal("100"), 3, Decimal("0.01"), poll_interval_sec=50.0, move_grid=False
        )
        self.store.mark_started(removed.strategy_id)
        self.store.mark_started(survivor.strategy_id)

        original_mark = self.exchange.get_mark_price
        deleted = False

        def delete_first_strategy_during_tick(symbol: str):
            nonlocal deleted
            if symbol == "BTCUSDT" and not deleted:
                deleted = True
                self.store.soft_delete_strategy(removed.strategy_id)
            return original_mark(symbol)

        self.exchange.get_mark_price = delete_first_strategy_during_tick  # type: ignore[method-assign]
        scheduler = StrategyScheduler(self.store, self.exchange, pid=24680)

        self.assertEqual(scheduler.run_once(now=0), 2)
        self.assertNotIn(removed.strategy_id, scheduler.engines)
        self.assertIsNone(self.store.get_strategy(removed.strategy_id))
        self.assertEqual(
            self.store.get_strategy(survivor.strategy_id).status,
            StrategyStatus.RUNNING,
        )
        survivor_cell = self.store.list_cells(survivor.strategy_id)[0]
        self.assertIsNotNone(survivor_cell.entry_order_id)

    def test_stop_during_tick_cannot_be_overwritten_by_running_heartbeat(self):
        config = self.service.create(
            "BTCUSDT", Mode.LONG, Decimal("110"), Decimal("0.10"), 1,
            Decimal("100"), 3, Decimal("0.01"), poll_interval_sec=50.0, move_grid=False
        )
        self.store.mark_started(config.strategy_id)
        original_mark = self.exchange.get_mark_price
        stopped = False

        def stop_strategy_during_tick(symbol: str):
            nonlocal stopped
            if not stopped:
                stopped = True
                self.store.mark_runtime_stopped(config.strategy_id)
                self.store.set_status(config.strategy_id, StrategyStatus.STOPPED)
            return original_mark(symbol)

        self.exchange.get_mark_price = stop_strategy_during_tick  # type: ignore[method-assign]
        scheduler = StrategyScheduler(self.store, self.exchange, pid=24680)
        scheduler.run_once(now=0)

        saved = self.store.get_strategy(config.strategy_id)
        runtime = self.store.get_runtime(config.strategy_id)
        self.assertEqual(saved.status, StrategyStatus.STOPPED)
        self.assertIsNotNone(runtime["stopped_at"])


if __name__ == "__main__":
    unittest.main()
