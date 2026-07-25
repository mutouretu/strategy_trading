from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from grid_server.domain import Mode, StrategyConfig, StrategyStatus
from grid_server.grid_math import build_cells
from grid_server.store import SQLiteStore


class SQLiteStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tempdir.name) / "test.sqlite3")
        self.config = StrategyConfig(
            "btc-long", "BTCUSDT", Mode.LONG, Decimal("110"), Decimal("0.10"), 3, Decimal("100")
        )
        self.store.create_strategy(self.config)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_draft_can_be_updated_before_start(self):
        changed = replace(self.config, grid_count=4)
        self.store.update_draft(changed)
        self.assertEqual(self.store.get_strategy("btc-long").grid_count, 4)

    def test_configuration_is_irreversibly_locked_on_first_start(self):
        self.store.mark_started("btc-long")
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.store.update_draft(replace(self.config, grid_count=4))
        loaded = self.store.get_strategy("btc-long")
        self.assertTrue(loaded.has_started)
        self.assertEqual(loaded.status, StrategyStatus.STARTING)

    def test_cells_and_events_survive_store_reopen(self):
        cells = build_cells(self.config, Decimal("0.01"))
        cells[0].open_qty = Decimal("0.600")
        cells[0].exit_executed_qty = Decimal("0.400")
        cells[0].entry_filled_at = "2026-07-16T00:00:00+00:00"
        self.store.replace_cells("btc-long", cells)
        self.store.append_event("btc-long", "TEST", {"answer": 42}, cells[0].cell_id)

        reopened = SQLiteStore(self.store.path)
        self.assertEqual(len(reopened.list_cells("btc-long")), 3)
        restored = reopened.list_cells("btc-long")[0]
        self.assertEqual(restored.exit_executed_qty, Decimal("0.400"))
        self.assertEqual(restored.entry_filled_at, "2026-07-16T00:00:00+00:00")
        self.assertEqual(reopened.list_events("btc-long")[0]["payload"], {"answer": 42})

    def test_current_order_quantities_are_read_from_order_events(self):
        self.store.append_event(
            "btc-long",
            "ENTRY_PLACED",
            {"order_id": 101, "qty": "1.250"},
            "cell-a",
        )
        self.store.append_event(
            "btc-long",
            "EXIT_PLACED",
            {"order_id": 202, "qty": "0.750"},
            "cell-a",
        )

        quantities = self.store.get_order_quantities("btc-long", {101, 202, 999})

        self.assertEqual(
            quantities,
            {101: Decimal("1.250"), 202: Decimal("0.750")},
        )

    def test_delete_is_soft_and_preserves_audit_data(self):
        self.store.soft_delete_strategy("btc-long")
        self.assertIsNone(self.store.get_strategy("btc-long"))
        self.assertIsNotNone(self.store.get_strategy("btc-long", include_deleted=True))

    def test_multiple_groups_may_share_symbol_and_direction(self):
        second = replace(self.config, strategy_id="btc-long-second", anchor_price=Decimal("120"))
        self.store.create_strategy(second)
        loaded = self.store.list_strategies()
        self.assertEqual({item.strategy_id for item in loaded}, {"btc-long", "btc-long-second"})

    def test_scheduler_audit_records_runs_gaps_and_aggregated_incidents(self):
        self.store.record_scheduler_run_start(
            "run-a",
            123,
            observed_at="2026-07-23T00:00:00+00:00",
        )
        self.store.record_scheduler_gap(
            "run-a",
            "2026-07-23T00:00:00+00:00",
            "2026-07-23T00:10:00+00:00",
            600,
            1,
        )
        first = self.store.record_scheduler_failure(
            "strategy:btc-long",
            "run-a",
            ConnectionError("offline"),
            strategy_id="btc-long",
        )
        repeated = self.store.record_scheduler_failure(
            "strategy:btc-long",
            "run-a",
            ConnectionError("still offline"),
            strategy_id="btc-long",
        )
        recovered = self.store.record_scheduler_recovery(
            "strategy:btc-long",
            "run-a",
        )
        self.store.stop_scheduler_run(
            "run-a",
            "stop_requested",
            observed_at="2026-07-23T00:11:00+00:00",
        )

        self.assertTrue(first["opened"])
        self.assertFalse(repeated["opened"])
        self.assertEqual(repeated["failure_count"], 2)
        self.assertEqual(recovered["failure_count"], 2)
        self.assertIsNotNone(recovered["recovered_at"])
        self.assertEqual(self.store.list_scheduler_gaps()[0]["gap_seconds"], 600.0)
        self.assertEqual(
            self.store.list_scheduler_runs()[0]["stop_reason"],
            "stop_requested",
        )


if __name__ == "__main__":
    unittest.main()
