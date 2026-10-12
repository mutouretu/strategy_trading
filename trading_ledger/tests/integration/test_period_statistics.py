from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from trading_ledger.application.contracts import CreateProjectCommand, GetMonthlyStatisticsQuery
from trading_ledger.infrastructure.database import LedgerDatabase
from trading_ledger.infrastructure.sqlite_application import SQLiteTradingLedgerApplication

TZ = ZoneInfo("Asia/Shanghai")


class PeriodStatisticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = SQLiteTradingLedgerApplication(LedgerDatabase(Path(self.temp.name) / "ledger.sqlite3"))
        self.app.initialize()
        self.app._now = Mock(return_value=datetime(2025, 11, 1, tzinfo=TZ))
        self.project = self.app.create_project(CreateProjectCommand("区间测试", D("100"), "test"))
        self.app._now.return_value = datetime(2026, 11, 20, tzinfo=TZ)
        with self.app.database.read() as db:
            self.account_id = db.execute("SELECT account_id FROM accounts").fetchone()[0]

    def snapshot(self, day, equity, flow="0", partial=False):
        # Deliberately inconsistent legacy peaks prove reports recompute locally.
        with self.app.database.transaction() as db:
            db.execute("""INSERT INTO daily_account_valuations (
                valuation_id,project_id,account_id,valuation_date,valuation_time,
                cash_balance,market_value,equity,external_net_flow,pnl,
                peak_equity,drawdown,is_partial,created_by,created_at
                ) VALUES (?,?,?,?,?,?,'0',?,?,'0','999999','-0.999',?,'test',?)""",
                (day, self.project.project_id, self.account_id, day, day+'T15:00:00+08:00',
                 equity, equity, flow, int(partial), day+'T15:00:00+08:00'))

    def report(self, month="2026-10"):
        return self.app.monthly_statistics(GetMonthlyStatisticsQuery(self.project.project_key, month))

    def test_month_year_reset_independently_and_stop_at_selected_month(self):
        for day, equity in (("2025-11-10", "300"), ("2025-12-31", "100"),
                            ("2026-09-10", "150"), ("2026-09-30", "100"),
                            ("2026-10-05", "102"), ("2026-10-12", "99"), ("2026-11-05", "1")):
            self.snapshot(day, equity)
        with self.app.database.read() as db:
            before = list(db.iterdump())
        report = self.report()
        self.assertAlmostEqual(report.return_rate, D("-0.01"))
        self.assertAlmostEqual(report.max_drawdown, D("99") / D("102") - 1)
        self.assertAlmostEqual(report.annual_return_rate, D("-0.01"))
        self.assertAlmostEqual(report.annual_max_drawdown, D("-0.34"))
        self.assertFalse(report.performance_is_partial)
        with self.app.database.read() as db:
            self.assertEqual(before, list(db.iterdump()))

    def test_periods_exclude_flows_and_partial_snapshots(self):
        self.snapshot("2025-12-31", "100")
        self.snapshot("2026-09-30", "100")
        self.snapshot("2026-10-05", "200", "100")
        self.snapshot("2026-10-06", "1", "100", partial=True)
        self.snapshot("2026-10-08", "220", "100")
        self.snapshot("2026-10-09", "170", "50")
        report = self.report()
        self.assertEqual(report.return_rate, D("0.1"))
        self.assertEqual(report.annual_return_rate, D("0.1"))
        self.assertEqual(report.max_drawdown, D("0"))
        self.assertEqual(report.annual_max_drawdown, D("0"))
        self.assertTrue(report.performance_is_partial)

    def test_current_equity_replaces_todays_snapshot_not_historical_months(self):
        self.snapshot("2025-12-31", "100")
        self.snapshot("2026-09-30", "100")
        self.snapshot("2026-10-09", "105")
        self.snapshot("2026-10-12", "1000")
        self.app._now.return_value = datetime(2026, 10, 12, 16, tzinfo=TZ)
        report = self.report()
        self.assertEqual(report.return_rate, D("0"))  # Live account equity is 100.
        self.assertAlmostEqual(report.max_drawdown, D("100") / D("105") - 1)
        self.assertEqual(self.report("2026-09").return_rate, D("0"))

    def test_missing_baseline_uses_observed_interval_with_warning(self):
        self.snapshot("2026-10-05", "200")
        self.snapshot("2026-10-09", "180")
        report = self.report()
        self.assertEqual(report.return_rate, D("-0.1"))
        self.assertEqual(report.max_drawdown, D("-0.1"))
        self.assertTrue(report.performance_is_partial)

    def test_no_data_is_unavailable_not_zero(self):
        report = self.report()
        self.assertIsNone(report.return_rate)
        self.assertIsNone(report.max_drawdown)
        self.assertIsNone(report.annual_return_rate)
        self.assertIsNone(report.annual_max_drawdown)
        self.assertTrue(report.performance_is_partial)
