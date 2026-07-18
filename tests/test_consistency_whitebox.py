from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from gridtrader.domain import CellStage, Mode, OrderSide, OrderSnapshot
from gridtrader.engine import TradingEngine
from gridtrader.position_coordinator import PositionCoordinator
from gridtrader.service import GridService
from gridtrader.snapshot_exchange import SnapshotExchange
from gridtrader.store import SQLiteStore

from tests.fakes import FakeExchange


class OrderRecoveryWhiteBoxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tempdir.name) / "recovery.sqlite3")
        self.exchange = FakeExchange(Decimal("140"))
        config = GridService(self.store).create(
            "BTCUSDT",
            Mode.LONG,
            Decimal("133.10"),
            Decimal("0.10"),
            3,
            Decimal("100"),
            3,
            Decimal("0.01"),
            move_grid=False,
        )
        self.strategy_id = config.strategy_id
        self.engine = TradingEngine(
            self.store,
            self.exchange,
            self.strategy_id,
            run_id="whitebox-original",
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _place_all_entries(self) -> dict[str, int]:
        self.engine.tick()
        cells = self.store.list_cells(self.strategy_id)
        self.assertEqual(len(cells), 3)
        self.assertTrue(all(cell.entry_order_id is not None for cell in cells))
        return {cell.cell_id: cell.entry_order_id for cell in cells}

    def _erase_database_order_links(self) -> None:
        for cell in self.store.list_cells(self.strategy_id):
            cell.stage = CellStage.UNTRIGGERED
            cell.entry_order_id = None
            cell.entry_client_id = ""
            self.store.save_cell(cell)

    def test_three_platform_orders_recover_to_their_exact_database_cells(self) -> None:
        platform_ids = self._place_all_entries()
        self._erase_database_order_links()

        restarted = TradingEngine(
            SQLiteStore(self.store.path),
            self.exchange,
            self.strategy_id,
            run_id="whitebox-restart",
        )
        restarted.initialize()

        recovered = restarted.store.list_cells(self.strategy_id)
        self.assertEqual(
            {cell.cell_id: cell.entry_order_id for cell in recovered},
            platform_ids,
        )
        self.assertTrue(all(cell.stage == CellStage.PENDING_ENTRY for cell in recovered))

    def test_matching_client_id_with_wrong_order_attributes_is_quarantined(self) -> None:
        platform_ids = self._place_all_entries()
        target = self.store.list_cells(self.strategy_id)[0]
        order_id = platform_ids[target.cell_id]
        order = self.exchange.orders[order_id]
        self.exchange.orders[order_id] = OrderSnapshot(
            **{
                **order.__dict__,
                "side": OrderSide.SELL,
                "price": order.price + Decimal("1"),
            }
        )
        self._erase_database_order_links()

        restarted = TradingEngine(
            self.store,
            self.exchange,
            self.strategy_id,
            run_id="whitebox-mismatch",
        )
        restarted.initialize()

        quarantined = next(
            cell
            for cell in self.store.list_cells(self.strategy_id)
            if cell.cell_id == target.cell_id
        )
        self.assertEqual(quarantined.stage, CellStage.MANUAL_REVIEW)
        self.assertIsNone(quarantined.entry_order_id)
        events = self.store.list_events(self.strategy_id)
        mismatch = next(event for event in events if event["event_type"] == "OPEN_ORDER_MISMATCH")
        self.assertIn("side:", " ".join(mismatch["payload"]["mismatches"]))
        self.assertIn("price:", " ".join(mismatch["payload"]["mismatches"]))

    def test_platform_order_for_missing_cell_is_reported_as_orphan(self) -> None:
        platform_ids = self._place_all_entries()
        removed = self.store.list_cells(self.strategy_id)[0]
        self.store.delete_cell(self.strategy_id, removed.cell_id)

        restarted = TradingEngine(
            self.store,
            self.exchange,
            self.strategy_id,
            run_id="whitebox-orphan",
        )
        restarted.initialize()

        orphan_events = [
            event
            for event in self.store.list_events(self.strategy_id)
            if event["event_type"] == "ORPHAN_MANAGED_ORDER"
        ]
        self.assertEqual(len(orphan_events), 1)
        self.assertEqual(orphan_events[0]["payload"]["order_id"], platform_ids[removed.cell_id])


class PositionOwnershipWhiteBoxTests(unittest.TestCase):
    def _run_cross_group_shortage(self, mode: Mode) -> list[tuple[Decimal, Decimal]]:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        store = SQLiteStore(Path(tempdir.name) / f"priority-{mode.value}.sqlite3")
        exchange = FakeExchange(Decimal("100"))
        service = GridService(store)
        engines: dict[str, TradingEngine] = {}
        cells = []
        desired_entries = (
            (Decimal("70"), Decimal("77"), Decimal("118.18"), Decimal("130")),
            (Decimal("90"), Decimal("99"), Decimal("100"), Decimal("110")),
            (Decimal("99"), Decimal("108.90"), Decimal("91.81"), Decimal("101")),
        )
        for long_buy, long_sell, short_buy, short_sell in desired_entries:
            anchor = long_sell if mode == Mode.LONG else short_buy
            config = service.create(
                "BTCUSDT",
                mode,
                anchor,
                Decimal("0.10"),
                1,
                Decimal("200"),
                3,
                Decimal("0.01"),
                move_grid=False,
            )
            store.mark_started(config.strategy_id)
            engine = TradingEngine(store, exchange, config.strategy_id, run_id="priority")
            engine.initialize()
            cell = store.list_cells(config.strategy_id)[0]
            cell.stage = CellStage.PENDING_EXIT
            cell.open_qty = Decimal("2")
            cell.entry_filled_at = "2026-07-17T00:00:00+00:00"
            store.save_cell(cell)
            engines[config.strategy_id] = engine
            cells.append(cell)

        position_side = mode.value.upper()
        exchange.set_position("BTCUSDT", position_side, Decimal("3"))

        # Keep an active exit only on the cell nearest the mark. Ownership must
        # still go to farther cells first; order survival cannot change it.
        nearest = min(
            cells,
            key=lambda cell: abs(
                (cell.buy_price if mode == Mode.LONG else cell.sell_price)
                - exchange.mark
            ),
        )
        engines[nearest.strategy_id].ensure_exit(nearest)

        snapshot = SnapshotExchange(exchange)
        snapshot.begin_cycle()
        for engine in engines.values():
            engine.exchange = snapshot
        PositionCoordinator(store, snapshot, "priority-pool").reconcile(engines)

        result = []
        for cell in store.list_all_cells():
            entry_price = cell.buy_price if mode == Mode.LONG else cell.sell_price
            result.append((abs(entry_price - exchange.mark), cell.open_qty))
        return sorted(result, reverse=True)

    def test_long_and_short_allocate_across_groups_by_distance_not_active_order(self) -> None:
        for mode in (Mode.LONG, Mode.SHORT):
            with self.subTest(mode=mode.value):
                allocations = self._run_cross_group_shortage(mode)
                self.assertEqual(
                    [quantity for _distance, quantity in allocations],
                    [Decimal("2"), Decimal("1"), Decimal("0")],
                )


if __name__ == "__main__":
    unittest.main()
