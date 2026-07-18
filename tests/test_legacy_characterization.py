from __future__ import annotations

import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

LEGACY_ROOT = Path(__file__).resolve().parents[1] / "legacy_grid"
sys.path.insert(0, str(LEGACY_ROOT))
sys.path.insert(0, str(LEGACY_ROOT / "tests"))

from dual_trigger_grid import DualTriggerGrid, Filters, StrategyConfig  # noqa: E402
from test_grid_stub import FakeClient  # type: ignore  # noqa: E402


class LegacyCharacterizationTests(unittest.TestCase):
    def test_long_crossing_places_buy_and_fill_places_sell(self):
        with tempfile.TemporaryDirectory() as tempdir:
            cfg = StrategyConfig(
                symbol="BTCUSDT",
                window_cells=1,
                move_grid=False,
                grid_ratio=Decimal("0.10"),
                order_usdt=Decimal("100"),
                leverage=3,
                mode="long",
                poll_interval_sec=1,
                status_interval_sec=1000,
                csv_path=str(Path(tempdir) / "legacy.csv"),
                strategy_id="legacy",
                anchor_price=Decimal("100"),
            )
            client = FakeClient(marks=["105"])
            filters = Filters(Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal("0"))
            bot = DualTriggerGrid(client, cfg, filters)

            bot.tick()
            self.assertEqual(client.placed[-1]["side"], "BUY")
            self.assertEqual(client.placed[-1]["price"], Decimal("100"))

            entry_id = bot.cells[0].long_entry
            client.order_map[entry_id]["status"] = "FILLED"
            bot._sync_orders()
            self.assertEqual(client.placed[-1]["side"], "SELL")
            self.assertEqual(client.placed[-1]["price"], Decimal("110"))


if __name__ == "__main__":
    unittest.main()
