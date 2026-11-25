from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from grid_server.application.grid_activity import hourly_break_even_activity
from grid_server.application.grid_break_even import project_break_even
from grid_server.application.service import GridService
from grid_server.domain import CellStage, GridCell, SymbolFilters
from grid_server.infrastructure.sqlite_store import SQLiteStore
from grid_server.runtime.scheduler import StrategyScheduler
from tests.fakes import FakeExchange


def cell(index: int, buy: str, sell: str, stage: CellStage,
         *, qty: str = "0", budget: str = "0") -> GridCell:
    return GridCell(
        strategy_id="example", cell_id=str(index), index=index,
        buy_price=Decimal(buy), sell_price=Decimal(sell), stage=stage,
        open_qty=Decimal(qty), budget_usdt=Decimal(budget),
    )


class ProjectedBreakEvenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.filters = SymbolFilters(Decimal("0.01"), Decimal("1"))

    def test_sell_on_rising_path_moves_break_even_above_platform_price(self) -> None:
        cells = [
            cell(1, "85", "90", CellStage.PENDING_EXIT, qty="1"),
            cell(2, "90", "95", CellStage.PENDING_EXIT, qty="1"),
            cell(3, "95", "100", CellStage.PENDING_EXIT, qty="1"),
            cell(4, "100", "105", CellStage.PENDING_EXIT, qty="1"),
        ]
        result = project_break_even(
            mark=Decimal("85"), platform_break_even=Decimal("92.5"),
            position_qty=Decimal("4"), cells=cells, filters=self.filters,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(Decimal(result["estimated_price"]), Decimal(280) / 3)
        self.assertEqual(result["sell_count"], 1)
        self.assertEqual(result["remaining_qty"], "3")

    def test_buys_on_falling_path_are_processed_in_price_order(self) -> None:
        cells = [
            cell(1, "95", "100", CellStage.PENDING_ENTRY, budget="95"),
            cell(2, "92", "97", CellStage.PENDING_ENTRY, budget="92"),
            cell(3, "80", "85", CellStage.UNTRIGGERED, budget="80"),
            cell(4, "60", "65", CellStage.PENDING_EXIT, qty="2"),
        ]
        result = project_break_even(
            mark=Decimal("100"), platform_break_even=Decimal("90"),
            position_qty=Decimal("2"), cells=cells, filters=self.filters,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["estimated_price"], "91.75")
        self.assertEqual(result["buy_count"], 2)
        self.assertEqual(result["remaining_qty"], "4")

    def test_unassigned_position_is_not_given_false_precision(self) -> None:
        result = project_break_even(
            mark=Decimal("85"), platform_break_even=Decimal("92.5"),
            position_qty=Decimal("5"),
            cells=[cell(1, "85", "90", CellStage.PENDING_EXIT, qty="4")],
            filters=self.filters,
        )
        self.assertEqual(result["status"], "unavailable")


class HourlyBreakEvenActivityTests(unittest.TestCase):
    def test_sample_and_completed_cycles_share_beijing_hour(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "grid.sqlite3")
            strategy_id = GridService(store, SimpleNamespace()).create_fixed(
                "SOXLUSDT", Decimal("30"), Decimal("60"), 4,
                Decimal("400"), Decimal("0.01"),
            ).strategy_id
            store.mark_started(strategy_id)
            with store.connection() as conn:
                conn.execute(
                    "UPDATE strategies SET first_started_at=? WHERE strategy_id=?",
                    ("2026-11-24T04:00:00+00:00", strategy_id),
                )
            event_id = store.append_event(strategy_id, "CYCLE_CLOSED", {})
            with store.connection() as conn:
                conn.execute(
                    "UPDATE events SET created_at=? WHERE id=?",
                    ("2026-11-24T04:30:00+00:00", event_id),
                )
            store.save_break_even_sample(strategy_id, "2026-11-24T04:00:00+00:00", {
                "status": "ok", "estimated_price": "93.333", "platform_break_even": "92.5",
                "mark_price": "85", "position_qty": "4", "buy_count": 0, "sell_count": 1,
            })
            result = hourly_break_even_activity(
                store, strategy_id, now=datetime(2026, 11, 24, 6, tzinfo=timezone.utc),
            )
            self.assertEqual(result["period_hours"], 1)
            self.assertEqual(result["periods"][0]["started_at"], "2026-11-24T12:00+08:00")
            self.assertEqual(result["periods"][0]["completed_cycles"], 1)
            self.assertEqual(result["periods"][0]["estimated_price"], "93.333")
            self.assertIsNone(result["periods"][1]["estimated_price"])

    def test_scheduler_samples_once_per_hour_without_trading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "grid.sqlite3")
            strategy = GridService(store, SimpleNamespace()).create_fixed(
                "SOXLUSDT", Decimal("80"), Decimal("110"), 4,
                Decimal("400"), Decimal("0.01"),
            )
            for cell in store.list_cells(strategy.strategy_id)[:2]:
                cell.stage = CellStage.PENDING_EXIT
                cell.open_qty = Decimal("1")
                store.save_cell(cell)
            exchange = FakeExchange(Decimal("85"))
            exchange.set_position("SOXLUSDT", "LONG", Decimal("2"))
            exchange.get_position_prices = lambda symbol, side="LONG": (
                Decimal("90"), Decimal("90"),
            )
            scheduler = StrategyScheduler(
                store, exchange, clock=lambda: 0.0,
                wall_clock=lambda: datetime(2026, 11, 24, 4, 1, tzinfo=timezone.utc).timestamp(),
            )
            scheduler._sample_break_even_if_due(strategy, Decimal("85"), 0.0)
            scheduler._sample_break_even_if_due(strategy, Decimal("85"), 1.0)
            rows = store.list_break_even_samples(
                strategy.strategy_id,
                "2026-11-24T04:00:00+00:00", "2026-11-24T05:00:00+00:00",
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["sell_count"], 1)
            self.assertEqual(exchange.calls.get("get_positions"), 1)


if __name__ == "__main__":
    unittest.main()
