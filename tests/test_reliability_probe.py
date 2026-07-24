from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from gridtrader.application.reliability import (
    analyze_state,
    build_alerts,
    managed_client_id,
    read_database,
    summarize_jsonl,
)
from gridtrader.domain import (
    CellStage,
    GridCell,
    Mode,
    OrderSide,
    OrderSnapshot,
    OrderStatus,
    PositionSnapshot,
    StrategyConfig,
    StrategyStatus,
)
from gridtrader.store import SQLiteStore


class ReliabilityProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "probe.sqlite3"
        self.store = SQLiteStore(self.db_path)
        self.config = StrategyConfig(
            strategy_id="uniusdt-long-probe",
            symbol="UNIUSDT",
            mode=Mode.LONG,
            anchor_price=Decimal("4"),
            grid_ratio=Decimal("0.02"),
            grid_count=1,
            order_usdt=Decimal("20"),
            leverage=3,
            poll_interval_sec=60,
            status=StrategyStatus.RUNNING,
            has_started=True,
        )
        self.cell = GridCell(
            strategy_id=self.config.strategy_id,
            cell_id="cell-00000001",
            index=1,
            buy_price=Decimal("3.9"),
            sell_price=Decimal("4"),
            stage=CellStage.PENDING_ENTRY,
            entry_order_id=101,
            entry_client_id=managed_client_id(
                self.config.strategy_id, "cell-00000001", "e"
            ),
        )
        self.store.create_strategy(self.config)
        self.store.replace_cells(self.config.strategy_id, [self.cell])
        self.store.heartbeat(
            self.config.strategy_id,
            "probe-run",
            0,
            Decimal("3.95"),
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def entry_order(self, *, order_id: int = 101, client_id: str | None = None) -> OrderSnapshot:
        return OrderSnapshot(
            order_id=order_id,
            client_order_id=client_id or self.cell.entry_client_id,
            status=OrderStatus.NEW,
            side=OrderSide.BUY,
            price=Decimal("3.9"),
            original_qty=Decimal("5"),
            position_side="LONG",
        )

    def test_readonly_snapshot_and_healthy_order_alignment(self) -> None:
        database = read_database(self.db_path)
        result = analyze_state(
            database,
            {"UNIUSDT": [self.entry_order()]},
            [],
            now=datetime.now(timezone.utc),
        )

        self.assertEqual(result["anomaly_counts"]["missing_expected_order"], 0)
        self.assertEqual(result["anomaly_counts"]["unknown_managed_order"], 0)
        self.assertEqual(result["anomaly_counts"]["duplicate_order"], 0)
        self.assertEqual(result["orders"]["platform_open"], 1)

    def test_client_id_recovery_is_not_classified_as_unknown(self) -> None:
        self.cell.entry_order_id = None
        self.cell.entry_client_id = ""
        self.store.save_cell(self.cell)
        database = read_database(self.db_path)
        order = self.entry_order(
            order_id=202,
            client_id=managed_client_id(self.config.strategy_id, self.cell.cell_id, "e"),
        )

        result = analyze_state(database, {"UNIUSDT": [order]}, [])

        self.assertEqual(result["anomaly_counts"]["recoverable_order_reference"], 1)
        self.assertEqual(result["anomaly_counts"]["unknown_managed_order"], 0)
        self.assertEqual(result["anomaly_counts"]["missing_expected_order"], 0)

    def test_shortage_unknown_order_and_missing_expected_are_reported(self) -> None:
        self.cell.stage = CellStage.PENDING_EXIT
        self.cell.entry_order_id = None
        self.cell.entry_client_id = ""
        self.cell.exit_order_id = 303
        self.cell.exit_client_id = managed_client_id(
            self.config.strategy_id, self.cell.cell_id, "x"
        )
        self.cell.open_qty = Decimal("1000")
        self.store.save_cell(self.cell)
        database = read_database(self.db_path)
        unknown = self.entry_order(order_id=404, client_id="wg-deadbeef-orphan-e")

        result = analyze_state(
            database,
            {"UNIUSDT": [unknown]},
            [PositionSnapshot("UNIUSDT", "LONG", Decimal("900"))],
        )

        self.assertEqual(result["anomaly_counts"]["position_shortage"], 1)
        self.assertEqual(result["anomaly_counts"]["unknown_managed_order"], 1)
        self.assertEqual(result["anomaly_counts"]["missing_expected_order"], 1)

    def test_summary_tracks_growth_resolution_and_pid_changes(self) -> None:
        path = Path(self.tempdir.name) / "samples.jsonl"
        samples = [
            {
                "sampled_at": "2026-07-19T00:00:00+00:00",
                "overall": "warning",
                "duration_ms": 10,
                "database": {"sizes": {"db_bytes": 100, "wal_bytes": 20, "shm_bytes": 5, "total_bytes": 125}},
                "alerts": [{"code": "position_unassigned", "severity": "warning"}],
                "process": {"candidate_pids": [10]},
                "analysis": {"heartbeats": [{"strategy_id": "s1", "heartbeat_age_sec": 5}]},
            },
            {
                "sampled_at": "2026-07-19T00:05:00+00:00",
                "overall": "ok",
                "duration_ms": 12,
                "database": {"sizes": {"db_bytes": 120, "wal_bytes": 10, "shm_bytes": 5, "total_bytes": 135}},
                "alerts": [],
                "process": {"candidate_pids": [11]},
                "analysis": {"heartbeats": [{"strategy_id": "s1", "heartbeat_age_sec": 7}]},
            },
        ]
        path.write_text("".join(json.dumps(item) + "\n" for item in samples), encoding="utf-8")

        summary = summarize_jsonl(path)

        self.assertEqual(summary["sample_count"], 2)
        self.assertEqual(summary["database_sizes"]["growth_bytes"]["db_bytes"], 20)
        self.assertEqual(summary["resolved_since_first"], ["position_unassigned"])
        self.assertEqual(summary["scheduler_pid_history"], [[10], [11]])
        self.assertEqual(summary["max_heartbeat_age_sec"]["s1"], 7)

    def test_api_connected_to_another_database_is_critical(self) -> None:
        alerts = build_alerts(
            {
                "analysis": {"active_strategy_count": 0, "anomaly_counts": {}},
                "process": {"candidate_pids": []},
                "http": {
                    "api": {"checked": True, "ok": True},
                    "api_database_alignment": {
                        "checked": True,
                        "ok": False,
                        "missing_in_api": [self.config.strategy_id],
                    },
                    "streamlit": {"checked": False},
                },
                "errors": [],
            }
        )

        self.assertIn(
            ("critical", "api_database_mismatch"),
            {(item["severity"], item["code"]) for item in alerts},
        )


if __name__ == "__main__":
    unittest.main()
