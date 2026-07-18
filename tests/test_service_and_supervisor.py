from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from gridtrader.domain import Mode, StrategyStatus
from gridtrader.service import GridService
from gridtrader.store import SQLiteStore
from gridtrader.supervisor import StrategySupervisor


class ServiceAndSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tempdir.name) / "service.sqlite3")
        self.supervisor = StrategySupervisor(self.store, Path(self.tempdir.name) / "logs")
        self.service = GridService(self.store, self.supervisor)
        self.config = self.service.create(
            "BTCUSDT", Mode.LONG, Decimal("110"), Decimal("0.10"), 3,
            Decimal("100"), 3, Decimal("0.01")
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_service_preview_and_create_use_same_cells(self):
        _, preview = self.service.preview(
            "ETHUSDT", Mode.SHORT, Decimal("100"), Decimal("0.10"), 2,
            Decimal("100"), 3, Decimal("0.01")
        )
        self.assertEqual([(cell.buy_price, cell.sell_price) for cell in preview], [
            (Decimal("100"), Decimal("110")),
            (Decimal("110"), Decimal("121")),
        ])

    @patch("gridtrader.supervisor.subprocess.Popen")
    def test_first_start_locks_config_before_process_spawn(self, popen):
        process = MagicMock()
        process.pid = 43210
        process.poll.return_value = None
        popen.return_value = process

        pid = self.service.start(self.config.strategy_id)
        self.assertEqual(pid, 43210)
        self.assertTrue(self.store.get_strategy(self.config.strategy_id).has_started)
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.service.update_draft(
                self.service.editable_copy(self.config.strategy_id, grid_count=4), Decimal("0.01")
            )

    @patch("gridtrader.supervisor.subprocess.Popen", side_effect=OSError("spawn failed"))
    def test_spawn_failure_does_not_unlock_configuration(self, _popen):
        with self.assertRaises(OSError):
            self.service.start(self.config.strategy_id)
        loaded = self.store.get_strategy(self.config.strategy_id)
        self.assertTrue(loaded.has_started)
        self.assertEqual(loaded.status, StrategyStatus.ERROR)

    @patch("gridtrader.supervisor.subprocess.Popen")
    def test_multiple_strategies_share_one_scheduler_and_stop_is_isolated(self, popen):
        process = MagicMock()
        process.pid = 43210
        process.poll.return_value = None
        popen.return_value = process
        second = self.service.create(
            "ETHUSDT", Mode.SHORT, Decimal("100"), Decimal("0.10"), 3,
            Decimal("100"), 3, Decimal("0.01")
        )

        first_pid = self.service.start(self.config.strategy_id)
        second_pid = self.service.start(second.strategy_id)
        self.assertEqual((first_pid, second_pid), (43210, 43210))
        self.assertEqual(popen.call_count, 1)

        self.service.stop(self.config.strategy_id)
        self.assertEqual(self.store.get_strategy(self.config.strategy_id).status, StrategyStatus.STOPPED)
        self.assertTrue(self.supervisor.is_running(second.strategy_id))
        self.assertIsNone(process.terminate.call_args)


if __name__ == "__main__":
    unittest.main()
