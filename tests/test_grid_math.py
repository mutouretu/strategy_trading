from __future__ import annotations

import unittest
from decimal import Decimal

from grid_server.domain import Mode, StrategyConfig
from grid_server.grid_math import build_cells


class GridMathTests(unittest.TestCase):
    def test_long_uses_web_anchor_as_highest_price_and_derives_downward(self):
        config = StrategyConfig(
            "btc-long", "BTCUSDT", Mode.LONG, Decimal("110"), Decimal("0.10"), 3, Decimal("100")
        )
        cells = build_cells(config, Decimal("0.01"))

        self.assertEqual(len(cells), 3)
        self.assertEqual(cells[-1].sell_price, Decimal("110"))
        self.assertEqual(cells[-1].buy_price, Decimal("100"))
        # Every boundary is rounded down to tickSize before the next cell is derived.
        self.assertEqual(cells[0].buy_price, Decimal("82.63"))
        self.assertEqual([cell.index for cell in cells], [1, 2, 3])

    def test_short_uses_web_anchor_as_lowest_price_and_derives_upward(self):
        config = StrategyConfig(
            "eth-short", "ETHUSDT", Mode.SHORT, Decimal("100"), Decimal("0.10"), 3, Decimal("100")
        )
        cells = build_cells(config, Decimal("0.01"))

        self.assertEqual(cells[0].buy_price, Decimal("100"))
        self.assertEqual(cells[0].sell_price, Decimal("110"))
        self.assertEqual(cells[-1].sell_price, Decimal("133.10"))
        self.assertEqual([cell.index for cell in cells], [1, 2, 3])

    def test_cell_ids_are_stable_across_rebuilds(self):
        config = StrategyConfig(
            "stable", "BTCUSDT", Mode.LONG, Decimal("100"), Decimal("0.005"), 5, Decimal("100")
        )
        first = build_cells(config, Decimal("0.1"))
        second = build_cells(config, Decimal("0.1"))
        self.assertEqual([cell.cell_id for cell in first], [cell.cell_id for cell in second])


if __name__ == "__main__":
    unittest.main()
