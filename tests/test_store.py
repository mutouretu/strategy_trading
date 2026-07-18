from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from gridtrader.domain import Mode, StrategyConfig, StrategyStatus
from gridtrader.grid_math import build_cells
from gridtrader.store import SQLiteStore


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


if __name__ == "__main__":
    unittest.main()
