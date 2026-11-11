from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from grid_server.application.fixed_engine import FixedGridEngine
from grid_server.domain import CellStage, FuturesMarket, GridKind, Mode, StrategyConfig
from grid_server.domain.grid import build_cells, fixed_cell_budgets, fixed_grid_ratio
from grid_server.infrastructure.binance import BinanceFuturesExchange
from grid_server.interfaces.api import create_app
from grid_server.ports.exchange import ExchangeExecutionUnknownError
from grid_server.runtime.scheduler import StrategyScheduler
from grid_server.store import SQLiteStore

from tests.fakes import FakeExchange


class FixedEqualUGridTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.store = SQLiteStore(Path(self.tempdir.name) / "fixed.sqlite3")
        self.exchange = FakeExchange(Decimal("100"))

    def make_engine(self) -> FixedGridEngine:
        config = StrategyConfig(
            strategy_id="fixed-sol",
            symbol="SOLUSDT",
            mode=Mode.LONG,
            anchor_price=Decimal("120"),
            grid_ratio=fixed_grid_ratio(Decimal("80"), Decimal("120"), 4),
            grid_count=4,
            order_usdt=Decimal("100"),
            market_type=FuturesMarket.USDM,
            leverage=1,
            move_grid=False,
            grid_kind=GridKind.FIXED_EQUAL_USDT,
            lower_price=Decimal("80"),
            upper_price=Decimal("120"),
            grid_budget_usdt=Decimal("400"),
        )
        self.store.create_strategy(config)
        self.store.replace_cells(config.strategy_id, build_cells(config, Decimal("0.01")))
        self.store.mark_started(config.strategy_id)
        return FixedGridEngine(self.store, self.exchange, config.strategy_id)

    def test_fixed_prices_and_budget_survive_sqlite_round_trip(self) -> None:
        engine = self.make_engine()
        cells = self.store.list_cells(engine.strategy_id)
        self.assertEqual(cells[0].buy_price, Decimal("80"))
        self.assertEqual(cells[-1].sell_price, Decimal("120"))
        self.assertEqual(sum((cell.budget_usdt for cell in cells), Decimal("0")), Decimal("400"))
        self.assertTrue(all(a.sell_price == b.buy_price for a, b in zip(cells, cells[1:])))
        self.assertEqual(fixed_cell_budgets(Decimal("100"), 3), [
            Decimal("33.33333333"), Decimal("33.33333333"), Decimal("33.33333334")
        ])
        self.assertEqual(
            fixed_cell_budgets(Decimal("10000"), 100), [Decimal("100")] * 100
        )

    def test_seed_uses_one_buy_and_assigns_its_quantity_to_upper_cells(self) -> None:
        engine = self.make_engine()
        engine.tick()
        cells = self.store.list_cells(engine.strategy_id)
        market_buys = [order for order in self.exchange.placed if order["price"] == Decimal("100")]
        self.assertEqual(len(market_buys), 1)
        self.assertEqual(market_buys[0]["quantity"], Decimal("2.000"))
        self.assertEqual([cell.stage for cell in cells], [
            CellStage.PENDING_ENTRY, CellStage.PENDING_ENTRY,
            CellStage.PENDING_EXIT, CellStage.PENDING_EXIT,
        ])
        self.assertEqual([cell.open_qty for cell in cells], [
            Decimal("0"), Decimal("0"), Decimal("1"), Decimal("1")
        ])
        self.assertEqual(len([order for order in self.exchange.placed if order["side"].value == "SELL"]), 2)
        self.assertTrue(all(order["post_only"] for order in self.exchange.placed[1:]))
        engine.tick()
        self.assertEqual(self.exchange.calls["place_market_order"], 1)

    def test_topup_once_updates_all_budgets_and_resizes_both_sides(self) -> None:
        engine = self.make_engine()
        engine.tick()
        first_orders = [cell.entry_order_id or cell.exit_order_id for cell in self.store.list_cells(engine.strategy_id)]
        self.store.create_capital_action("topup-001", engine.strategy_id, "topup", Decimal("40"))
        engine.tick()
        cells = self.store.list_cells(engine.strategy_id)
        self.assertEqual([cell.budget_usdt for cell in cells], [Decimal("110")] * 4)
        self.assertEqual([cell.open_qty for cell in cells], [
            Decimal("0"), Decimal("0"), Decimal("1.1"), Decimal("1.1")
        ])
        self.assertEqual(self.store.get_strategy(engine.strategy_id).grid_budget_usdt, Decimal("440"))
        self.assertEqual(self.exchange.calls["place_market_order"], 2)
        self.assertTrue(all(self.exchange.orders[order_id].status.value == "CANCELED" for order_id in first_orders))
        self.assertEqual(self.store.create_capital_action("topup-001", engine.strategy_id, "topup", Decimal("40"))["status"], "applied")
        FixedGridEngine(self.store, self.exchange, engine.strategy_id).tick()
        self.assertEqual(self.exchange.calls["place_market_order"], 2)

    def test_recovery_queries_unknown_aggregate_order_without_duplicate_buy(self) -> None:
        engine = self.make_engine()
        original_place = self.exchange.place_market_order

        def accepted_then_lost(*args):
            original_place(*args)
            raise ExchangeExecutionUnknownError("response lost")

        self.exchange.place_market_order = accepted_then_lost
        engine.tick()
        action = self.store.get_capital_action(f"seed:{engine.strategy_id}")
        self.assertEqual(action["status"], "submitted")
        self.assertEqual(self.exchange.calls["place_market_order"], 1)
        self.exchange.place_market_order = original_place
        FixedGridEngine(self.store, self.exchange, engine.strategy_id).tick()
        self.assertEqual(self.store.get_capital_action(action["id"])["status"], "applied")
        self.assertEqual(self.exchange.calls["place_market_order"], 1)

    def test_topup_uses_current_held_cells_after_a_completed_sell(self) -> None:
        engine = self.make_engine()
        engine.tick()
        sold_cell = self.store.list_cells(engine.strategy_id)[2]
        self.exchange.fill(sold_cell.exit_order_id)
        engine.tick()
        self.store.create_capital_action("topup-after-sell", engine.strategy_id, "topup", Decimal("40"))
        engine.tick()
        action = self.store.get_capital_action("topup-after-sell")
        self.assertEqual(action["executed_qty"], "0.100")
        self.assertEqual(self.store.list_cells(engine.strategy_id)[2].stage, CellStage.PENDING_ENTRY)

    def test_topup_rejects_stale_sell_cell_preview_without_buying(self) -> None:
        engine = self.make_engine()
        engine.tick()
        app = create_app(self.store.path, lambda market=FuturesMarket.USDM: self.exchange)
        client = TestClient(app)
        preview = client.post(
            f"/strategies/{engine.strategy_id}/topups/preview",
            json={"amount_usdt": "40"},
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        preview_ids = [
            cell.cell_id for cell in self.store.list_cells(engine.strategy_id)
            if cell.open_qty > 0
        ]
        self.assertEqual(preview.json()["sell_cell_ids"], preview_ids)
        sold_cell = self.store.list_cells(engine.strategy_id)[2]
        self.exchange.fill(sold_cell.exit_order_id)
        requested = client.post(
            f"/strategies/{engine.strategy_id}/topups",
            json={
                "request_id": "stale-topup", "amount_usdt": "40",
                "expected_sell_cell_ids": preview_ids,
            },
        )
        self.assertEqual(requested.status_code, 202, requested.text)
        engine.tick()
        self.assertEqual(self.store.get_capital_action("stale-topup")["status"], "rejected")
        self.assertEqual(self.exchange.calls["place_market_order"], 1)
        self.assertEqual(self.store.get_strategy(engine.strategy_id).grid_budget_usdt, Decimal("400"))
        engine.tick()
        fresh_ids = [
            cell.cell_id for cell in self.store.list_cells(engine.strategy_id)
            if cell.open_qty > 0
        ]
        self.store.create_capital_action(
            "fresh-topup", engine.strategy_id, "topup", Decimal("40"), fresh_ids
        )
        engine.tick()
        self.assertEqual(self.store.get_capital_action("fresh-topup")["status"], "applied")

    def test_topup_below_order_minimum_is_rejected_without_blocking_grid(self) -> None:
        engine = self.make_engine()
        engine.tick()
        client = TestClient(create_app(self.store.path, lambda market=FuturesMarket.USDM: self.exchange))
        response = client.post(
            f"/strategies/{engine.strategy_id}/topups/preview",
            json={"amount_usdt": "0.01"},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.store.create_capital_action(
            "tiny-topup", engine.strategy_id, "topup", Decimal("0.01")
        )
        engine.tick()
        self.assertEqual(self.store.get_capital_action("tiny-topup")["status"], "rejected")
        self.assertEqual(self.exchange.calls["place_market_order"], 1)
        self.store.create_capital_action(
            "normal-topup", engine.strategy_id, "topup", Decimal("40")
        )
        engine.tick()
        self.assertEqual(self.store.get_capital_action("normal-topup")["status"], "applied")

    def test_production_url_cannot_initialize_fixed_engine(self) -> None:
        engine = self.make_engine()
        engine.exchange = BinanceFuturesExchange("", "", "https://fapi.binance.com")
        with self.assertRaisesRegex(ValueError, "testnet only"):
            engine.initialize()

    def test_fixed_preview_and_create_api(self) -> None:
        app = create_app(Path(self.tempdir.name) / "api.sqlite3", lambda market=FuturesMarket.USDM: self.exchange)
        client = TestClient(app)
        payload = {
            "symbol": "SOLUSDT", "lower_price": "80", "upper_price": "120",
            "grid_count": 4, "grid_budget_usdt": "400",
        }
        preview = client.post("/fixed-grids/preview", json=payload)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["allocation"]["sell_cell_count"], 2)
        created = client.post("/fixed-grids", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["grid_kind"], "fixed_equal_usdt")
        self.assertEqual(created.json()["grid_budget_usdt"], "400")

    def test_shared_scheduler_selects_fixed_engine_and_reconciles_position(self) -> None:
        engine = self.make_engine()
        scheduler = StrategyScheduler(self.store, self.exchange, pid=12345)
        self.assertEqual(scheduler.run_once(now=0), 1)
        self.assertIsInstance(scheduler.engines[engine.strategy_id], FixedGridEngine)
        self.assertEqual(self.exchange.calls["place_market_order"], 1)
        self.assertEqual(
            sum((cell.open_qty for cell in self.store.list_cells(engine.strategy_id)), Decimal("0")),
            Decimal("2"),
        )


if __name__ == "__main__":
    unittest.main()
