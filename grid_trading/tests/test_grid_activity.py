from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from grid_server.application.grid_activity import completed_cycle_activity
from grid_server.application.service import GridService
from grid_server.infrastructure.sqlite_store import SQLiteStore


class GridActivityTests(unittest.TestCase):
    def setUp(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        self.store = SQLiteStore(Path(tempdir.name) / "activity.sqlite3")
        service = GridService(self.store, SimpleNamespace())
        self.strategy_id = service.create_fixed(
            "SOXLUSDT", Decimal("30"), Decimal("60"), 4,
            Decimal("400"), Decimal("0.01"),
        ).strategy_id
        self.other_id = service.create_fixed(
            "CRCLUSDT", Decimal("30"), Decimal("60"), 4,
            Decimal("400"), Decimal("0.01"),
        ).strategy_id
        self.store.mark_started(self.strategy_id)
        with self.store.connection() as conn:
            conn.execute(
                "UPDATE strategies SET first_started_at=? WHERE strategy_id=?",
                ("2026-11-22T00:00:00+00:00", self.strategy_id),
            )

    def record(self, kind: str, at: str, payload: dict | None = None,
               strategy_id: str | None = None) -> None:
        event_id = self.store.append_event(strategy_id or self.strategy_id, kind, payload)
        with self.store.connection() as conn:
            conn.execute(
                "UPDATE events SET created_at=? WHERE id=?", (at, event_id),
            )

    def test_only_completed_cycles_in_finished_beijing_periods(self) -> None:
        self.record("CYCLE_CLOSED", "2026-11-24T03:59:59+00:00")
        self.record("CYCLE_CLOSED", "2026-11-24T04:00:00+00:00")
        self.record("EXIT_ENDED", "2026-11-24T07:59:59+00:00", {"action": "cycle_closed"})
        self.record("EXIT_ENDED", "2026-11-24T07:59:59+00:00", {"action": "manual_review"})
        self.record("ENTRY_FILLED", "2026-11-24T07:59:59+00:00")
        self.record("CYCLE_CLOSED", "2026-11-24T08:00:00+00:00")
        self.record("CYCLE_CLOSED", "2026-11-24T04:30:00+00:00", strategy_id=self.other_id)

        result = completed_cycle_activity(
            self.store, self.strategy_id,
            now=datetime(2026, 11, 24, 10, 15, tzinfo=timezone.utc),
        )

        self.assertEqual(result["timezone"], "Asia/Shanghai")
        self.assertEqual(result["period_hours"], 4)
        self.assertEqual(result["periods"][-2:], [
            {"started_at": "2026-11-24T08:00+08:00", "completed_cycles": 1},
            {"started_at": "2026-11-24T12:00+08:00", "completed_cycles": 2},
        ])
        self.assertEqual(sum(point["completed_cycles"] for point in result["periods"]), 3)
        self.assertEqual(result["periods"][-1]["started_at"], "2026-11-24T12:00+08:00")

    def test_limits_history_to_fifty_periods_and_keeps_zero_periods(self) -> None:
        with self.store.connection() as conn:
            conn.execute(
                "UPDATE strategies SET first_started_at=? WHERE strategy_id=?",
                ("2026-01-01T00:00:00+00:00", self.strategy_id),
            )
        self.record("CYCLE_CLOSED", "2026-11-16T00:00:00+00:00")
        self.record("CYCLE_CLOSED", "2026-11-15T23:59:59+00:00")

        result = completed_cycle_activity(
            self.store, self.strategy_id,
            now=datetime(2026, 11, 24, 8, tzinfo=timezone.utc),
        )

        self.assertEqual(len(result["periods"]), 50)
        self.assertEqual(result["periods"][0], {
            "started_at": "2026-11-16T08:00+08:00", "completed_cycles": 1,
        })
        self.assertEqual(result["periods"][-1], {
            "started_at": "2026-11-24T12:00+08:00", "completed_cycles": 0,
        })
        self.assertEqual(sum(point["completed_cycles"] for point in result["periods"]), 1)

    def test_unstarted_strategy_has_no_periods(self) -> None:
        result = completed_cycle_activity(self.store, self.other_id)
        self.assertEqual(result["periods"], [])


if __name__ == "__main__":
    unittest.main()
