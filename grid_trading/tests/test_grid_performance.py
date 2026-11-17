from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from grid_server.application.grid_performance import strategy_performance, sync_strategy_trades
from grid_server.application.service import GridService
from grid_server.domain import GridCell
from grid_server.infrastructure.sqlite_store import SQLiteStore


class TradeHistoryFake:
    def __init__(self, trades: list[dict]) -> None:
        self.trades = trades
        self.calls: list[dict] = []

    def get_user_trades(self, symbol: str, **kwargs) -> list[dict]:
        self.calls.append(kwargs)
        rows = self.trades
        if kwargs.get("from_id") is not None:
            rows = [row for row in rows if row["id"] >= kwargs["from_id"]]
        else:
            rows = [
                row for row in rows
                if kwargs["start_time_ms"] <= row["time"] <= kwargs["end_time_ms"]
            ]
        return rows[: kwargs["limit"]]


class GridPerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        self.store = SQLiteStore(Path(tempdir.name) / "performance.sqlite3")
        self.config = GridService(self.store, SimpleNamespace()).create_fixed(
            "SOXLUSDT", Decimal("80"), Decimal("120"), 4,
            Decimal("400"), Decimal("0.01"),
        )
        self.store.mark_started(self.config.strategy_id)
        self.started = datetime.fromisoformat(
            self.store.get_first_started_at(self.config.strategy_id)
        )
        self.cell = self.store.list_cells(self.config.strategy_id)[0]
        self.store.append_event(
            self.config.strategy_id, "ENTRY_PLACED", {"order_id": 101}, self.cell.cell_id
        )
        self.store.append_event(
            self.config.strategy_id, "EXIT_PLACED", {"order_id": 102}, self.cell.cell_id
        )
        self.cell.cycle_count = 1
        self.store.save_cell(self.cell)

    def _trade(self, trade_id: int, order_id: int, pnl: str, fee: str,
               asset: str = "USDT", side: str = "SELL", qty: str = "1",
               price: str | None = None) -> dict:
        return {
            "id": trade_id, "orderId": order_id, "side": side,
            "qty": qty, "price": price or str(self.cell.buy_price if side == "BUY" else self.cell.sell_price),
            "realizedPnl": pnl,
            "commission": fee, "commissionAsset": asset,
            "time": int((self.started + timedelta(minutes=trade_id)).timestamp() * 1000),
        }

    def test_owned_fills_net_fees_roi_and_annualized(self) -> None:
        exchange = TradeHistoryFake([
            self._trade(1, 101, "0", "0.02", side="BUY"),
            self._trade(2, 999, "100", "0"),
            self._trade(3, 102, "0.5", "0.02"),
        ])
        now = self.started + timedelta(days=1)
        self.assertTrue(sync_strategy_trades(self.store, exchange, self.config, now=now))
        self.assertEqual(self.store.latest_strategy_trade_id(self.config.strategy_id), 3)
        summary = strategy_performance(
            self.store, self.config, coverage_complete=True, now=now
        )
        gross = self.cell.sell_price - self.cell.buy_price
        net = gross - Decimal("0.04")
        self.assertEqual(Decimal(summary["gross_grid_profit_usdt"]), gross)
        self.assertEqual(Decimal(summary["grid_trading_fees_usdt"]), Decimal("0.04"))
        self.assertEqual(Decimal(summary["net_grid_profit_usdt"]), net)
        self.assertEqual(
            Decimal(summary["cell_net_grid_profit_usdt"][self.cell.cell_id]), net
        )
        self.assertEqual(
            sum((Decimal(value) for value in summary["cell_net_grid_profit_usdt"].values()), Decimal("0")),
            net,
        )
        self.assertEqual(Decimal(summary["gross_realized_pnl_usdt"]), Decimal("0.5"))
        self.assertEqual(Decimal(summary["grid_profit_pct"]), net / Decimal("400") * 100)
        self.assertEqual(Decimal(summary["simple_annualized_pct"]), net / Decimal("400") * 36500)
        self.assertTrue(summary["accounting_complete"])
        self.assertEqual(summary["completed_cycles"], 1)
        self.assertEqual(summary["trade_fill_count"], 2)
        incomplete = strategy_performance(
            self.store, self.config, coverage_complete=False, now=now
        )
        self.assertIsNone(incomplete["cell_net_grid_profit_usdt"])
        self.assertTrue(sync_strategy_trades(self.store, exchange, self.config, now=now))
        self.assertEqual(len(self.store.list_strategy_trades(self.config.strategy_id)), 3)
        self.assertEqual(exchange.calls[-1]["from_id"], 4)

    def test_initial_entry_price_comes_from_seed_fill_event(self) -> None:
        self.store.append_event(
            self.config.strategy_id,
            "CAPITAL_ACTION_APPLIED",
            {"operation": "seed", "average_price": "62.93"},
        )
        self.store.append_event(
            self.config.strategy_id,
            "CAPITAL_ACTION_APPLIED",
            {"operation": "topup", "average_price": "60.00"},
        )
        summary = strategy_performance(
            self.store, self.config, coverage_complete=True,
            now=self.started + timedelta(days=1),
        )
        self.assertEqual(summary["initial_entry_price"], "62.93")

    def test_adverse_catchup_fill_is_a_grid_loss_not_theoretical_line_profit(self) -> None:
        self.store.save_strategy_trades(self.config.strategy_id, [
            self._trade(1, 101, "0", "0", side="BUY", qty="0.79", price="62.82"),
            self._trade(2, 102, "0.20894230", "0.01984480", qty="0.79", price="62.80"),
        ])

        summary = strategy_performance(
            self.store, self.config, coverage_complete=True,
            now=self.started + timedelta(days=1),
        )

        self.assertEqual(Decimal(summary["gross_grid_profit_usdt"]), Decimal("-0.0158"))
        self.assertEqual(Decimal(summary["net_grid_profit_usdt"]), Decimal("-0.03564480"))
        self.assertEqual(Decimal(summary["gross_realized_pnl_usdt"]), Decimal("0.20894230"))

    def test_favorable_catchup_fill_does_not_inflate_grid_profit(self) -> None:
        buy_price = self.cell.buy_price
        sell_price = self.cell.sell_price
        improved_sell = sell_price + Decimal("2")
        self.store.save_strategy_trades(self.config.strategy_id, [
            self._trade(1, 101, "0", "0.02", side="BUY", price=str(buy_price)),
            self._trade(2, 102, "3", "0.03", price=str(improved_sell)),
        ])

        summary = strategy_performance(
            self.store, self.config, coverage_complete=True,
            now=self.started + timedelta(days=1),
        )

        planned_spread = sell_price - buy_price
        self.assertEqual(Decimal(summary["gross_grid_profit_usdt"]), planned_spread)
        self.assertEqual(
            Decimal(summary["net_grid_profit_usdt"]), planned_spread - Decimal("0.05")
        )
        self.assertEqual(
            Decimal(summary["trend_and_execution_pnl_usdt"]), Decimal("3") - planned_spread
        )
        self.assertEqual(
            Decimal(summary["grid_profit_pct"]),
            (planned_spread - Decimal("0.05")) / Decimal("400") * 100,
        )

    def test_other_fee_asset_does_not_claim_net_profit(self) -> None:
        self.store.save_strategy_trades(
            self.config.strategy_id, [
                self._trade(1, 101, "0", "0", side="BUY"),
                self._trade(2, 102, "1", "0.01", "BNB"),
            ]
        )
        summary = strategy_performance(
            self.store, self.config, coverage_complete=True,
            now=self.started + timedelta(days=1),
        )
        self.assertIsNone(summary["net_grid_profit_usdt"])
        self.assertIsNone(summary["cell_net_grid_profit_usdt"])
        self.assertEqual(summary["unsupported_fee_assets"], ["BNB"])

    def test_seed_fee_is_allocated_only_to_sold_quantity(self) -> None:
        other = self.store.list_cells(self.config.strategy_id)[1]
        allocations = {self.cell.cell_id: Decimal("1"), other.cell_id: Decimal("1")}
        action_id = f"seed:{self.config.strategy_id}"
        self.store.create_capital_action(
            action_id, self.config.strategy_id, "seed", Decimal("400")
        )
        self.store.plan_capital_action(
            action_id, list(allocations), allocations, Decimal("2"), "seed-client"
        )
        self.store.mark_capital_action_submitted(action_id, 100)
        self.store.apply_capital_action(action_id, allocations, Decimal("2"))
        self.store.save_strategy_trades(self.config.strategy_id, [
            self._trade(1, 100, "0", "0.20", side="BUY", qty="2"),
            self._trade(2, 102, "7.00", "0.01"),
        ])
        summary = strategy_performance(
            self.store, self.config, coverage_complete=True,
            now=self.started + timedelta(days=1),
        )
        self.assertEqual(
            Decimal(summary["net_grid_profit_usdt"]),
            self.cell.sell_price - self.cell.buy_price - Decimal("0.11"),
        )
        self.assertEqual(Decimal(summary["trading_fees_usdt"]), Decimal("0.21"))
        self.assertEqual(Decimal(summary["grid_trading_fees_usdt"]), Decimal("0.11"))
        self.assertEqual(
            Decimal(summary["cell_net_grid_profit_usdt"][self.cell.cell_id]),
            self.cell.sell_price - self.cell.buy_price - Decimal("0.11"),
        )
        self.assertEqual(
            Decimal(summary["cell_net_grid_profit_usdt"][other.cell_id]), Decimal("0"),
        )

    def test_partial_sell_fills_match_one_buy_lot(self) -> None:
        self.store.save_strategy_trades(self.config.strategy_id, [
            self._trade(1, 101, "0", "0.04", side="BUY", qty="2"),
            self._trade(2, 102, "5", "0.005", qty="0.5"),
            self._trade(3, 102, "5", "0.015", qty="1.5"),
        ])
        summary = strategy_performance(
            self.store, self.config, coverage_complete=True,
            now=self.started + timedelta(days=1),
        )
        gross = Decimal("2") * (self.cell.sell_price - self.cell.buy_price)
        self.assertEqual(Decimal(summary["gross_grid_profit_usdt"]), gross)
        self.assertEqual(Decimal(summary["grid_trading_fees_usdt"]), Decimal("0.06"))
        self.assertEqual(Decimal(summary["net_grid_profit_usdt"]), gross - Decimal("0.06"))
        self.assertEqual(
            Decimal(summary["cell_net_grid_profit_usdt"][self.cell.cell_id]), gross - Decimal("0.06")
        )

    def test_partial_sell_books_only_sold_quantity_and_matched_buy_fee(self) -> None:
        self.store.save_strategy_trades(self.config.strategy_id, [
            self._trade(1, 101, "0", "0.04", side="BUY", qty="2"),
            self._trade(2, 102, "1", "0.005", qty="0.5"),
        ])
        summary = strategy_performance(
            self.store, self.config, coverage_complete=True,
            now=self.started + timedelta(days=1),
        )
        expected = Decimal("0.5") * (self.cell.sell_price - self.cell.buy_price) - Decimal("0.015")
        self.assertEqual(
            Decimal(summary["cell_net_grid_profit_usdt"][self.cell.cell_id]), expected
        )
        self.assertEqual(Decimal(summary["net_grid_profit_usdt"]), expected)

    def test_missing_buy_fill_hides_profit(self) -> None:
        self.store.save_strategy_trades(
            self.config.strategy_id, [self._trade(1, 102, "5", "0")]
        )
        summary = strategy_performance(
            self.store, self.config, coverage_complete=True,
            now=self.started + timedelta(days=1),
        )
        self.assertFalse(summary["accounting_complete"])
        self.assertIsNone(summary["net_grid_profit_usdt"])
        self.assertIsNone(summary["cell_net_grid_profit_usdt"])

    def test_removed_cell_keeps_its_grid_prices(self) -> None:
        self.store.save_strategy_trades(self.config.strategy_id, [
            self._trade(1, 101, "0", "0", side="BUY"),
            self._trade(2, 102, "5", "0"),
        ])
        self.store.delete_cell(self.config.strategy_id, self.cell.cell_id)
        summary = strategy_performance(
            self.store, self.config, coverage_complete=True,
            now=self.started + timedelta(days=1),
        )
        self.assertEqual(
            Decimal(summary["net_grid_profit_usdt"]),
            self.cell.sell_price - self.cell.buy_price,
        )

    def test_new_cell_prices_survive_insert_and_delete(self) -> None:
        cell = GridCell(
            strategy_id=self.config.strategy_id, cell_id="new-cell", index=5,
            buy_price=Decimal("120"), sell_price=Decimal("121"),
        )
        self.store.save_cell(cell)
        self.store.delete_cell(self.config.strategy_id, cell.cell_id)
        self.assertEqual(
            self.store.get_cell_price_history(self.config.strategy_id)[cell.cell_id],
            (Decimal("120"), Decimal("121")),
        )

    def test_history_coverage_remains_complete_after_ninety_days(self) -> None:
        exchange = TradeHistoryFake([self._trade(1, 101, "0", "0.01")])
        self.assertTrue(sync_strategy_trades(
            self.store, exchange, self.config,
            now=self.started + timedelta(days=1),
        ))
        self.assertTrue(sync_strategy_trades(
            self.store, exchange, self.config,
            now=self.started + timedelta(days=80),
        ))
        self.assertTrue(sync_strategy_trades(
            self.store, exchange, self.config,
            now=self.started + timedelta(days=91),
        ))
        self.assertEqual(self.store.get_strategy_trade_sync(self.config.strategy_id)["history_complete"], 1)

    def test_long_sync_gap_marks_history_incomplete(self) -> None:
        exchange = TradeHistoryFake([self._trade(1, 101, "0", "0.01")])
        self.assertTrue(sync_strategy_trades(
            self.store, exchange, self.config,
            now=self.started + timedelta(days=1),
        ))
        self.assertFalse(sync_strategy_trades(
            self.store, exchange, self.config,
            now=self.started + timedelta(days=92),
        ))

    def test_old_strategy_without_prior_sync_has_incomplete_history(self) -> None:
        exchange = TradeHistoryFake([])
        self.assertFalse(sync_strategy_trades(
            self.store, exchange, self.config,
            now=self.started + timedelta(days=91),
        ))
        self.assertEqual(self.store.get_strategy_trade_sync(self.config.strategy_id)["history_complete"], 0)


if __name__ == "__main__":
    unittest.main()
