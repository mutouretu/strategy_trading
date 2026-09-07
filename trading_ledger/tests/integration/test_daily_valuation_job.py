from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from trading_ledger.application.contracts import (
    AddTrackedInstrumentCommand,
    ArchiveProjectCommand,
    ConfirmManualTradeCommand,
    CreateProjectCommand,
    RecordDailyValuationCommand,
)
from trading_ledger.application.daily_valuation import run_daily_valuations
from trading_ledger.domain import TradeSide
from trading_ledger.infrastructure.database import LedgerDatabase
from trading_ledger.infrastructure.quotes import RealtimeQuote
from trading_ledger.infrastructure.sqlite_application import SQLiteTradingLedgerApplication


NOW = datetime(2026, 9, 7, 15, 15, tzinfo=ZoneInfo("Asia/Shanghai"))


class DailyValuationJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.quotes = Mock()
        self.app = SQLiteTradingLedgerApplication(
            LedgerDatabase(Path(self.temp.name) / "test.sqlite3"), quote_provider=self.quotes
        )
        self.app._now = lambda: NOW
        self.app.initialize()
        self.project = self.project_named("持仓项目")
        self.app.add_tracking(AddTrackedInstrumentCommand(
            project_key=self.project.project_key, symbol="600000.SH", name="浦发银行",
            source_text="测试观察", actor="test",
        ))
        self.app.confirm_manual_trade(ConfirmManualTradeCommand(
            project_key=self.project.project_key, symbol="600000.SH", side=TradeSide.BUY,
            allocation_ratio=Decimal("0.5"), price=Decimal("10"), signal_text="测试买入",
            actor="test", trade_time=NOW.replace(hour=10),
        ))
        self.set_quote(NOW.replace(hour=15, minute=0))

    def project_named(self, name):
        return self.app.create_project(CreateProjectCommand(
            project_name=name, initial_capital=Decimal("100000"), actor="test"
        ))

    def set_quote(self, timestamp):
        self.quotes.fetch_many.return_value = ({
            "600000.SH": RealtimeQuote(
                "600000.SH", "浦发银行", Decimal("11"), timestamp, "测试收盘行情"
            )
        }, {})

    def run_job(self, now=NOW):
        return run_daily_valuations(self.app, now=now, actor="system:daily-valuation")

    def snapshots(self):
        with self.app.database.read() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM daily_account_valuations ORDER BY project_id"
            )]

    def test_refresh_then_value_and_repeated_run_keeps_one_snapshot(self):
        self.assertEqual(self.run_job(), 0)
        self.assertEqual(self.run_job(), 0)
        rows = self.snapshots()
        self.assertEqual(len(rows), 1)
        self.assertEqual(Decimal(rows[0]["equity"]), Decimal("104895"))
        self.assertEqual(rows[0]["valuation_date"], "2026-09-07")
        self.assertEqual(rows[0]["created_by"], "system:daily-valuation")
        self.assertEqual(self.app.integrity_check(), ())

    def test_weekend_and_before_close_do_not_fetch_or_write(self):
        self.assertEqual(self.run_job(NOW.replace(hour=14)), 0)
        self.assertEqual(self.run_job(NOW - timedelta(days=1)), 0)
        self.quotes.fetch_many.assert_not_called()
        self.assertEqual(self.snapshots(), [])

    def test_stale_or_future_quotes_do_not_create_valuation(self):
        for timestamp in (NOW - timedelta(days=3), NOW + timedelta(minutes=1)):
            with self.subTest(timestamp=timestamp):
                self.set_quote(timestamp)
                with self.assertLogs(level="ERROR"):
                    self.assertEqual(self.run_job(), 1)
                self.assertEqual(self.snapshots(), [])

    def test_failed_refresh_preserves_existing_valuation_and_continues_other_projects(self):
        self.app.record_daily_valuation(RecordDailyValuationCommand(
            project_key=self.project.project_key, actor="manual"
        ))
        before = self.snapshots()[0]
        cash = self.project_named("现金项目")
        self.quotes.fetch_many.return_value = ({}, {"600000.SH": "网络超时"})
        with self.assertLogs(level="ERROR"):
            self.assertEqual(self.run_job(), 1)
        rows = self.snapshots()
        self.assertEqual(rows[0], before)
        self.assertEqual(rows[1]["project_id"], cash.project_id)
        self.assertEqual(Decimal(rows[1]["equity"]), Decimal("100000"))

    def test_archived_projects_are_not_valued(self):
        active = self.project_named("保留活动项目")
        self.app.archive_project(ArchiveProjectCommand(self.project.project_key, "test"))
        self.assertEqual(self.run_job(), 0)
        self.quotes.fetch_many.assert_not_called()
        self.assertEqual([row["project_id"] for row in self.snapshots()], [active.project_id])

    def test_watch_only_quote_failure_does_not_block_valid_holdings(self):
        quotes, _ = self.quotes.fetch_many.return_value
        self.quotes.fetch_many.return_value = (quotes, {"000001.SZ": "观察股请求失败"})
        self.assertEqual(self.run_job(), 0)
        self.assertEqual(len(self.snapshots()), 1)


if __name__ == "__main__":
    unittest.main()
