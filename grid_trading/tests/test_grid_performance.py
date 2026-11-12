from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from grid_server.application.grid_performance import strategy_performance, sync_strategy_trades
from grid_server.application.service import GridService
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
        self.store.append_event(self.config.strategy_id, "ENTRY_PLACED", {"order_id": 101})
        self.store.append_event(self.config.strategy_id, "EXIT_PLACED", {"order_id": 102})
        cell = self.store.list_cells(self.config.strategy_id)[0]
        cell.cycle_count = 1
        self.store.save_cell(cell)

    def _trade(self, trade_id: int, order_id: int, pnl: str, fee: str,
               asset: str = "USDT") -> dict:
        return {
            "id": trade_id, "orderId": order_id, "side": "SELL",
            "qty": "1", "price": "100", "realizedPnl": pnl,
            "commission": fee, "commissionAsset": asset,
            "time": int((self.started + timedelta(minutes=trade_id)).timestamp() * 1000),
        }

    def test_owned_fills_net_fees_roi_and_annualized(self) -> None:
        exchange = TradeHistoryFake([
            self._trade(1, 101, "0", "0.02"),
            self._trade(2, 999, "100", "0"),
            self._trade(3, 102, "0.5", "0.02"),
        ])
        now = self.started + timedelta(days=1)
        self.assertTrue(sync_strategy_trades(self.store, exchange, self.config, now=now))
        self.assertEqual(self.store.latest_strategy_trade_id(self.config.strategy_id), 3)
        summary = strategy_performance(
            self.store, self.config, coverage_complete=True, now=now
        )
        self.assertEqual(Decimal(summary["gross_realized_pnl_usdt"]), Decimal("0.5"))
        self.assertEqual(Decimal(summary["trading_fees_usdt"]), Decimal("0.04"))
        self.assertEqual(Decimal(summary["net_grid_profit_usdt"]), Decimal("0.46"))
        self.assertEqual(Decimal(summary["grid_profit_pct"]), Decimal("0.115"))
        self.assertEqual(Decimal(summary["simple_annualized_pct"]), Decimal("41.975"))
        self.assertEqual(summary["completed_cycles"], 1)
        self.assertEqual(summary["trade_fill_count"], 2)
        self.assertTrue(sync_strategy_trades(self.store, exchange, self.config, now=now))
        self.assertEqual(len(self.store.list_strategy_trades(self.config.strategy_id)), 3)
        self.assertEqual(exchange.calls[-1]["from_id"], 4)

    def test_other_fee_asset_does_not_claim_net_profit(self) -> None:
        self.store.save_strategy_trades(
            self.config.strategy_id, [self._trade(1, 102, "1", "0.01", "BNB")]
        )
        summary = strategy_performance(
            self.store, self.config, coverage_complete=True,
            now=self.started + timedelta(days=1),
        )
        self.assertIsNone(summary["net_grid_profit_usdt"])
        self.assertEqual(summary["unsupported_fee_assets"], ["BNB"])

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
