from __future__ import annotations

import os
import tempfile
import time
import unittest
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from pathlib import Path
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from grid_server.api import create_app
from grid_server.binance import BinanceFuturesExchange, decimal_text
from grid_server.config import binance_base_url, binance_credentials, load_environment
from grid_server.domain import CellStage, Mode, OrderSide, OrderStatus, SymbolFilters
from grid_server.engine import TradingEngine
from grid_server.position_coordinator import PositionCoordinator
from grid_server.service import GridService
from grid_server.snapshot_exchange import SnapshotExchange
from grid_server.store import SQLiteStore

from tests.test_binance_testnet_orders import round_to_step, wait_for_status


SYMBOL = "UNIUSDT"
TARGET_NOTIONAL = Decimal("15")


@unittest.skipUnless(
    os.getenv("RUN_BINANCE_TESTNET_LLT") == "1",
    "set RUN_BINANCE_TESTNET_LLT=1 to run Binance Testnet LLT",
)
class BinanceTestnetLowLevelTests(unittest.TestCase):
    """Real Testnet coverage for API, strategy recovery, and position pools."""

    @classmethod
    def setUpClass(cls) -> None:
        load_environment(Path(os.getenv("GRID_ENV_FILE", "test.env")), override=True)
        base_url = binance_base_url()
        if (urlparse(base_url).hostname or "").lower() != "testnet.binancefuture.com":
            raise RuntimeError("refusing LLT: BINANCE_BASE_URL is not Binance Futures Testnet")
        api_key, api_secret = binance_credentials(required=True)
        cls.exchange = BinanceFuturesExchange(api_key, api_secret, base_url)
        cls.exchange.set_hedge_mode(True)
        cls.exchange.set_leverage(SYMBOL, 3)

    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.assertEqual(self.exchange.get_open_orders(SYMBOL), [], "UNIUSDT has existing open orders")
        self.assertEqual(self._position_qty("LONG"), Decimal("0"), "UNIUSDT has an existing LONG")
        self.assertEqual(self._position_qty("SHORT"), Decimal("0"), "UNIUSDT has an existing SHORT")

    def tearDown(self) -> None:
        cleanup_errors: list[str] = []
        try:
            for order in self.exchange.get_open_orders(SYMBOL):
                try:
                    self.exchange.cancel_order(SYMBOL, order.order_id)
                except Exception as exc:  # pragma: no cover - emergency cleanup path
                    cleanup_errors.append(f"cancel {order.order_id}: {exc}")
            for position_side, close_side in (("LONG", "SELL"), ("SHORT", "BUY")):
                quantity = self._position_qty(position_side)
                if quantity > 0:
                    try:
                        self._market_order(close_side, position_side, quantity, "cleanup")
                    except Exception as exc:  # pragma: no cover - emergency cleanup path
                        cleanup_errors.append(f"close {position_side} {quantity}: {exc}")
        finally:
            self.tempdir.cleanup()
        if cleanup_errors:
            self.fail("; ".join(cleanup_errors))

    def _position_qty(self, position_side: str) -> Decimal:
        for position in self.exchange.get_positions():
            if position.symbol == SYMBOL and position.position_side == position_side:
                return position.quantity
        return Decimal("0")

    def _wait_position(self, position_side: str, expected: Decimal) -> Decimal:
        actual = self._position_qty(position_side)
        for _ in range(29):
            if actual == expected:
                return actual
            time.sleep(0.1)
            actual = self._position_qty(position_side)
        return actual

    def _market_order(self, side: str, position_side: str, quantity: Decimal, role: str) -> dict:
        client_order_id = f"gtllt-{role[:8]}-{int(time.time() * 1000)}"
        if len(client_order_id) >= 36:
            raise ValueError("test client order id must be shorter than 36 characters")
        return self.exchange._request(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": SYMBOL,
                "side": side,
                "positionSide": position_side,
                "type": "MARKET",
                "quantity": decimal_text(quantity),
                "newClientOrderId": client_order_id,
                "newOrderRespType": "RESULT",
            },
            signed=True,
        )

    def _grid_inputs(
        self,
        mode: Mode = Mode.LONG,
    ) -> tuple[Decimal, Decimal, SymbolFilters, Decimal]:
        mark = self.exchange.get_mark_price(SYMBOL)
        filters = self.exchange.get_symbol_filters(SYMBOL)
        if mode == Mode.LONG:
            anchor = round_to_step(mark * Decimal("1.04"), filters.tick_size, ROUND_UP)
        else:
            anchor = round_to_step(mark * Decimal("0.96"), filters.tick_size, ROUND_DOWN)
        quantity = max(
            filters.min_qty,
            round_to_step(TARGET_NOTIONAL / mark, filters.step_size, ROUND_UP),
        )
        notional = quantity * mark
        self.assertGreaterEqual(notional, Decimal("10"))
        self.assertLessEqual(notional, Decimal("20"))
        return mark, anchor, filters, quantity

    def _make_strategy(
        self,
        mode: Mode = Mode.LONG,
        *,
        store: SQLiteStore | None = None,
        db_name: str = "strategy.sqlite3",
    ) -> tuple[SQLiteStore, TradingEngine, str, Decimal]:
        _mark, anchor, filters, quantity = self._grid_inputs(mode)
        store = store or SQLiteStore(Path(self.tempdir.name) / db_name)
        service = GridService(store)
        config = service.create(
            SYMBOL,
            mode,
            anchor,
            Decimal("0.08"),
            1,
            TARGET_NOTIONAL,
            3,
            filters.tick_size,
            poll_interval_sec=5.0,
            move_grid=False,
        )
        store.mark_started(config.strategy_id)
        engine = TradingEngine(
            store,
            self.exchange,
            config.strategy_id,
            run_id=f"testnet-llt-{mode.value}",
        )
        return store, engine, config.strategy_id, quantity

    def test_fastapi_crud_preview_cells_and_price_refresh_use_testnet(self) -> None:
        db_path = Path(self.tempdir.name) / "api.sqlite3"
        app = create_app(db_path, exchange_factory=lambda: self.exchange)
        client = TestClient(app)
        _mark, anchor, _filters, _quantity = self._grid_inputs()
        payload = {
            "symbol": SYMBOL,
            "mode": "long",
            "anchor_price": str(anchor),
            "grid_ratio": "0.08",
            "grid_count": 2,
            "order_usdt": "15",
            "leverage": 3,
            "poll_interval_sec": 5,
            "move_grid": False,
        }

        preview = client.post("/strategies/preview", json=payload)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(len(preview.json()["cells"]), 2)

        created = client.post("/strategies", json=payload)
        self.assertEqual(created.status_code, 201)
        strategy_id = created.json()["strategy_id"]
        self.assertEqual(created.json()["symbol"], SYMBOL)
        self.assertEqual(client.get(f"/strategies/{strategy_id}/cells").status_code, 200)

        payload["grid_count"] = 3
        edited = client.put(f"/strategies/{strategy_id}", json=payload)
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.json()["grid_count"], 3)

        refreshed = client.post(f"/strategies/{strategy_id}/refresh-price")
        self.assertEqual(refreshed.status_code, 200)
        self.assertGreater(Decimal(refreshed.json()["mark_price"]), 0)
        self.assertEqual(len(client.get("/strategies").json()), 1)
        self.assertEqual(client.get("/position-pools").json(), [])

    def test_long_and_short_replace_manually_canceled_entry_orders(self) -> None:
        for mode in (Mode.LONG, Mode.SHORT):
            with self.subTest(mode=mode.value):
                store, engine, strategy_id, _quantity = self._make_strategy(
                    mode,
                    db_name=f"entry-{mode.value}.sqlite3",
                )
                mark = engine.tick()
                first = store.list_cells(strategy_id)[0]
                self.assertEqual(first.stage, CellStage.PENDING_ENTRY)
                if mode == Mode.LONG:
                    self.assertLess(first.buy_price, mark)
                else:
                    self.assertGreater(first.sell_price, mark)
                first_order_id = first.entry_order_id
                self.assertIsNotNone(first_order_id)
                first_platform_order = wait_for_status(
                    self.exchange,
                    SYMBOL,
                    first_order_id,
                    OrderStatus.NEW,
                )
                self.assertEqual(first_platform_order.status, OrderStatus.NEW)
                self.assertEqual(first_platform_order.position_side, mode.value.upper())

                self.exchange.cancel_order(SYMBOL, first_order_id)
                canceled = wait_for_status(
                    self.exchange, SYMBOL, first_order_id, OrderStatus.CANCELED
                )
                self.assertEqual(canceled.status, OrderStatus.CANCELED)

                engine.tick()
                restored = store.list_cells(strategy_id)[0]
                self.assertEqual(restored.stage, CellStage.PENDING_ENTRY)
                self.assertIsNotNone(restored.entry_order_id)
                self.assertNotEqual(restored.entry_order_id, first_order_id)
                restored_platform_order = wait_for_status(
                    self.exchange,
                    SYMBOL,
                    restored.entry_order_id,
                    OrderStatus.NEW,
                )
                self.assertEqual(restored_platform_order.status, OrderStatus.NEW)
                self.assertEqual(
                    restored_platform_order.position_side,
                    mode.value.upper(),
                )
                events = [
                    event["event_type"] for event in store.list_events(strategy_id)
                ]
                self.assertIn("ENTRY_ENDED", events)
                self.assertGreaterEqual(events.count("ENTRY_PLACED"), 2)

                self.exchange.cancel_order(SYMBOL, restored.entry_order_id)
                wait_for_status(
                    self.exchange,
                    SYMBOL,
                    restored.entry_order_id,
                    OrderStatus.CANCELED,
                )

    def test_multiple_database_groups_restore_all_canceled_entry_orders(self) -> None:
        store = SQLiteStore(Path(self.tempdir.name) / "multi-group.sqlite3")
        groups = [
            self._make_strategy(Mode.LONG, store=store)[1:3],
            self._make_strategy(Mode.LONG, store=store)[1:3],
            self._make_strategy(Mode.SHORT, store=store)[1:3],
        ]
        original_ids: set[int] = set()
        for engine, strategy_id in groups:
            engine.tick()
            order_id = store.list_cells(strategy_id)[0].entry_order_id
            self.assertIsNotNone(order_id)
            original_ids.add(order_id)
        self.assertEqual(len(original_ids), 3)

        for order_id in original_ids:
            self.exchange.cancel_order(SYMBOL, order_id)
            wait_for_status(self.exchange, SYMBOL, order_id, OrderStatus.CANCELED)

        restored_ids: set[int] = set()
        restored_client_ids: set[str] = set()
        for engine, strategy_id in groups:
            engine.tick()
            cell = store.list_cells(strategy_id)[0]
            self.assertEqual(cell.stage, CellStage.PENDING_ENTRY)
            self.assertIsNotNone(cell.entry_order_id)
            self.assertNotIn(cell.entry_order_id, original_ids)
            restored_ids.add(cell.entry_order_id)
            restored_client_ids.add(cell.entry_client_id)
        self.assertEqual(len(restored_ids), 3)
        self.assertEqual(len(restored_client_ids), 3)

    def test_same_direction_groups_restore_only_the_canceled_group(self) -> None:
        store = SQLiteStore(Path(self.tempdir.name) / "same-direction-isolation.sqlite3")
        groups = [
            self._make_strategy(Mode.LONG, store=store)[1:3]
            for _ in range(3)
        ]
        original: dict[str, int] = {}
        client_ids: set[str] = set()
        for engine, strategy_id in groups:
            engine.tick()
            cell = store.list_cells(strategy_id)[0]
            self.assertIsNotNone(cell.entry_order_id)
            original[strategy_id] = cell.entry_order_id
            client_ids.add(cell.entry_client_id)
        self.assertEqual(len(set(original.values())), 3)
        self.assertEqual(len(client_ids), 3)

        target_engine, target_id = groups[1]
        canceled_id = original[target_id]
        self.exchange.cancel_order(SYMBOL, canceled_id)
        wait_for_status(self.exchange, SYMBOL, canceled_id, OrderStatus.CANCELED)
        target_engine.tick()

        restored = store.list_cells(target_id)[0]
        self.assertIsNotNone(restored.entry_order_id)
        self.assertNotEqual(restored.entry_order_id, canceled_id)
        for _engine, strategy_id in (groups[0], groups[2]):
            untouched = store.list_cells(strategy_id)[0]
            self.assertEqual(untouched.entry_order_id, original[strategy_id])

        platform_ids = {
            order.order_id for order in self.exchange.get_open_orders(SYMBOL)
        }
        expected_ids = {
            store.list_cells(strategy_id)[0].entry_order_id
            for _engine, strategy_id in groups
        }
        self.assertEqual(platform_ids, expected_ids)

    def test_open_platform_entries_are_recovered_after_database_order_id_loss(self) -> None:
        db_path = Path(self.tempdir.name) / "restart-recovery.sqlite3"
        store = SQLiteStore(db_path)
        records: list[tuple[str, int]] = []
        for mode in (Mode.LONG, Mode.SHORT):
            _store, engine, strategy_id, _quantity = self._make_strategy(
                mode,
                store=store,
            )
            engine.tick()
            cell = store.list_cells(strategy_id)[0]
            platform_order_id = cell.entry_order_id
            self.assertIsNotNone(platform_order_id)

            # Simulate a crash after Binance accepted the order but before its
            # order id/client id were durably recorded in SQLite.
            cell.stage = CellStage.UNTRIGGERED
            cell.entry_order_id = None
            cell.entry_client_id = ""
            store.save_cell(cell)
            records.append((strategy_id, platform_order_id))

        reopened = SQLiteStore(db_path)
        for strategy_id, platform_order_id in records:
            restarted = TradingEngine(
                reopened,
                self.exchange,
                strategy_id,
                run_id="testnet-db-restart",
            )
            restarted.initialize()
            recovered = reopened.list_cells(strategy_id)[0]
            self.assertEqual(recovered.stage, CellStage.PENDING_ENTRY)
            self.assertEqual(recovered.entry_order_id, platform_order_id)
            self.assertTrue(recovered.entry_client_id.startswith("wg-"))
            events = [
                event["event_type"] for event in reopened.list_events(strategy_id)
            ]
            self.assertIn("OPEN_ORDER_RECOVERED", events)

    def _make_multi_cell_entry_strategy(
        self,
        db_name: str,
        cell_count: int,
    ) -> tuple[SQLiteStore, TradingEngine, str]:
        mark = self.exchange.get_mark_price(SYMBOL)
        filters = self.exchange.get_symbol_filters(SYMBOL)
        anchor = round_to_step(
            mark * Decimal("0.98"),
            filters.tick_size,
            ROUND_DOWN,
        )
        store = SQLiteStore(Path(self.tempdir.name) / db_name)
        config = GridService(store).create(
            SYMBOL,
            Mode.LONG,
            anchor,
            Decimal("0.01"),
            cell_count,
            TARGET_NOTIONAL,
            3,
            filters.tick_size,
            poll_interval_sec=5.0,
            move_grid=False,
        )
        engine = TradingEngine(
            store,
            self.exchange,
            config.strategy_id,
            run_id="testnet-multi-cell",
        )
        return store, engine, config.strategy_id

    def test_three_platform_entries_recover_to_exact_cells_after_database_loss(self) -> None:
        store, engine, strategy_id = self._make_multi_cell_entry_strategy(
            "multi-cell-recovery.sqlite3",
            3,
        )
        engine.tick()
        before = store.list_cells(strategy_id)
        platform_ids = {cell.cell_id: cell.entry_order_id for cell in before}
        self.assertEqual(len(platform_ids), 3)
        for order_id in platform_ids.values():
            self.assertIsNotNone(order_id)
            wait_for_status(self.exchange, SYMBOL, order_id, OrderStatus.NEW)

        for cell in before:
            cell.stage = CellStage.UNTRIGGERED
            cell.entry_order_id = None
            cell.entry_client_id = ""
            store.save_cell(cell)

        reopened = SQLiteStore(store.path)
        restarted = TradingEngine(
            reopened,
            self.exchange,
            strategy_id,
            run_id="testnet-multi-cell-restart",
        )
        restarted.initialize()
        recovered = reopened.list_cells(strategy_id)
        self.assertEqual(
            {cell.cell_id: cell.entry_order_id for cell in recovered},
            platform_ids,
        )
        self.assertTrue(all(cell.stage == CellStage.PENDING_ENTRY for cell in recovered))

    def test_missing_database_cell_reports_real_platform_order_as_orphan(self) -> None:
        store, engine, strategy_id = self._make_multi_cell_entry_strategy(
            "orphan-platform-order.sqlite3",
            2,
        )
        engine.tick()
        cells = store.list_cells(strategy_id)
        removed = cells[0]
        removed_order_id = removed.entry_order_id
        self.assertIsNotNone(removed_order_id)
        wait_for_status(self.exchange, SYMBOL, removed_order_id, OrderStatus.NEW)
        store.delete_cell(strategy_id, removed.cell_id)

        restarted = TradingEngine(
            SQLiteStore(store.path),
            self.exchange,
            strategy_id,
            run_id="testnet-orphan-restart",
        )
        restarted.initialize()
        orphan_events = [
            event
            for event in restarted.store.list_events(strategy_id)
            if event["event_type"] == "ORPHAN_MANAGED_ORDER"
        ]
        self.assertEqual(len(orphan_events), 1)
        self.assertEqual(orphan_events[0]["payload"]["order_id"], removed_order_id)

    def test_real_order_with_matching_client_id_but_wrong_price_is_quarantined(self) -> None:
        store, engine, strategy_id, _quantity = self._make_strategy(
            Mode.LONG,
            db_name="platform-mismatch.sqlite3",
        )
        engine.initialize()
        cell = store.list_cells(strategy_id)[0]
        filters = self.exchange.get_symbol_filters(SYMBOL)
        wrong_price = round_to_step(
            cell.buy_price - filters.tick_size,
            filters.tick_size,
            ROUND_DOWN,
        )
        expected_qty = engine._quantity(cell.buy_price)
        platform_id = self.exchange.place_limit_order(
            SYMBOL,
            OrderSide.BUY,
            "LONG",
            expected_qty,
            wrong_price,
            engine._client_id(cell, "e"),
        )
        wait_for_status(self.exchange, SYMBOL, platform_id, OrderStatus.NEW)

        restarted = TradingEngine(
            SQLiteStore(store.path),
            self.exchange,
            strategy_id,
            run_id="testnet-platform-mismatch",
        )
        restarted.initialize()
        quarantined = restarted.store.list_cells(strategy_id)[0]
        self.assertEqual(quarantined.stage, CellStage.MANUAL_REVIEW)
        self.assertIsNone(quarantined.entry_order_id)
        mismatch_events = [
            event
            for event in restarted.store.list_events(strategy_id)
            if event["event_type"] == "OPEN_ORDER_MISMATCH"
        ]
        self.assertEqual(len(mismatch_events), 1)
        self.assertIn(
            "price:",
            " ".join(mismatch_events[0]["payload"]["mismatches"]),
        )
        self.assertEqual(len(self.exchange.get_open_orders(SYMBOL)), 1)

    def _exercise_real_position_pool(self, mode: Mode) -> None:
        store, engine, strategy_id, quantity = self._make_strategy(
            mode,
            db_name=f"position-{mode.value}.sqlite3",
        )
        engine.initialize()
        cell = store.list_cells(strategy_id)[0]
        position_side = mode.value.upper()
        open_side = "BUY" if mode == Mode.LONG else "SELL"
        close_side = "SELL" if mode == Mode.LONG else "BUY"

        self._market_order(open_side, position_side, quantity, f"open-{mode.value}")
        self.assertEqual(self._wait_position(position_side, quantity), quantity)

        cell.stage = CellStage.PENDING_EXIT
        cell.open_qty = quantity
        cell.entry_filled_at = "2026-07-17T00:00:00+00:00"
        store.save_cell(cell)
        engine.ensure_exit(cell)
        pending_exit = store.list_cells(strategy_id)[0]
        original_exit_id = pending_exit.exit_order_id
        self.assertIsNotNone(original_exit_id)

        # Simulate SQLite losing the platform exit id during a crash. A fresh
        # engine must recover the still-open Binance order by deterministic
        # clientOrderId before any cancellation/reallocation work begins.
        pending_exit.stage = CellStage.MANUAL_REVIEW
        pending_exit.exit_order_id = None
        pending_exit.exit_client_id = ""
        store.save_cell(pending_exit)
        store = SQLiteStore(store.path)
        engine = TradingEngine(
            store,
            self.exchange,
            strategy_id,
            run_id=f"testnet-exit-restart-{mode.value}",
        )
        engine.initialize()
        pending_exit = store.list_cells(strategy_id)[0]
        self.assertEqual(pending_exit.stage, CellStage.PENDING_EXIT)
        self.assertEqual(pending_exit.exit_order_id, original_exit_id)

        self.exchange.cancel_order(SYMBOL, original_exit_id)
        wait_for_status(self.exchange, SYMBOL, original_exit_id, OrderStatus.CANCELED)
        engine.sync_cell(pending_exit)
        self.assertEqual(store.list_cells(strategy_id)[0].stage, CellStage.MANUAL_REVIEW)

        snapshot = SnapshotExchange(self.exchange)
        snapshot.begin_cycle()
        # Production schedulers give engines and the position coordinator the
        # same SnapshotExchange so repaired orders update the shared cycle cache.
        engine.exchange = snapshot
        coordinator = PositionCoordinator(store, snapshot, "testnet-pool")
        coordinator.reconcile({strategy_id: engine})
        restored = store.list_cells(strategy_id)[0]
        self.assertEqual(restored.stage, CellStage.PENDING_EXIT)
        self.assertIsNotNone(restored.exit_order_id)
        self.assertNotEqual(restored.exit_order_id, original_exit_id)
        pool = next(
            item
            for item in store.list_position_pools()
            if item["symbol"] == SYMBOL and item["position_side"] == position_side
        )
        self.assertEqual(pool["position_side"], position_side)
        self.assertEqual(Decimal(pool["actual_qty"]), quantity)
        self.assertEqual(Decimal(pool["logical_qty"]), quantity)
        self.assertEqual(pool["status"], "consistent")

        partial_close = max(
            self.exchange.get_symbol_filters(SYMBOL).step_size,
            round_to_step(quantity / Decimal("2"), self.exchange.get_symbol_filters(SYMBOL).step_size, ROUND_DOWN),
        )
        self.assertLess(partial_close, quantity)
        self._market_order(
            close_side,
            position_side,
            partial_close,
            f"partial-close-{mode.value}",
        )
        remaining = quantity - partial_close
        self.assertEqual(self._wait_position(position_side, remaining), remaining)

        snapshot.begin_cycle()
        coordinator.reconcile({strategy_id: engine})
        resized = store.list_cells(strategy_id)[0]
        self.assertEqual(resized.open_qty, remaining)
        resized_order = self.exchange.get_order(SYMBOL, resized.exit_order_id)
        self.assertEqual(resized_order.original_qty, remaining)
        resized_pool = next(
            item
            for item in store.list_position_pools()
            if item["symbol"] == SYMBOL and item["position_side"] == position_side
        )
        self.assertEqual(resized_pool["status"], "consistent")

        self.exchange.cancel_order(SYMBOL, resized.exit_order_id)
        wait_for_status(self.exchange, SYMBOL, resized.exit_order_id, OrderStatus.CANCELED)
        engine.sync_cell(resized)
        self._market_order(
            close_side,
            position_side,
            remaining,
            f"close-{mode.value}",
        )
        self.assertEqual(
            self._wait_position(position_side, Decimal("0")),
            Decimal("0"),
        )

        snapshot.begin_cycle()
        coordinator.reconcile({strategy_id: engine})
        released = store.list_cells(strategy_id)[0]
        self.assertEqual(released.open_qty, Decimal("0"))
        self.assertEqual(released.stage, CellStage.UNTRIGGERED)
        final_pool = next(
            item
            for item in store.list_position_pools()
            if item["symbol"] == SYMBOL and item["position_side"] == position_side
        )
        self.assertEqual(Decimal(final_pool["actual_qty"]), Decimal("0"))
        self.assertEqual(Decimal(final_pool["logical_qty"]), Decimal("0"))

    def test_long_position_pool_restores_resizes_and_releases_exit(self) -> None:
        self._exercise_real_position_pool(Mode.LONG)

    def test_short_position_pool_restores_resizes_and_releases_exit(self) -> None:
        self._exercise_real_position_pool(Mode.SHORT)

    def test_multi_group_position_shortage_penetrates_by_price_distance(self) -> None:
        mark = self.exchange.get_mark_price(SYMBOL)
        filters = self.exchange.get_symbol_filters(SYMBOL)
        store = SQLiteStore(Path(self.tempdir.name) / "platform-penetration.sqlite3")
        service = GridService(store)
        engines: dict[str, TradingEngine] = {}

        group_specs = (
            (Decimal("1.035"), 2),
            (Decimal("1.025"), 1),
        )
        for multiplier, cell_count in group_specs:
            anchor = round_to_step(mark * multiplier, filters.tick_size, ROUND_UP)
            config = service.create(
                SYMBOL,
                Mode.LONG,
                anchor,
                Decimal("0.015"),
                cell_count,
                Decimal("8"),
                3,
                filters.tick_size,
                poll_interval_sec=5.0,
                move_grid=False,
            )
            store.mark_started(config.strategy_id)
            engine = TradingEngine(
                store,
                self.exchange,
                config.strategy_id,
                run_id="testnet-penetration",
            )
            engine.initialize()
            engines[config.strategy_id] = engine

        cells = store.list_all_cells()
        self.assertEqual(len(cells), 3)
        per_cell = Decimal("2")
        total = per_cell * len(cells)
        self._market_order("BUY", "LONG", total, "penetration-open")
        self.assertEqual(self._wait_position("LONG", total), total)

        for sequence, cell in enumerate(cells):
            cell.stage = CellStage.PENDING_EXIT
            cell.open_qty = per_cell
            cell.entry_filled_at = f"2026-07-17T00:00:0{sequence}+00:00"
            store.save_cell(cell)
            engines[cell.strategy_id].ensure_exit(cell)

        cells = store.list_all_cells()
        by_distance = sorted(
            cells,
            key=lambda cell: abs(cell.buy_price - mark),
            reverse=True,
        )
        farthest, middle, nearest = by_distance

        # Create the exact conflict that the old implementation mishandled:
        # the farthest Cell loses its exit while the nearest exit survives.
        self.exchange.cancel_order(SYMBOL, farthest.exit_order_id)
        wait_for_status(
            self.exchange,
            SYMBOL,
            farthest.exit_order_id,
            OrderStatus.CANCELED,
        )
        engines[farthest.strategy_id].sync_cell(farthest)

        self._market_order("SELL", "LONG", Decimal("3"), "penetration-close")
        self.assertEqual(self._wait_position("LONG", Decimal("3")), Decimal("3"))

        snapshot = SnapshotExchange(self.exchange)
        snapshot.begin_cycle()
        for engine in engines.values():
            engine.exchange = snapshot
        PositionCoordinator(store, snapshot, "testnet-penetration-pool").reconcile(
            engines
        )

        updated = {
            (cell.strategy_id, cell.cell_id): cell for cell in store.list_all_cells()
        }
        far_after = updated[(farthest.strategy_id, farthest.cell_id)]
        middle_after = updated[(middle.strategy_id, middle.cell_id)]
        near_after = updated[(nearest.strategy_id, nearest.cell_id)]
        self.assertEqual(far_after.open_qty, Decimal("2"))
        self.assertEqual(middle_after.open_qty, Decimal("1"))
        self.assertEqual(near_after.open_qty, Decimal("0"))
        self.assertEqual(near_after.stage, CellStage.UNTRIGGERED)

        open_exits = [
            order
            for order in self.exchange.get_open_orders(SYMBOL)
            if order.position_side == "LONG" and order.side == OrderSide.SELL
        ]
        self.assertEqual(
            sum(
                (order.original_qty - order.executed_qty for order in open_exits),
                Decimal("0"),
            ),
            Decimal("3"),
        )
        pool = next(
            item
            for item in store.list_position_pools()
            if item["symbol"] == SYMBOL and item["position_side"] == "LONG"
        )
        self.assertEqual(Decimal(pool["actual_qty"]), Decimal("3"))
        self.assertEqual(Decimal(pool["logical_qty"]), Decimal("3"))
        self.assertEqual(pool["status"], "consistent")

        # Delete every surviving exit, remove the remaining real position, and
        # verify that zero resources penetrate through both groups/all Cells.
        for cell in store.list_all_cells():
            if cell.exit_order_id is None:
                continue
            snapshot.cancel_order(SYMBOL, cell.exit_order_id)
            wait_for_status(
                self.exchange,
                SYMBOL,
                cell.exit_order_id,
                OrderStatus.CANCELED,
            )
            engines[cell.strategy_id].sync_cell(cell)
        self._market_order("SELL", "LONG", Decimal("3"), "penetration-zero")
        self.assertEqual(self._wait_position("LONG", Decimal("0")), Decimal("0"))
        snapshot.begin_cycle()
        PositionCoordinator(store, snapshot, "testnet-penetration-zero").reconcile(
            engines
        )
        self.assertTrue(
            all(
                cell.open_qty == 0 and cell.stage == CellStage.UNTRIGGERED
                for cell in store.list_all_cells()
            )
        )

    def test_external_close_order_reserves_then_returns_real_exit_capacity(self) -> None:
        store, engine, strategy_id, quantity = self._make_strategy(
            Mode.LONG,
            db_name="external-reservation.sqlite3",
        )
        engine.initialize()
        cell = store.list_cells(strategy_id)[0]
        self._market_order("BUY", "LONG", quantity, "external-open")
        self.assertEqual(self._wait_position("LONG", quantity), quantity)

        cell.stage = CellStage.PENDING_EXIT
        cell.open_qty = quantity
        cell.entry_filled_at = "2026-07-17T00:00:00+00:00"
        store.save_cell(cell)

        filters = self.exchange.get_symbol_filters(SYMBOL)
        mark = self.exchange.get_mark_price(SYMBOL)
        external_qty = max(filters.min_qty, Decimal("2"))
        self.assertLess(external_qty, quantity)
        external_price = round_to_step(
            mark * Decimal("1.045"),
            filters.tick_size,
            ROUND_UP,
        )
        external_id = self.exchange.place_limit_order(
            SYMBOL,
            OrderSide.SELL,
            "LONG",
            external_qty,
            external_price,
            f"external-{int(time.time() * 1000)}",
        )
        wait_for_status(self.exchange, SYMBOL, external_id, OrderStatus.NEW)

        snapshot = SnapshotExchange(self.exchange)
        snapshot.begin_cycle()
        engine.exchange = snapshot
        coordinator = PositionCoordinator(store, snapshot, "testnet-external")
        coordinator.reconcile({strategy_id: engine})

        reserved = store.list_cells(strategy_id)[0]
        self.assertEqual(reserved.open_qty, quantity)
        reserved_exit = wait_for_status(
            self.exchange,
            SYMBOL,
            reserved.exit_order_id,
            OrderStatus.NEW,
        )
        self.assertEqual(reserved_exit.original_qty, quantity - external_qty)
        pool = next(
            item
            for item in store.list_position_pools()
            if item["symbol"] == SYMBOL and item["position_side"] == "LONG"
        )
        self.assertEqual(Decimal(pool["external_reserved_qty"]), external_qty)
        self.assertEqual(pool["status"], "consistent")

        self.exchange.cancel_order(SYMBOL, external_id)
        wait_for_status(self.exchange, SYMBOL, external_id, OrderStatus.CANCELED)
        snapshot.begin_cycle()
        coordinator.reconcile({strategy_id: engine})

        expanded = store.list_cells(strategy_id)[0]
        expanded_exit = wait_for_status(
            self.exchange,
            SYMBOL,
            expanded.exit_order_id,
            OrderStatus.NEW,
        )
        self.assertEqual(expanded.open_qty, quantity)
        self.assertEqual(expanded_exit.original_qty, quantity)
        expanded_pool = next(
            item
            for item in store.list_position_pools()
            if item["symbol"] == SYMBOL and item["position_side"] == "LONG"
        )
        self.assertEqual(
            Decimal(expanded_pool["external_reserved_qty"]),
            Decimal("0"),
        )
        self.assertEqual(expanded_pool["status"], "consistent")

    def test_platform_positions_without_database_cells_are_reported_unassigned(self) -> None:
        _mark, _anchor, _filters, quantity = self._grid_inputs()
        self._market_order("BUY", "LONG", quantity, "unassigned-long")
        self._market_order("SELL", "SHORT", quantity, "unassigned-short")
        self.assertEqual(self._wait_position("LONG", quantity), quantity)
        self.assertEqual(self._wait_position("SHORT", quantity), quantity)

        store = SQLiteStore(Path(self.tempdir.name) / "unassigned.sqlite3")
        snapshot = SnapshotExchange(self.exchange)
        snapshot.begin_cycle()
        PositionCoordinator(store, snapshot, "testnet-unassigned").reconcile({})

        pools = {
            item["position_side"]: item for item in store.list_position_pools()
            if item["symbol"] == SYMBOL
        }
        self.assertEqual(set(pools), {"LONG", "SHORT"})
        for position_side in ("LONG", "SHORT"):
            pool = pools[position_side]
            self.assertEqual(Decimal(pool["actual_qty"]), quantity)
            self.assertEqual(Decimal(pool["logical_qty"]), Decimal("0"))
            self.assertEqual(Decimal(pool["unassigned_qty"]), quantity)
            self.assertEqual(pool["status"], "unassigned")


if __name__ == "__main__":
    unittest.main()
