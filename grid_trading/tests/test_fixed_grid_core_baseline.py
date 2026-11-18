"""Release-baseline decisions and legacy imports for the fixed grid core."""

from __future__ import annotations

import unittest
from decimal import Decimal

from grid_rule.fixed_equal_usdt import (
    FixedEntryMode,
    FixedGridSpec,
    build_fixed_cells,
    initial_sell_allocations,
    triggered_entry_ids,
)
from grid_rule.fixed_window import WindowCell, WindowInput, plan_fixed_window
from trading_strategies.grid_core import fixed_equal_usdt, fixed_window


class FixedGridCoreBaselineTests(unittest.TestCase):
    def test_legacy_imports_are_the_same_objects(self) -> None:
        self.assertIs(FixedGridSpec, fixed_equal_usdt.FixedGridSpec)
        self.assertIs(build_fixed_cells, fixed_equal_usdt.build_fixed_cells)
        self.assertIs(FixedEntryMode, fixed_equal_usdt.FixedEntryMode)
        self.assertIs(plan_fixed_window, fixed_window.plan_fixed_window)

    def test_release_geometry_trigger_and_window(self) -> None:
        spec = FixedGridSpec(
            "snapshot-soxl", Decimal("40"), Decimal("60"), 4,
            Decimal("200"), Decimal("0.01"),
        )
        cells = build_fixed_cells(spec)
        self.assertEqual(
            tuple((cell.cell_id, cell.buy_price, cell.sell_price) for cell in cells),
            (
                ("7c12ebfea83b0c37", Decimal("40"), Decimal("44.26")),
                ("aaf9ef3607e18eb3", Decimal("44.26"), Decimal("48.98")),
                ("6ee01b69cfc56ef5", Decimal("48.98"), Decimal("54.21")),
                ("9b36abc5564e7a20", Decimal("54.21"), Decimal("60")),
            ),
        )
        self.assertEqual(
            triggered_entry_ids(cells, Decimal("49.70")),
            frozenset(cell.cell_id for cell in cells[:3]),
        )

        allocations = initial_sell_allocations(
            cells, Decimal("49.70"), step_size=Decimal("0.01"),
            min_qty=Decimal("0.01"), min_notional=Decimal("5"),
        )
        self.assertEqual(allocations, {
            cells[2].cell_id: Decimal("1.02"),
            cells[3].cell_id: Decimal("0.92"),
        })
        window_cells = tuple(WindowCell(
            cell_id=cell.cell_id, index=cell.index,
            buy_price=cell.buy_price, sell_price=cell.sell_price,
            stage="pending_exit" if cell.cell_id in allocations else "pending_entry",
            open_qty=allocations.get(cell.cell_id, Decimal("0")),
            has_entry_order=False, has_exit_order=False,
            entry_ready=True, exit_ready=True,
        ) for cell in cells)
        seeded = plan_fixed_window(WindowInput(
            cells=window_cells, mark=Decimal("49.70"),
            entry_mode=FixedEntryMode.SEEDED,
        ))
        self.assertEqual(seeded.entry_ids, frozenset(cell.cell_id for cell in cells[:2]))
        self.assertEqual(seeded.exit_ids, frozenset(cell.cell_id for cell in cells[2:]))

        triggered = plan_fixed_window(WindowInput(
            cells=tuple(WindowCell(
                cell_id=cell.cell_id, index=cell.index,
                buy_price=cell.buy_price, sell_price=cell.sell_price,
                stage="pending_entry", open_qty=Decimal("0"),
                has_entry_order=False, has_exit_order=False,
                entry_ready=cell in cells[:3], exit_ready=True,
            ) for cell in cells),
            mark=Decimal("49.70"),
            entry_mode=FixedEntryMode.PRICE_TRIGGERED,
        ))
        self.assertEqual(triggered.entry_ids, frozenset(cell.cell_id for cell in cells[:3]))
        self.assertFalse(triggered.exit_ids)


if __name__ == "__main__":
    unittest.main()
