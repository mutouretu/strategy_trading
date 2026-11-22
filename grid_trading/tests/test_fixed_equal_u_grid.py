from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from grid_server.application.fixed_engine import FixedGridEngine
from grid_server.application.service import GridService
from grid_server.domain import (
    CellStage, FuturesMarket, GridKind, Mode, OrderSide, OrderSnapshot,
    OrderStatus, StrategyConfig,
)
from grid_server.domain.grid import (
    build_cells, fixed_cell_budgets, fixed_grid_count_for_ratio,
    fixed_grid_ratio, round_down,
)
from grid_server.infrastructure.binance import BinanceFuturesExchange
from grid_server.interfaces.api import create_app
from grid_server.ports.exchange import ExchangeExecutionUnknownError
from grid_server.shared.config import fixed_grid_endpoint_allowed
from grid_server.runtime.scheduler import StrategyScheduler
from grid_server.store import SQLiteStore

from tests.fakes import FakeExchange


class FixedEqualUGridTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.store = SQLiteStore(Path(self.tempdir.name) / "fixed.sqlite3")
        self.exchange = FakeExchange(Decimal("100"))

    def test_count_from_target_ratio_fits_range_without_exceeding_gap(self) -> None:
        lower, upper, target = Decimal("15"), Decimal("90"), Decimal("0.005")
        count = fixed_grid_count_for_ratio(lower, upper, target)
        self.assertEqual(count, 360)
        self.assertLessEqual(fixed_grid_ratio(lower, upper, count), target)
        self.assertGreater(fixed_grid_ratio(lower, upper, count - 1), target)
        self.assertEqual(sum(fixed_cell_budgets(Decimal("50000"), count)), Decimal("50000"))

    def test_count_from_target_ratio_rejects_invalid_inputs(self) -> None:
        with self.assertRaises(ValueError):
            fixed_grid_count_for_ratio(Decimal("90"), Decimal("15"), Decimal("0.005"))
        with self.assertRaises(ValueError):
            fixed_grid_count_for_ratio(Decimal("15"), Decimal("90"), Decimal("0"))

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
        expected = [
            round_down(cell.budget_usdt / cell.buy_price, self.exchange.filters.step_size)
            for cell in cells[2:]
        ]
        market_buys = [order for order in self.exchange.placed if order["price"] == Decimal("100")]
        self.assertEqual(len(market_buys), 1)
        self.assertEqual(market_buys[0]["quantity"], sum(expected))
        self.assertEqual([cell.stage for cell in cells], [
            CellStage.PENDING_ENTRY, CellStage.PENDING_ENTRY,
            CellStage.PENDING_EXIT, CellStage.PENDING_EXIT,
        ])
        self.assertEqual([cell.open_qty for cell in cells], [
            Decimal("0"), Decimal("0"), *expected
        ])
        self.assertNotEqual(expected[0], expected[1])
        for cell in cells[2:]:
            target = cell.budget_usdt * (Decimal("1") + engine._config().grid_ratio)
            self.assertLessEqual(target - cell.open_qty * cell.sell_price,
                                 self.exchange.filters.step_size * cell.sell_price)
        self.assertEqual(len([order for order in self.exchange.placed if order["side"].value == "SELL"]), 2)
        self.assertTrue(all(order["post_only"] for order in self.exchange.placed[1:]))
        engine.tick()
        self.assertEqual(self.exchange.calls["place_market_order"], 1)

    def test_auto_reinvests_one_quantity_step_after_sell_without_extra_market_buy(self) -> None:
        engine = self.make_engine()
        engine.tick()
        cell = self.store.list_cells(engine.strategy_id)[0]
        entry_order = self.exchange.orders[cell.entry_order_id]
        self.exchange.fill(entry_order.order_id, cell.buy_price)
        engine.tick()
        cell = next(item for item in self.store.list_cells(engine.strategy_id) if item.cell_id == cell.cell_id)
        exit_order = self.exchange.orders[cell.exit_order_id]
        self.exchange.fill(exit_order.order_id, cell.sell_price)
        seed = self.store.get_capital_action(f"seed:{engine.strategy_id}")
        def fill(trade_id, order_id, side, qty, price):
            return {
                "id": trade_id, "orderId": order_id, "side": side,
                "qty": str(qty), "price": str(price), "realizedPnl": "0",
                "commission": "0", "commissionAsset": "USDT", "time": 1794480000000 + trade_id,
            }
        self.store.save_strategy_trades(engine.strategy_id, [
            fill(1, seed["order_id"], "BUY", seed["executed_qty"], self.exchange.mark),
            fill(2, entry_order.order_id, "BUY", entry_order.original_qty, cell.buy_price),
            fill(3, exit_order.order_id, "SELL", exit_order.original_qty, cell.sell_price),
        ])
        self.exchange.get_user_trades = lambda symbol, **kwargs: []
        self.store.set_auto_reinvest_enabled(engine.strategy_id, True)
        old_budget = cell.budget_usdt
        before_total = self.store.get_strategy(engine.strategy_id).grid_budget_usdt
        market_buys = self.exchange.calls["place_market_order"]

        engine.tick()

        updated = next(item for item in self.store.list_cells(engine.strategy_id) if item.cell_id == cell.cell_id)
        step = self.exchange.filters.step_size
        amount = cell.buy_price * step
        self.assertEqual(updated.budget_usdt, old_budget + amount)
        self.assertEqual(self.store.get_strategy(engine.strategy_id).grid_budget_usdt, before_total + amount)
        self.assertEqual(self.exchange.orders[updated.entry_order_id].original_qty,
                         entry_order.original_qty + step)
        self.assertEqual(self.exchange.calls["place_market_order"], market_buys)
        entries = self.store.list_cell_reinvestments(engine.strategy_id)
        self.assertEqual(len(entries), 1)
        self.assertEqual(Decimal(entries[0]["amount_usdt"]), amount)
        engine.tick()
        self.assertEqual(len(self.store.list_cell_reinvestments(engine.strategy_id)), 1)

    def test_auto_reinvest_toggle_is_persisted_and_exposes_audit_history(self) -> None:
        engine = self.make_engine()
        client = TestClient(create_app(self.store.path, lambda market=FuturesMarket.USDM: self.exchange))

        enabled = client.post(
            f"/strategies/{engine.strategy_id}/auto-reinvest", json={"enabled": True}
        )

        self.assertEqual(enabled.status_code, 200, enabled.text)
        self.assertTrue(self.store.get_strategy(engine.strategy_id).auto_reinvest_enabled)
        self.assertTrue(client.get("/strategies").json()[0]["auto_reinvest_enabled"])
        self.assertEqual(client.get(f"/strategies/{engine.strategy_id}/reinvestments").json(), [])

    def test_delayed_trade_fees_keep_percent_from_cycle_close(self) -> None:
        engine = self.make_engine()
        engine.tick()
        cell = self.store.list_cells(engine.strategy_id)[0]
        entry_order = self.exchange.orders[cell.entry_order_id]
        self.exchange.fill(entry_order.order_id, cell.buy_price)
        engine.tick()
        cell = next(c for c in self.store.list_cells(engine.strategy_id) if c.cell_id == cell.cell_id)
        exit_order = self.exchange.orders[cell.exit_order_id]
        self.store.set_auto_reinvest_config(
            engine.strategy_id, enabled=False, auto_reinvest_percent=Decimal("50")
        )
        self.exchange.fill(exit_order.order_id, cell.sell_price)
        engine.tick()  # Closing event is persisted, but exchange fees are not synced yet.
        close = next(e for e in self.store.list_events(engine.strategy_id)
                     if e["event_type"] == "CYCLE_CLOSED" and e["cell_id"] == cell.cell_id)
        self.assertEqual(close["payload"]["auto_reinvest_percent"], "50")
        self.assertEqual(self.store.list_cell_reinvest_earnings(engine.strategy_id), [])

        self.store.set_auto_reinvest_config(
            engine.strategy_id, enabled=True, auto_reinvest_percent=Decimal("100")
        )
        seed = self.store.get_capital_action(f"seed:{engine.strategy_id}")
        def fill(trade_id, order_id, side, qty, price, fee):
            return {
                "id": trade_id, "orderId": order_id, "side": side,
                "qty": str(qty), "price": str(price), "realizedPnl": "0",
                "commission": str(fee), "commissionAsset": "USDT",
                "time": 1794480000000 + trade_id,
            }
        first_sell_qty = exit_order.original_qty / 2
        self.store.save_strategy_trades(engine.strategy_id, [
            fill(1, seed["order_id"], "BUY", seed["executed_qty"], self.exchange.mark, Decimal("0")),
            fill(2, entry_order.order_id, "BUY", entry_order.original_qty, cell.buy_price, Decimal("0.03")),
            fill(3, exit_order.order_id, "SELL", first_sell_qty, cell.sell_price, Decimal("0.02")),
        ])
        self.exchange.get_user_trades = lambda symbol, **kwargs: []
        engine.tick()
        self.assertEqual(self.store.list_cell_reinvest_earnings(engine.strategy_id), [])
        self.store.save_strategy_trades(engine.strategy_id, [
            fill(4, exit_order.order_id, "SELL", exit_order.original_qty - first_sell_qty,
                 cell.sell_price, Decimal("0.02")),
        ])
        engine.tick()

        earnings = self.store.list_cell_reinvest_earnings(engine.strategy_id)
        self.assertEqual(len(earnings), 1)
        gross = entry_order.original_qty * (cell.sell_price - cell.buy_price)
        expected_net = gross - Decimal("0.07")
        self.assertEqual(Decimal(earnings[0]["net_profit_usdt"]), expected_net)
        self.assertEqual(Decimal(earnings[0]["eligible_profit_usdt"]), expected_net / 2)
        self.assertEqual(earnings[0]["auto_reinvest_percent"], "50")

    def test_partial_replaced_exit_waits_for_all_fee_fills(self) -> None:
        engine = self.make_engine()
        engine.initialize()
        cell = self.store.list_cells(engine.strategy_id)[0]
        cell.cycle_count = 1
        self.store.save_cell(cell)
        for kind, payload in (
            ("ENTRY_FILLED", {"order_id": 901, "qty": "1"}),
            ("EXIT_PARTIALLY_FILLED", {"order_id": 902, "executed_total": "0.4"}),
            ("EXIT_ENDED", {"order_id": 902, "executed_qty": "0.4", "action": "manual_review"}),
        ):
            self.store.append_event(engine.strategy_id, kind, payload, cell.cell_id)
        self.store.append_cycle_close_event(
            engine.strategy_id, cell.cell_id, "CYCLE_CLOSED",
            {"order_id": 903, "qty": "0.6"}, 1, None,
        )

        def trade(trade_id, order_id, side, qty, price, fee):
            return {
                "id": trade_id, "orderId": order_id, "side": side,
                "qty": str(qty), "price": str(price), "realizedPnl": "0",
                "commission": str(fee), "commissionAsset": "USDT",
                "time": 1794480000000 + trade_id,
            }
        self.store.save_strategy_trades(engine.strategy_id, [
            trade(1, 901, "BUY", "1", cell.buy_price, "0.01"),
            trade(3, 903, "SELL", "0.6", cell.sell_price, "0.02"),
        ])
        self.exchange.get_user_trades = lambda symbol, **kwargs: []
        engine._reinvest_closed_cells()
        self.assertEqual(self.store.list_cell_reinvest_earnings(engine.strategy_id), [])

        self.store.save_strategy_trades(engine.strategy_id, [
            trade(2, 902, "SELL", "0.4", cell.sell_price, "0.03"),
        ])
        engine._reinvest_closed_cells()
        earnings = self.store.list_cell_reinvest_earnings(engine.strategy_id)
        self.assertEqual(len(earnings), 1)
        self.assertEqual(Decimal(earnings[0]["net_profit_usdt"]),
                         cell.sell_price - cell.buy_price - Decimal("0.06"))

    def test_reinvestment_cannot_spend_same_cycle_profit_twice(self) -> None:
        engine = self.make_engine()
        self.store.ensure_reinvest_cutover(engine.strategy_id)
        cell = self.store.list_cells(engine.strategy_id)[0]
        cell.cycle_count = 1
        self.store.save_cell(cell)
        self.store.set_auto_reinvest_enabled(engine.strategy_id, True)
        unit = cell.buy_price * self.exchange.filters.step_size
        original_budget = cell.budget_usdt

        self.assertFalse(self.store.apply_cell_reinvestment(
            engine.strategy_id, cell.cell_id, 1, self.exchange.filters.step_size, unit - Decimal("0.00001")
        ))
        self.assertTrue(self.store.book_cell_reinvest_earnings(
            engine.strategy_id, cell.cell_id, 1, unit * 3, Decimal("100")
        ))
        self.assertTrue(self.store.apply_cell_reinvestment(
            engine.strategy_id, cell.cell_id, 1, self.exchange.filters.step_size, unit * 3
        ))
        self.assertFalse(self.store.apply_cell_reinvestment(
            engine.strategy_id, cell.cell_id, 1, self.exchange.filters.step_size, unit * 3
        ))
        updated = next(item for item in self.store.list_cells(engine.strategy_id) if item.cell_id == cell.cell_id)
        self.assertEqual(updated.budget_usdt, original_budget + unit)
        self.assertEqual(len(self.store.list_cell_reinvestments(engine.strategy_id)), 1)

    def test_reinvest_percent_is_locked_per_cycle_and_kept_per_cell(self) -> None:
        engine = self.make_engine()
        self.store.ensure_reinvest_cutover(engine.strategy_id)
        cell = self.store.list_cells(engine.strategy_id)[0]
        other = self.store.list_cells(engine.strategy_id)[1]
        unit = cell.buy_price * self.exchange.filters.step_size
        self.store.set_auto_reinvest_config(
            engine.strategy_id, enabled=True, auto_reinvest_percent=Decimal("100")
        )

        def close(cycle: int, net: Decimal, percent: Decimal) -> bool:
            cell.cycle_count = cycle
            self.store.save_cell(cell)
            self.assertTrue(self.store.book_cell_reinvest_earnings(
                engine.strategy_id, cell.cell_id, cycle, net, percent
            ))
            return self.store.apply_cell_reinvestment(
                engine.strategy_id, cell.cell_id, cycle,
                self.exchange.filters.step_size, net,
            )

        self.assertFalse(close(1, unit * Decimal("0.75"), Decimal("100")))
        self.store.set_auto_reinvest_config(
            engine.strategy_id, enabled=True, auto_reinvest_percent=Decimal("50")
        )
        self.assertTrue(close(2, unit, Decimal("50")))
        self.store.set_auto_reinvest_config(
            engine.strategy_id, enabled=True, auto_reinvest_percent=Decimal("100")
        )
        self.assertTrue(close(3, unit, Decimal("100")))
        self.assertEqual(len(self.store.list_cell_reinvestments(engine.strategy_id)), 2)
        earnings = self.store.list_cell_reinvest_earnings(engine.strategy_id)
        self.assertEqual(
            [Decimal(item["eligible_profit_usdt"]) for item in earnings],
            [unit * Decimal("0.75"), unit * Decimal("0.5"), unit],
        )
        other.cycle_count = 1
        self.store.save_cell(other)
        self.assertTrue(self.store.book_cell_reinvest_earnings(
            engine.strategy_id, other.cell_id, 1, Decimal("0"), Decimal("100")
        ))
        self.assertFalse(self.store.apply_cell_reinvestment(
            engine.strategy_id, other.cell_id, 1,
            self.exchange.filters.step_size, unit,
        ))

    def test_zero_percent_and_negative_cycle_do_not_unlock_old_profit(self) -> None:
        engine = self.make_engine()
        self.store.ensure_reinvest_cutover(engine.strategy_id)
        cell = self.store.list_cells(engine.strategy_id)[0]
        unit = cell.buy_price * self.exchange.filters.step_size
        self.store.set_auto_reinvest_enabled(engine.strategy_id, True)
        for cycle, net, percent in (
            (1, unit * 10, Decimal("0")),
            (2, unit * 2, Decimal("100")),
            (3, -unit * 2, Decimal("100")),
            (4, unit, Decimal("100")),
        ):
            cell.cycle_count = cycle
            self.store.save_cell(cell)
            self.assertTrue(self.store.book_cell_reinvest_earnings(
                engine.strategy_id, cell.cell_id, cycle, net, percent
            ))
            spent = self.store.apply_cell_reinvestment(
                engine.strategy_id, cell.cell_id, cycle,
                self.exchange.filters.step_size, net,
            )
            self.assertEqual(spent, cycle == 2)
        self.assertEqual(len(self.store.list_cell_reinvestments(engine.strategy_id)), 1)

    def test_zero_percent_loss_can_exhaust_earlier_earmarked_profit(self) -> None:
        engine = self.make_engine()
        self.store.ensure_reinvest_cutover(engine.strategy_id)
        cell = self.store.list_cells(engine.strategy_id)[0]
        unit = cell.buy_price * self.exchange.filters.step_size
        for cycle, net, percent in (
            (1, unit * 2, Decimal("50")),
            (2, -unit * 2, Decimal("0")),
        ):
            cell.cycle_count = cycle
            self.store.save_cell(cell)
            self.assertTrue(self.store.book_cell_reinvest_earnings(
                engine.strategy_id, cell.cell_id, cycle, net, percent
            ))
        self.store.set_auto_reinvest_enabled(engine.strategy_id, True)
        self.assertFalse(self.store.apply_cell_reinvestment(
            engine.strategy_id, cell.cell_id, 2,
            self.exchange.filters.step_size, Decimal("0"),
        ))

    def test_reinvest_cutover_excludes_old_cycles_and_old_spends(self) -> None:
        engine = self.make_engine()
        cell = self.store.list_cells(engine.strategy_id)[0]
        unit = cell.buy_price * self.exchange.filters.step_size
        cell.cycle_count = 3
        self.store.save_cell(cell)
        with self.store.connection() as conn:
            conn.execute(
                """
                INSERT INTO cell_reinvestments(
                    strategy_id, cell_id, cycle_count, quantity_step,
                    amount_usdt, profit_snapshot_usdt, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (engine.strategy_id, cell.cell_id, 2,
                 str(self.exchange.filters.step_size), str(unit), str(unit),
                 "2026-11-01T00:00:00+00:00"),
            )
        self.store.ensure_reinvest_cutover(engine.strategy_id)
        self.assertEqual(self.store.reinvest_cutover_cycles(engine.strategy_id)[cell.cell_id], 3)
        self.assertFalse(self.store.book_cell_reinvest_earnings(
            engine.strategy_id, cell.cell_id, 3, unit * 100, Decimal("100")
        ))
        cell.cycle_count = 4
        self.store.save_cell(cell)
        self.store.set_auto_reinvest_enabled(engine.strategy_id, True)
        self.assertTrue(self.store.book_cell_reinvest_earnings(
            engine.strategy_id, cell.cell_id, 4, unit, Decimal("100")
        ))
        self.assertTrue(self.store.apply_cell_reinvestment(
            engine.strategy_id, cell.cell_id, 4,
            self.exchange.filters.step_size, unit,
        ))
        self.assertEqual(len(self.store.list_cell_reinvestments(engine.strategy_id)), 2)

    def test_reinvest_percent_api_validates_and_preserves_value_on_old_toggle(self) -> None:
        engine = self.make_engine()
        client = TestClient(create_app(self.store.path, lambda market=FuturesMarket.USDM: self.exchange))
        path = f"/strategies/{engine.strategy_id}/auto-reinvest"
        updated = client.post(path, json={"enabled": True, "auto_reinvest_percent": "37.5"})
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["auto_reinvest_percent"], "37.5")
        self.assertEqual(client.get("/strategies").json()[0]["auto_reinvest_percent"], "37.5")
        self.assertEqual(client.post(path, json={"enabled": False}).json()["auto_reinvest_percent"], "37.5")
        for invalid in ("101", "-1", "NaN"):
            self.assertEqual(client.post(
                path, json={"enabled": True, "auto_reinvest_percent": invalid}
            ).status_code, 422)

    def test_fifty_usdt_cells_seed_variable_qty_for_equal_sell_notional(self) -> None:
        self.exchange.mark = Decimal("62.54")
        self.exchange.filters = replace(
            self.exchange.filters,
            step_size=Decimal("0.01"), min_qty=Decimal("0.01"),
            min_notional=Decimal("5"), contract_type="TRADIFI_PERPETUAL",
            percent_price_up=Decimal("1.03"), percent_price_down=Decimal("0.97"),
        )
        service = GridService(self.store, SimpleNamespace())
        config = service.create_fixed(
            "SOXLUSDT", Decimal("51.22"), Decimal("76.34"), 80,
            Decimal("4000"), Decimal("0.01"),
        )
        self.store.mark_started(config.strategy_id)
        engine = FixedGridEngine(self.store, self.exchange, config.strategy_id)
        engine.tick()
        cells = self.store.list_cells(config.strategy_id)
        seeded = [cell for cell in cells if cell.sell_price > self.exchange.mark]
        target_sell = Decimal("50") * (Decimal("1") + config.grid_ratio)
        self.assertEqual(self.exchange.calls["place_market_order"], 1)
        self.assertGreater(len({cell.open_qty for cell in seeded}), 1)
        self.assertTrue(all(
            cell.open_qty == round_down(Decimal("50") / cell.buy_price, Decimal("0.01"))
            for cell in seeded
        ))
        self.assertTrue(all(
            abs(cell.open_qty * cell.sell_price - target_sell) < Decimal("0.80")
            for cell in seeded
        ))
        action = self.store.get_capital_action(f"seed:{config.strategy_id}")
        self.assertIsNotNone(action["planned_allocations_json"])
        self.assertEqual(
            Decimal(action["executed_qty"]),
            sum((cell.open_qty for cell in seeded), Decimal("0")),
        )

    def test_tradifi_price_band_allows_far_passive_orders(self) -> None:
        self.exchange.filters = replace(
            self.exchange.filters,
            contract_type="TRADIFI_PERPETUAL",
            min_notional=Decimal("5"),
            percent_price_up=Decimal("1.03"),
            percent_price_down=Decimal("0.97"),
        )
        engine = self.make_engine()
        engine.tick()
        cells = self.store.list_cells(engine.strategy_id)
        self.assertEqual(sum(cell.entry_order_id is not None for cell in cells), 2)
        self.assertEqual(sum(cell.exit_order_id is not None for cell in cells), 2)
        self.assertEqual(self.exchange.calls["place_market_order"], 1)

        client = TestClient(create_app(self.store.path, lambda market=FuturesMarket.USDM: self.exchange))
        preview = client.post(
            "/fixed-grids/preview",
            json={
                "symbol": "SOLUSDT", "lower_price": "80", "upper_price": "120",
                "grid_count": 4, "grid_budget_usdt": "400",
            },
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["allocation"]["deferred_buy_cell_count"], 0)
        self.assertEqual(preview.json()["allocation"]["deferred_sell_cell_count"], 0)

    def test_crossed_sell_line_never_sends_unprotected_market_order(self) -> None:
        engine = self.make_engine()
        engine.initialize()
        self.exchange.mark = Decimal("63.26")
        order_id = engine._submit_limit_order(
            "SOLUSDT", OrderSide.SELL, "LONG", Decimal("0.79"),
            Decimal("63.13"), "catchup-sell",
        )
        self.assertEqual(self.exchange.calls.get("place_market_order", 0), 0)
        self.assertEqual(self.exchange.orders[order_id].price, Decimal("63.13"))
        self.assertFalse(self.exchange.placed[-1]["post_only"])

    def test_recovered_historical_market_catchup_order_allows_zero_limit_price(self) -> None:
        engine = self.make_engine()
        engine.initialize()
        cell = self.store.list_cells(engine.strategy_id)[0]
        order = OrderSnapshot(
            order_id=987,
            client_order_id="catchup-987",
            status=OrderStatus.FILLED,
            side=OrderSide.BUY,
            price=Decimal("0"),
            original_qty=Decimal("1.250"),
            executed_qty=Decimal("1.250"),
            position_side="LONG",
        )
        self.assertEqual(engine._recovery_mismatches(cell, "entry", order), [])

    def test_topup_once_updates_all_budgets_and_resizes_both_sides(self) -> None:
        engine = self.make_engine()
        engine.tick()
        first_orders = [cell.entry_order_id or cell.exit_order_id for cell in self.store.list_cells(engine.strategy_id)]
        self.store.create_capital_action("topup-001", engine.strategy_id, "topup", Decimal("40"))
        engine.tick()
        cells = self.store.list_cells(engine.strategy_id)
        self.assertEqual([cell.budget_usdt for cell in cells], [Decimal("110")] * 4)
        self.assertEqual([cell.open_qty for cell in cells], [
            Decimal("0"), Decimal("0"),
            *[round_down(Decimal("110") / cell.buy_price, self.exchange.filters.step_size)
              for cell in cells[2:]],
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
        paired_qty = sold_cell.open_qty
        self.exchange.fill(sold_cell.exit_order_id)
        engine.tick()
        reentry = self.store.list_cells(engine.strategy_id)[2]
        self.assertEqual(self.exchange.orders[reentry.entry_order_id].original_qty, paired_qty)
        self.store.create_capital_action("topup-after-sell", engine.strategy_id, "topup", Decimal("40"))
        engine.tick()
        action = self.store.get_capital_action("topup-after-sell")
        remaining = self.store.list_cells(engine.strategy_id)[3]
        expected_increment = (
            round_down(Decimal("110") / remaining.buy_price, self.exchange.filters.step_size)
            - round_down(Decimal("100") / remaining.buy_price, self.exchange.filters.step_size)
        )
        self.assertEqual(Decimal(action["executed_qty"]), expected_increment)
        self.assertEqual(self.store.list_cells(engine.strategy_id)[2].stage, CellStage.PENDING_ENTRY)

    def test_topup_does_not_rebuy_quantity_already_sold_from_a_cell(self) -> None:
        engine = self.make_engine()
        engine.tick()
        cells = self.store.list_cells(engine.strategy_id)
        first_sell = cells[2]
        self.exchange.partial_fill(first_sell.exit_order_id, Decimal("0.200"))
        engine.tick()
        self.store.create_capital_action("topup-after-partial", engine.strategy_id, "topup", Decimal("40"))
        engine.tick()
        action = self.store.get_capital_action("topup-after-partial")
        expected_delta = sum(
            (
                round_down(Decimal("110") / cell.buy_price, self.exchange.filters.step_size)
                - round_down(Decimal("100") / cell.buy_price, self.exchange.filters.step_size)
                for cell in cells[2:]
            ),
            Decimal("0"),
        )
        self.assertEqual(Decimal(action["executed_qty"]), expected_delta)
        updated = self.store.list_cells(engine.strategy_id)
        self.assertEqual(updated[2].open_qty, cells[2].open_qty - Decimal("0.200") +
                         round_down(Decimal("110") / cells[2].buy_price, self.exchange.filters.step_size) -
                         round_down(Decimal("100") / cells[2].buy_price, self.exchange.filters.step_size))

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
        with self.assertRaisesRegex(ValueError, "approved Binance endpoint"):
            engine.initialize()

    def test_production_endpoint_requires_explicit_opt_in(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(fixed_grid_endpoint_allowed("https://fapi.binance.com"))
            self.assertTrue(fixed_grid_endpoint_allowed("https://demo-fapi.binance.com"))
        with patch.dict("os.environ", {"GRID_ENABLE_BINANCE_LIVE_FIXED_GRID": "1"}):
            self.assertTrue(fixed_grid_endpoint_allowed("https://fapi.binance.com"))
            self.assertFalse(fixed_grid_endpoint_allowed("https://example.com"))
            self.assertFalse(fixed_grid_endpoint_allowed("http://fapi.binance.com"))
            self.assertFalse(fixed_grid_endpoint_allowed("https://fapi.binance.com:8080"))

    def test_live_runtime_directory_uses_its_own_scheduler_pid(self) -> None:
        runtime_dir = Path(self.tempdir.name) / "live"
        with patch.dict("os.environ", {"GRID_RUNTIME_DIR": str(runtime_dir)}):
            app = create_app(
                Path(self.tempdir.name) / "live.sqlite3",
                lambda market=FuturesMarket.USDM: self.exchange,
            )
        self.assertEqual(app.state.service.supervisor.pid_path, (runtime_dir / "scheduler.pid").resolve())

    def test_323_cell_preview_is_accepted(self) -> None:
        self.exchange.mark = Decimal("60.23")
        self.exchange.filters = replace(
            self.exchange.filters,
            step_size=Decimal("0.01"), min_qty=Decimal("0.01"),
            min_notional=Decimal("5"), contract_type="TRADIFI_PERPETUAL",
            percent_price_up=Decimal("1.03"), percent_price_down=Decimal("0.97"),
        )
        client = TestClient(create_app(Path(self.tempdir.name) / "large.sqlite3", lambda market=FuturesMarket.USDM: self.exchange))
        response = client.post("/fixed-grids/preview", json={
            "symbol": "SOXLUSDT", "lower_price": "30", "upper_price": "150",
            "grid_count": 323, "grid_budget_usdt": "5000",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["cells"]), 323)

    def test_restart_refills_ten_per_side_without_coordinator_overplacing(self) -> None:
        self.exchange.mark = Decimal("60.23")
        self.exchange.filters = replace(
            self.exchange.filters,
            step_size=Decimal("0.01"), min_qty=Decimal("0.01"),
            min_notional=Decimal("5"), contract_type="TRADIFI_PERPETUAL",
            percent_price_up=Decimal("1.03"), percent_price_down=Decimal("0.97"),
        )
        service = GridService(self.store, SimpleNamespace())
        config = service.create_fixed(
            "SOXLUSDT", Decimal("30"), Decimal("150"), 323,
            Decimal("5000"), Decimal("0.01"),
        )
        self.store.mark_started(config.strategy_id)
        scheduler = StrategyScheduler(self.store, self.exchange, pid=12345)
        scheduler.run_once(now=0)
        cells = self.store.list_cells(config.strategy_id)
        self.assertEqual(sum(cell.stage == CellStage.PENDING_ENTRY and cell.entry_order_id is not None for cell in cells), 10)
        self.assertEqual(sum(cell.stage == CellStage.PENDING_EXIT and cell.exit_order_id is not None for cell in cells), 10)
        self.assertNotEqual(self.store.get_position_pool("SOXLUSDT", "LONG", FuturesMarket.USDM)["status"], "manual_review")

        for role in ("entry", "exit"):
            selected = [
                cell for cell in cells
                if (cell.entry_order_id if role == "entry" else cell.exit_order_id) is not None
            ][:4]
            for cell in selected:
                order_id = cell.entry_order_id if role == "entry" else cell.exit_order_id
                self.exchange.cancel_order(config.symbol, order_id)
                if role == "entry":
                    cell.entry_order_id = None
                    cell.entry_client_id = ""
                else:
                    cell.exit_order_id = None
                    cell.exit_client_id = ""
                self.store.save_cell(cell)

        restarted = FixedGridEngine(self.store, self.exchange, config.strategy_id)
        restarted.tick()
        cells = self.store.list_cells(config.strategy_id)
        self.assertEqual(sum(cell.stage == CellStage.PENDING_ENTRY and cell.entry_order_id is not None for cell in cells), 10)
        self.assertEqual(sum(cell.stage == CellStage.PENDING_EXIT and cell.exit_order_id is not None for cell in cells), 10)

    def test_external_amended_then_canceled_exits_reenter_window_after_audit(self) -> None:
        self.exchange.mark = Decimal("60.23")
        self.exchange.filters = replace(
            self.exchange.filters,
            step_size=Decimal("0.01"), min_qty=Decimal("0.01"),
            min_notional=Decimal("5"), contract_type="TRADIFI_PERPETUAL",
        )
        service = GridService(self.store, SimpleNamespace())
        config = service.create_fixed(
            "SOXLUSDT", Decimal("30"), Decimal("150"), 323,
            Decimal("5000"), Decimal("0.01"),
        )
        self.store.mark_started(config.strategy_id)
        scheduler = StrategyScheduler(
            self.store, self.exchange, pid=12345, reconcile_interval_sec=5,
        )
        scheduler.run_once(now=0)
        exits = sorted(
            (
                cell for cell in self.store.list_cells(config.strategy_id)
                if cell.exit_order_id is not None
            ),
            key=lambda cell: cell.sell_price,
        )
        self.assertEqual(len(exits), 10)
        # Model a stale window with three farther resting sells. Their orders
        # must yield to the nearer cells after the position audit clears them.
        far_cells = sorted(
            (
                cell for cell in self.store.list_cells(config.strategy_id)
                if cell.stage == CellStage.PENDING_EXIT and cell.exit_order_id is None
            ),
            key=lambda cell: cell.sell_price,
        )[:3]
        self.assertEqual(len(far_cells), 3)
        engine = scheduler.engines[config.strategy_id]
        for cell in far_cells:
            engine.ensure_exit(cell)
        far_order_ids = {
            self.store.get_cell(config.strategy_id, cell.cell_id).exit_order_id
            for cell in far_cells
        }
        canceled = exits[:3]
        previous = {
            cell.cell_id: (cell.exit_order_id, cell.open_qty, cell.cycle_count)
            for cell in canceled
        }
        for cell in canceled:
            # OKX chart dragging amends the live order without changing its ID.
            self.exchange.orders[cell.exit_order_id] = replace(
                self.exchange.orders[cell.exit_order_id],
                price=cell.sell_price - self.exchange.filters.tick_size,
            )
            self.exchange.cancel_order(config.symbol, cell.exit_order_id)

        # First pass observes the cancellation; the position audit confirms
        # that the held quantity still belongs to each cell before it can sell.
        scheduler.run_once(now=6)
        for cell in canceled:
            audited = self.store.get_cell(config.strategy_id, cell.cell_id)
            self.assertEqual(audited.open_qty, previous[cell.cell_id][1])
            self.assertEqual(audited.cycle_count, previous[cell.cell_id][2])
            self.assertEqual(audited.stage, CellStage.PENDING_EXIT)

        scheduler.run_once(now=12)
        cells = self.store.list_cells(config.strategy_id)
        self.assertEqual(sum(cell.exit_order_id is not None for cell in cells), 10)
        self.assertTrue(all(
            self.exchange.orders[order_id].status == OrderStatus.CANCELED
            for order_id in far_order_ids
        ))
        for cell in canceled:
            restored = self.store.get_cell(config.strategy_id, cell.cell_id)
            old_id, original_qty, cycle_count = previous[cell.cell_id]
            self.assertIsNotNone(restored.exit_order_id)
            self.assertNotEqual(restored.exit_order_id, old_id)
            self.assertEqual(restored.open_qty, original_qty)
            self.assertEqual(restored.cycle_count, cycle_count)
            self.assertEqual(
                self.exchange.orders[restored.exit_order_id].price,
                restored.sell_price,
            )
        scheduler.run_once(now=18)
        active_ids = {
            cell.exit_order_id
            for cell in self.store.list_cells(config.strategy_id)
            if cell.exit_order_id is not None
        }
        self.assertEqual(len(active_ids), 10)
        self.assertEqual({
            order.order_id for order in self.exchange.get_open_orders(config.symbol)
            if order.side == OrderSide.SELL
        }, active_ids)

    def test_external_canceled_exits_refill_even_with_seven_remaining(self) -> None:
        self.exchange.mark = Decimal("60.23")
        self.exchange.filters = replace(
            self.exchange.filters,
            step_size=Decimal("0.01"), min_qty=Decimal("0.01"),
            min_notional=Decimal("5"), contract_type="TRADIFI_PERPETUAL",
        )
        config = GridService(self.store, SimpleNamespace()).create_fixed(
            "SOXLUSDT", Decimal("30"), Decimal("150"), 323,
            Decimal("5000"), Decimal("0.01"),
        )
        self.store.mark_started(config.strategy_id)
        scheduler = StrategyScheduler(
            self.store, self.exchange, pid=12345, reconcile_interval_sec=5,
        )
        scheduler.run_once(now=0)
        exits = sorted(
            (cell for cell in self.store.list_cells(config.strategy_id)
             if cell.exit_order_id is not None),
            key=lambda cell: cell.sell_price,
        )
        self.assertEqual(len(exits), 10)
        canceled = exits[:3]
        old_ids = {cell.cell_id: cell.exit_order_id for cell in canceled}
        original_qty = {cell.cell_id: cell.open_qty for cell in canceled}
        for cell in canceled:
            self.exchange.orders[cell.exit_order_id] = replace(
                self.exchange.orders[cell.exit_order_id],
                price=cell.sell_price - self.exchange.filters.tick_size,
            )
            self.exchange.cancel_order(config.symbol, cell.exit_order_id)

        scheduler.run_once(now=6)
        audited = self.store.list_cells(config.strategy_id)
        self.assertEqual(sum(cell.exit_order_id is not None for cell in audited), 7)
        for cell in canceled:
            current = self.store.get_cell(config.strategy_id, cell.cell_id)
            self.assertEqual(current.stage, CellStage.PENDING_EXIT)
            self.assertEqual(current.open_qty, original_qty[cell.cell_id])
            self.assertIsNone(current.exit_order_id)

        # These three were in the managed window before external cancellation.
        # The five-order threshold only governs ordinary trading depletion.
        scheduler.run_once(now=12)
        cells = self.store.list_cells(config.strategy_id)
        self.assertEqual(sum(cell.exit_order_id is not None for cell in cells), 10)
        for cell in canceled:
            restored = self.store.get_cell(config.strategy_id, cell.cell_id)
            self.assertIsNotNone(restored.exit_order_id)
            self.assertNotEqual(restored.exit_order_id, old_ids[cell.cell_id])
            self.assertEqual(restored.open_qty, original_qty[cell.cell_id])
            self.assertEqual(restored.cycle_count, cell.cycle_count)
            self.assertEqual(
                self.exchange.orders[restored.exit_order_id].price,
                restored.sell_price,
            )
        scheduler.run_once(now=18)
        self.assertEqual(sum(
            cell.exit_order_id is not None
            for cell in self.store.list_cells(config.strategy_id)
        ), 10)

    def test_external_exit_repair_waits_for_unreserved_consistent_pool(self) -> None:
        self.exchange.mark = Decimal("60.23")
        self.exchange.filters = replace(
            self.exchange.filters,
            step_size=Decimal("0.01"), min_qty=Decimal("0.01"),
            min_notional=Decimal("5"), contract_type="TRADIFI_PERPETUAL",
        )
        config = GridService(self.store, SimpleNamespace()).create_fixed(
            "SOXLUSDT", Decimal("30"), Decimal("150"), 323,
            Decimal("5000"), Decimal("0.01"),
        )
        self.store.mark_started(config.strategy_id)
        engine = FixedGridEngine(self.store, self.exchange, config.strategy_id)
        engine.tick()
        canceled = sorted(
            (cell for cell in self.store.list_cells(config.strategy_id)
             if cell.exit_order_id is not None),
            key=lambda cell: cell.sell_price,
        )[:3]
        self.assertEqual(len(canceled), 3)
        for cell in canceled:
            self.exchange.cancel_order(config.symbol, cell.exit_order_id)
        engine.tick()
        for cell in canceled:
            reviewed = self.store.get_cell(config.strategy_id, cell.cell_id)
            self.assertEqual(reviewed.stage, CellStage.MANUAL_REVIEW)
            # Represent a cell released for possible repair while the pool
            # audit is still stale or another close order reserves position.
            reviewed.stage = CellStage.PENDING_EXIT
            self.store.save_cell(reviewed)

        logical_qty = sum(
            (cell.open_qty for cell in self.store.list_cells(config.strategy_id)),
            Decimal("0"),
        )

        def save_pool(status: str, reserved: Decimal) -> None:
            self.store.save_position_pool(
                config.symbol, "LONG", logical_qty, logical_qty, reserved,
                Decimal("0"), Decimal("0"), status, config.market_type,
            )

        def assert_seven_unrepaired() -> None:
            cells = self.store.list_cells(config.strategy_id)
            self.assertEqual(sum(cell.exit_order_id is not None for cell in cells), 7)
            self.assertTrue(all(
                self.store.get_cell(config.strategy_id, cell.cell_id).exit_order_id is None
                for cell in canceled
            ))

        save_pool("manual_review", Decimal("0"))
        engine.tick()
        assert_seven_unrepaired()

        save_pool("consistent", Decimal("0.01"))
        engine.tick()
        assert_seven_unrepaired()

        save_pool("consistent", Decimal("0"))
        engine.tick()
        self.assertEqual(sum(
            cell.exit_order_id is not None
            for cell in self.store.list_cells(config.strategy_id)
        ), 10)

    def test_patrol_closed_sell_rebuys_after_mark_crosses_buy_line(self) -> None:
        self.exchange.mark = Decimal("61.50")
        self.exchange.filters = replace(
            self.exchange.filters,
            step_size=Decimal("0.01"), min_qty=Decimal("0.01"),
            min_notional=Decimal("5"), contract_type="TRADIFI_PERPETUAL",
        )
        config = GridService(self.store, SimpleNamespace()).create_fixed(
            "SOXLUSDT", Decimal("59"), Decimal("64"), 16,
            Decimal("800"), Decimal("0.01"), poll_interval_sec=60,
        )
        self.store.mark_started(config.strategy_id)
        scheduler = StrategyScheduler(
            self.store, self.exchange, pid=12345, reconcile_interval_sec=5,
        )
        scheduler.run_once(now=0)
        cells = self.store.list_cells(config.strategy_id)
        active_buys = [cell for cell in cells if cell.entry_order_id is not None]
        active_sells = [cell for cell in cells if cell.exit_order_id is not None]
        self.assertTrue(5 <= len(active_buys) < 10)
        self.assertTrue(6 <= len(active_sells) <= 10)
        sold = max(active_sells, key=lambda cell: cell.sell_price)
        old_sell_id = sold.exit_order_id
        self.exchange.fill(old_sell_id, sold.sell_price)
        self.exchange.mark = sold.sell_price + Decimal("0.01")

        # Only the position patrol runs here. It records the completed cycle
        # but does not have a window in which to place the paired buy.
        self.assertEqual(scheduler.run_once(now=6), 0)
        after_patrol = self.store.get_cell(config.strategy_id, sold.cell_id)
        self.assertEqual(after_patrol.stage, CellStage.UNTRIGGERED)
        self.assertIsNone(after_patrol.entry_order_id)
        self.assertEqual(after_patrol.cycle_count, sold.cycle_count + 1)

        self.exchange.mark = sold.buy_price - Decimal("0.01")
        self.assertLess(self.exchange.mark, sold.buy_price)
        scheduler.run_once(now=60)
        reopened = self.store.get_cell(config.strategy_id, sold.cell_id)
        self.assertEqual(reopened.stage, CellStage.PENDING_ENTRY)
        self.assertIsNotNone(reopened.entry_order_id)
        self.assertEqual(
            self.exchange.orders[reopened.entry_order_id].price,
            reopened.buy_price,
        )
        self.assertEqual(reopened.cycle_count, sold.cycle_count + 1)

    def test_crossed_paired_buy_preempts_ten_closer_resting_buys(self) -> None:
        self.exchange.mark = Decimal("60.23")
        self.exchange.filters = replace(
            self.exchange.filters,
            step_size=Decimal("0.01"), min_qty=Decimal("0.01"),
            min_notional=Decimal("5"), contract_type="TRADIFI_PERPETUAL",
        )
        config = GridService(self.store, SimpleNamespace()).create_fixed(
            "SOXLUSDT", Decimal("30"), Decimal("150"), 323,
            Decimal("5000"), Decimal("0.01"), poll_interval_sec=60,
        )
        self.store.mark_started(config.strategy_id)
        scheduler = StrategyScheduler(
            self.store, self.exchange, pid=12345, reconcile_interval_sec=5,
        )
        scheduler.run_once(now=0)
        cells = self.store.list_cells(config.strategy_id)
        active_buys = [cell for cell in cells if cell.entry_order_id is not None]
        active_sells = [cell for cell in cells if cell.exit_order_id is not None]
        self.assertEqual(len(active_buys), 10)
        self.assertEqual(len(active_sells), 10)
        sold = max(active_sells, key=lambda cell: cell.sell_price)
        self.assertGreater(
            sold.buy_price, max(cell.buy_price for cell in active_buys),
        )
        self.exchange.fill(sold.exit_order_id, sold.sell_price)
        self.exchange.mark = sold.sell_price + Decimal("0.01")
        self.assertEqual(scheduler.run_once(now=6), 0)
        after_patrol = self.store.get_cell(config.strategy_id, sold.cell_id)
        self.assertEqual(after_patrol.stage, CellStage.UNTRIGGERED)
        self.assertEqual(after_patrol.cycle_count, sold.cycle_count + 1)

        self.exchange.mark = min(cell.buy_price for cell in active_buys)
        self.assertLess(self.exchange.mark, sold.buy_price)
        self.assertTrue(all(
            abs(cell.buy_price - self.exchange.mark)
            < abs(sold.buy_price - self.exchange.mark)
            for cell in active_buys
        ))
        scheduler.run_once(now=60)
        reopened = self.store.get_cell(config.strategy_id, sold.cell_id)
        self.assertEqual(reopened.stage, CellStage.PENDING_ENTRY)
        self.assertIsNotNone(reopened.entry_order_id)
        self.assertLessEqual(
            self.exchange.orders[reopened.entry_order_id].price,
            reopened.buy_price,
        )
        self.assertEqual(reopened.cycle_count, sold.cycle_count + 1)
        active_ids = {
            cell.entry_order_id
            for cell in self.store.list_cells(config.strategy_id)
            if cell.entry_order_id is not None
        }
        self.assertLessEqual(len(active_ids), 10)
        self.assertIn(reopened.entry_order_id, active_ids)
        self.assertTrue(any(
            self.exchange.orders[cell.entry_order_id].status == OrderStatus.CANCELED
            for cell in active_buys
        ))
        self.assertEqual({
            order.order_id for order in self.exchange.get_open_orders(config.symbol)
            if order.side == OrderSide.BUY
        }, active_ids)

    def test_rebalance_cancels_old_partial_exit_and_keeps_logical_position(self) -> None:
        self.exchange.mark = Decimal("60.23")
        self.exchange.filters = replace(
            self.exchange.filters,
            step_size=Decimal("0.01"), min_qty=Decimal("0.01"),
            min_notional=Decimal("5"), contract_type="TRADIFI_PERPETUAL",
            percent_price_up=Decimal("1.03"), percent_price_down=Decimal("0.97"),
        )
        service = GridService(self.store, SimpleNamespace())
        config = service.create_fixed(
            "SOXLUSDT", Decimal("30"), Decimal("150"), 323,
            Decimal("5000"), Decimal("0.01"),
        )
        self.store.mark_started(config.strategy_id)
        engine = FixedGridEngine(self.store, self.exchange, config.strategy_id)
        engine.tick()
        cells = self.store.list_cells(config.strategy_id)
        buy = next(cell for cell in cells if cell.entry_order_id is not None)
        sell = next(cell for cell in cells if cell.exit_order_id is not None)
        buy_order_id = buy.entry_order_id
        sell_order_id = sell.exit_order_id
        self.exchange.partial_fill(sell_order_id, Decimal("0.01"))
        engine.tick()
        sell_before = next(cell for cell in self.store.list_cells(config.strategy_id) if cell.cell_id == sell.cell_id)

        for other in [
            cell for cell in self.store.list_cells(config.strategy_id)
            if cell.entry_order_id is not None and cell.cell_id != buy.cell_id
        ][:6]:
            self.exchange.cancel_order(config.symbol, other.entry_order_id)
            other.entry_order_id = None
            other.entry_client_id = ""
            self.store.save_cell(other)

        self.exchange.mark = Decimal("70")
        engine.tick()
        self.assertEqual(self.exchange.orders[buy_order_id].status, OrderStatus.NEW)
        sell_after = next(cell for cell in self.store.list_cells(config.strategy_id) if cell.cell_id == sell.cell_id)
        self.assertEqual(self.exchange.orders[sell_order_id].status, OrderStatus.CANCELED)
        self.assertIsNone(sell_after.exit_order_id)
        self.assertEqual(sell_after.stage, CellStage.PENDING_EXIT)
        self.assertEqual(sell_after.open_qty, sell_before.open_qty)

    def test_order_window_waits_until_one_side_has_fewer_than_five(self) -> None:
        self.exchange.mark = Decimal("60.23")
        self.exchange.filters = replace(
            self.exchange.filters,
            step_size=Decimal("0.01"), min_qty=Decimal("0.01"),
            min_notional=Decimal("5"), contract_type="TRADIFI_PERPETUAL",
        )
        service = GridService(self.store, SimpleNamespace())
        config = service.create_fixed(
            "SOXLUSDT", Decimal("30"), Decimal("150"), 323,
            Decimal("5000"), Decimal("0.01"),
        )
        self.store.mark_started(config.strategy_id)
        engine = FixedGridEngine(self.store, self.exchange, config.strategy_id)
        engine.tick()
        cells = self.store.list_cells(config.strategy_id)
        self.assertEqual(sum(cell.entry_order_id is not None for cell in cells), 10)
        self.assertEqual(sum(cell.exit_order_id is not None for cell in cells), 10)
        sell_ids = [cell.exit_order_id for cell in cells if cell.exit_order_id is not None]
        window_events = lambda: sum(
            event["event_type"] == "ORDER_WINDOW_REBALANCED"
            for event in self.store.list_events(config.strategy_id)
        )
        initial_rebalances = window_events()

        for order_id in sell_ids[:5]:
            self.exchange.fill(order_id)
        engine.tick()
        cells = self.store.list_cells(config.strategy_id)
        self.assertEqual(sum(cell.exit_order_id is not None for cell in cells), 5)
        self.assertEqual(sum(cell.entry_order_id is not None for cell in cells), 15)
        self.assertEqual(window_events(), initial_rebalances)
        self.assertEqual(self.exchange.calls.get("cancel_order", 0), 0)

        self.exchange.fill(sell_ids[5])
        engine.tick()
        cells = self.store.list_cells(config.strategy_id)
        self.assertEqual(sum(cell.exit_order_id is not None for cell in cells), 10)
        self.assertEqual(sum(cell.entry_order_id is not None for cell in cells), 10)
        self.assertEqual(window_events(), initial_rebalances + 1)

    def test_order_window_refills_missing_buy_inside_nine_resting_orders(self) -> None:
        self.exchange.mark = Decimal("60.23")
        self.exchange.filters = replace(
            self.exchange.filters,
            step_size=Decimal("0.01"), min_qty=Decimal("0.01"),
            min_notional=Decimal("5"), contract_type="TRADIFI_PERPETUAL",
        )
        service = GridService(self.store, SimpleNamespace())
        config = service.create_fixed(
            "SOXLUSDT", Decimal("30"), Decimal("150"), 323,
            Decimal("5000"), Decimal("0.01"),
        )
        self.store.mark_started(config.strategy_id)
        engine = FixedGridEngine(self.store, self.exchange, config.strategy_id)
        engine.tick()
        buys = sorted(
            (cell for cell in self.store.list_cells(config.strategy_id)
             if cell.stage == CellStage.PENDING_ENTRY and cell.entry_order_id is not None),
            key=lambda cell: cell.buy_price, reverse=True,
        )
        self.assertEqual(len(buys), 10)
        missing = buys[1]
        previous_order_id = missing.entry_order_id
        self.exchange.cancel_order(config.symbol, previous_order_id)
        missing.entry_order_id = None
        missing.entry_client_id = ""
        self.store.save_cell(missing)
        self.assertEqual(
            sum(cell.entry_order_id is not None for cell in self.store.list_cells(config.strategy_id)
                if cell.stage == CellStage.PENDING_ENTRY), 9,
        )

        engine.tick()

        restored = self.store.get_cell(config.strategy_id, missing.cell_id)
        self.assertIsNotNone(restored.entry_order_id)
        self.assertNotEqual(restored.entry_order_id, previous_order_id)
        self.assertEqual(
            sum(cell.entry_order_id is not None for cell in self.store.list_cells(config.strategy_id)
                if cell.stage == CellStage.PENDING_ENTRY), 10,
        )
        self.assertTrue(any(
            event["event_type"] == "ORDER_WINDOW_REBALANCED"
            and event["payload"].get("entry_gap")
            for event in self.store.list_events(config.strategy_id)
        ))

    def test_order_window_refills_missing_sell_inside_nine_resting_orders(self) -> None:
        self.exchange.mark = Decimal("60.23")
        self.exchange.filters = replace(
            self.exchange.filters,
            step_size=Decimal("0.01"), min_qty=Decimal("0.01"),
            min_notional=Decimal("5"), contract_type="TRADIFI_PERPETUAL",
        )
        service = GridService(self.store, SimpleNamespace())
        config = service.create_fixed(
            "SOXLUSDT", Decimal("30"), Decimal("150"), 323,
            Decimal("5000"), Decimal("0.01"),
        )
        self.store.mark_started(config.strategy_id)
        engine = FixedGridEngine(self.store, self.exchange, config.strategy_id)
        engine.tick()
        sells = sorted(
            (cell for cell in self.store.list_cells(config.strategy_id)
             if cell.stage == CellStage.PENDING_EXIT and cell.exit_order_id is not None),
            key=lambda cell: cell.sell_price,
        )
        self.assertEqual(len(sells), 10)
        missing = sells[1]
        previous_order_id = missing.exit_order_id
        open_qty = missing.open_qty
        self.exchange.cancel_order(config.symbol, previous_order_id)
        missing.exit_order_id = None
        missing.exit_client_id = ""
        self.store.save_cell(missing)

        engine.tick()

        restored = self.store.get_cell(config.strategy_id, missing.cell_id)
        self.assertIsNotNone(restored.exit_order_id)
        self.assertNotEqual(restored.exit_order_id, previous_order_id)
        self.assertEqual(restored.open_qty, open_qty)
        self.assertTrue(any(
            event["event_type"] == "ORDER_WINDOW_REBALANCED"
            and event["payload"].get("exit_gap")
            for event in self.store.list_events(config.strategy_id)
        ))

    def test_order_window_trims_excess_orders_in_bounded_batches(self) -> None:
        self.exchange.mark = Decimal("60.23")
        self.exchange.filters = replace(
            self.exchange.filters,
            step_size=Decimal("0.01"), min_qty=Decimal("0.01"),
            min_notional=Decimal("5"), contract_type="TRADIFI_PERPETUAL",
        )
        service = GridService(self.store, SimpleNamespace())
        config = service.create_fixed(
            "SOXLUSDT", Decimal("30"), Decimal("150"), 323,
            Decimal("5000"), Decimal("0.01"),
        )
        self.store.mark_started(config.strategy_id)
        engine = FixedGridEngine(self.store, self.exchange, config.strategy_id)
        engine.tick()
        extra = [
            cell for cell in self.store.list_cells(config.strategy_id)
            if cell.stage == CellStage.PENDING_ENTRY and cell.entry_order_id is None
        ][:20]
        for cell in extra:
            engine._place_entry(cell)
        self.assertEqual(sum(c.entry_order_id is not None for c in self.store.list_cells(config.strategy_id)), 30)

        before = self.exchange.calls.get("cancel_order", 0)
        engine.tick()
        after_first = self.exchange.calls.get("cancel_order", 0)
        self.assertEqual(after_first - before, engine.MAX_WINDOW_CANCELS_PER_TICK)
        self.assertTrue(engine._window_cleanup_pending)

        engine.tick()
        after_second = self.exchange.calls.get("cancel_order", 0)
        self.assertLessEqual(after_second - after_first, engine.MAX_WINDOW_CANCELS_PER_TICK)
        self.assertFalse(engine._window_cleanup_pending)
        self.assertEqual(sum(c.entry_order_id is not None for c in self.store.list_cells(config.strategy_id)), 10)

    def test_position_patrol_does_not_place_orders_outside_window(self) -> None:
        self.exchange.mark = Decimal("60.23")
        self.exchange.filters = replace(
            self.exchange.filters,
            step_size=Decimal("0.01"), min_qty=Decimal("0.01"),
            min_notional=Decimal("5"), contract_type="TRADIFI_PERPETUAL",
        )
        service = GridService(self.store, SimpleNamespace())
        config = service.create_fixed(
            "SOXLUSDT", Decimal("30"), Decimal("150"), 323,
            Decimal("5000"), Decimal("0.01"),
        )
        self.store.mark_started(config.strategy_id)
        engine = FixedGridEngine(self.store, self.exchange, config.strategy_id)
        engine.tick()
        before = self.exchange.calls["place_limit_order"]

        engine.sync_orders_only()  # scheduler's position patrol, outside tick()

        self.assertEqual(self.exchange.calls["place_limit_order"], before)
        cells = self.store.list_cells(config.strategy_id)
        self.assertEqual(sum(cell.entry_order_id is not None for cell in cells), 10)
        self.assertEqual(sum(cell.exit_order_id is not None for cell in cells), 10)

    def test_small_price_move_keeps_existing_window(self) -> None:
        self.exchange.mark = Decimal("60.23")
        self.exchange.filters = replace(
            self.exchange.filters,
            step_size=Decimal("0.01"), min_qty=Decimal("0.01"),
            min_notional=Decimal("5"), contract_type="TRADIFI_PERPETUAL",
            percent_price_up=Decimal("1.03"), percent_price_down=Decimal("0.97"),
        )
        service = GridService(self.store, SimpleNamespace())
        config = service.create_fixed(
            "SOXLUSDT", Decimal("30"), Decimal("150"), 323,
            Decimal("5000"), Decimal("0.01"),
        )
        self.store.mark_started(config.strategy_id)
        engine = FixedGridEngine(self.store, self.exchange, config.strategy_id)
        engine.tick()
        placements = self.exchange.calls["place_limit_order"]
        self.exchange.mark = Decimal("60.35")
        engine.tick()
        self.assertEqual(self.exchange.calls["place_limit_order"], placements)
        self.assertEqual(self.exchange.calls.get("cancel_order", 0), 0)

    def test_filled_buy_gets_paired_sell_even_when_sell_window_is_full(self) -> None:
        self.exchange.mark = Decimal("60.23")
        self.exchange.filters = replace(
            self.exchange.filters,
            step_size=Decimal("0.01"), min_qty=Decimal("0.01"),
            min_notional=Decimal("5"), contract_type="TRADIFI_PERPETUAL",
        )
        service = GridService(self.store, SimpleNamespace())
        config = service.create_fixed(
            "SOXLUSDT", Decimal("30"), Decimal("150"), 323,
            Decimal("5000"), Decimal("0.01"),
        )
        self.store.mark_started(config.strategy_id)
        engine = FixedGridEngine(self.store, self.exchange, config.strategy_id)
        engine.tick()
        buy = next(
            cell for cell in self.store.list_cells(config.strategy_id)
            if cell.entry_order_id is not None
        )
        self.exchange.fill(buy.entry_order_id)
        engine.tick()
        updated = self.store.get_cell(config.strategy_id, buy.cell_id)
        self.assertIsNotNone(updated.exit_order_id)
        self.assertEqual(
            sum(cell.exit_order_id is not None for cell in self.store.list_cells(config.strategy_id)),
            11,
        )

    def test_crossed_limits_are_clamped_to_binance_price_band(self) -> None:
        self.exchange.filters = replace(
            self.exchange.filters,
            percent_price_up=Decimal("1.03"),
            percent_price_down=Decimal("0.97"),
        )
        engine = self.make_engine()
        engine.initialize()
        engine._submit_limit_order(
            "SOLUSDT", OrderSide.SELL, "LONG", Decimal("0.24"),
            Decimal("95"), "catchup-sell",
        )
        engine._submit_limit_order(
            "SOLUSDT", OrderSide.BUY, "LONG", Decimal("0.24"),
            Decimal("105"), "catchup-buy",
        )
        self.assertEqual(self.exchange.placed[-2]["price"], Decimal("97"))
        self.assertEqual(self.exchange.placed[-1]["price"], Decimal("103"))
        self.assertFalse(self.exchange.placed[-2]["post_only"])
        self.assertFalse(self.exchange.placed[-1]["post_only"])

    def test_recovery_accepts_only_price_improvement_from_catchup_limit(self) -> None:
        engine = self.make_engine()
        cell = self.store.list_cells(engine.strategy_id)[2]
        cell.open_qty = Decimal("1")
        order = OrderSnapshot(
            order_id=123, client_order_id="catchup-sell", status=OrderStatus.NEW,
            side=OrderSide.SELL, position_side="LONG",
            price=cell.sell_price + Decimal("1"),
            original_qty=Decimal("1"),
        )
        self.assertEqual(engine._recovery_mismatches(cell, "exit", order), [])
        worse = replace(order, price=cell.sell_price - Decimal("0.01"))
        self.assertTrue(any(
            item.startswith("price:")
            for item in engine._recovery_mismatches(cell, "exit", worse)
        ))

    def test_previously_deferred_held_exit_is_placed_without_window_rebalance(self) -> None:
        self.exchange.mark = Decimal("60.23")
        self.exchange.filters = replace(
            self.exchange.filters,
            step_size=Decimal("0.01"), min_qty=Decimal("0.01"),
            min_notional=Decimal("5"), contract_type="TRADIFI_PERPETUAL",
            percent_price_up=Decimal("1.03"), percent_price_down=Decimal("0.97"),
        )
        service = GridService(self.store, SimpleNamespace())
        config = service.create_fixed(
            "SOXLUSDT", Decimal("30"), Decimal("150"), 323,
            Decimal("5000"), Decimal("0.01"),
        )
        self.store.mark_started(config.strategy_id)
        engine = FixedGridEngine(self.store, self.exchange, config.strategy_id)
        engine.tick()
        cell = next(
            item for item in self.store.list_cells(config.strategy_id)
            if item.exit_order_id is not None
        )
        old_id = cell.exit_order_id
        self.exchange.cancel_order(config.symbol, old_id)
        cell.exit_order_id = None
        cell.exit_client_id = ""
        self.store.save_cell(cell)
        self.exchange.mark = cell.sell_price + Decimal("0.10")
        before = sum(
            event["event_type"] == "ORDER_WINDOW_REBALANCED"
            for event in self.store.list_events(config.strategy_id)
        )

        engine.tick()

        updated = self.store.get_cell(config.strategy_id, cell.cell_id)
        self.assertIsNotNone(updated.exit_order_id)
        self.assertNotEqual(updated.exit_order_id, old_id)
        rebalances = [
            event for event in self.store.list_events(config.strategy_id)
            if event["event_type"] == "ORDER_WINDOW_REBALANCED"
        ]
        self.assertEqual(len(rebalances), before + 1)
        self.assertTrue(rebalances[-1]["payload"]["exit_gap"])

    def test_failed_initial_seed_cannot_be_restarted_blindly(self) -> None:
        engine = self.make_engine()
        seed_id = f"seed:{engine.strategy_id}"
        self.store.create_capital_action(seed_id, engine.strategy_id, "seed", Decimal("400"))
        self.store.mark_capital_action_review(seed_id, "Binance -4411 agreement required")
        service = GridService(self.store, SimpleNamespace(start=lambda _: 12345))
        with self.assertRaisesRegex(ValueError, "requires review"):
            service.start(engine.strategy_id)

    def test_fixed_preview_and_create_api(self) -> None:
        app = create_app(Path(self.tempdir.name) / "api.sqlite3", lambda market=FuturesMarket.USDM: self.exchange)
        client = TestClient(app)
        payload = {
            "symbol": "SOLUSDT", "lower_price": "80", "upper_price": "120",
            "grid_count": 4, "grid_budget_usdt": "400",
            "auto_reinvest_enabled": True, "auto_reinvest_percent": "25",
        }
        preview = client.post("/fixed-grids/preview", json=payload)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["allocation"]["sell_cell_count"], 2)
        created = client.post("/fixed-grids", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["grid_kind"], "fixed_equal_usdt")
        self.assertEqual(created.json()["grid_budget_usdt"], "400")
        self.assertTrue(created.json()["auto_reinvest_enabled"])
        self.assertEqual(created.json()["auto_reinvest_percent"], "25")

    def test_shared_scheduler_selects_fixed_engine_and_reconciles_position(self) -> None:
        engine = self.make_engine()
        scheduler = StrategyScheduler(self.store, self.exchange, pid=12345)
        self.assertEqual(scheduler.run_once(now=0), 1)
        self.assertIsInstance(scheduler.engines[engine.strategy_id], FixedGridEngine)
        self.assertEqual(self.exchange.calls["place_market_order"], 1)
        self.assertEqual(
            sum((cell.open_qty for cell in self.store.list_cells(engine.strategy_id)), Decimal("0")),
            Decimal("1.942"),
        )

    def test_position_coordinator_accepts_virtual_far_exits(self) -> None:
        self.exchange.filters = replace(
            self.exchange.filters,
            contract_type="TRADIFI_PERPETUAL",
            percent_price_up=Decimal("1.03"),
            percent_price_down=Decimal("0.97"),
        )
        engine = self.make_engine()
        scheduler = StrategyScheduler(
            self.store, self.exchange, pid=12345, reconcile_interval_sec=5
        )
        scheduler.run_once(now=0)
        scheduler.run_once(now=6)
        pool = self.store.get_position_pool("SOLUSDT", "LONG", FuturesMarket.USDM)
        self.assertIsNotNone(pool)
        self.assertNotEqual(pool["status"], "manual_review")
        self.assertEqual(Decimal(pool["actual_qty"]), Decimal("1.942"))
        self.assertTrue(all(
            cell.stage != CellStage.MANUAL_REVIEW
            for cell in self.store.list_cells(engine.strategy_id)
        ))


if __name__ == "__main__":
    unittest.main()
