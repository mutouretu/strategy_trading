from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from trading_ledger.application.contracts import AddTrackedInstrumentCommand, CreateProjectCommand, ConfirmManualTradeCommand
from trading_ledger.application.historical_valuation import build_plan, apply_plan
from trading_ledger.domain import TradeSide
from trading_ledger.infrastructure.database import LedgerDatabase
from trading_ledger.infrastructure.sqlite_application import SQLiteTradingLedgerApplication

TZ = ZoneInfo("Asia/Shanghai")


class HistoricalValuationTests(unittest.TestCase):
    def test_backfill_replays_then_writes_only_snapshots_with_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data" / "ledger.sqlite3"
            app = SQLiteTradingLedgerApplication(LedgerDatabase(path))
            app.initialize()
            app._now = Mock(return_value=datetime(2026, 9, 4, 16, tzinfo=TZ))
            project = app.create_project(CreateProjectCommand("回放", Decimal("100000"), "test"))
            app.add_tracking(AddTrackedInstrumentCommand(project.project_key, "600000.SH", "测试", "观察", "test"))
            buy = app.confirm_manual_trade(ConfirmManualTradeCommand(project_key=project.project_key, symbol="600000.SH", side=TradeSide.BUY,
                allocation_ratio=Decimal("0.5"), price=Decimal("10"), signal_text="买入", actor="test", trade_time=datetime(2026, 9, 7, 10, tzinfo=TZ)))
            sell = app.confirm_manual_trade(ConfirmManualTradeCommand(project_key=project.project_key, symbol="600000.SH", side=TradeSide.SELL,
                allocation_ratio=Decimal("1"), price=Decimal("11"), signal_text="卖出", actor="test", trade_time=datetime(2026, 9, 8, 10, tzinfo=TZ)))
            provider = Mock()
            provider.fetch.side_effect = lambda symbol, start, end: {"2026-09-07": Decimal("12"), "2026-09-08": Decimal("11")}
            tables = ("trade_records", "cash_ledger", "positions", "position_lots", "tracked_instruments", "reference_prices", "accounts")
            def business():
                with app.database.read() as db:
                    return {table: [tuple(r) for r in db.execute(f"SELECT * FROM {table}")] for table in tables}
            before = business()
            plan = build_plan(path, date(2026, 9, 7), date(2026, 9, 8), provider, datetime(2026, 9, 9, 16, tzinfo=TZ))
            self.assertEqual(plan.skipped, [])
            self.assertEqual(business(), before)
            first, second = plan.valuations
            self.assertEqual(Decimal(first[0]["cash_balance"]), Decimal("100000") + buy.net_cash_amount)
            self.assertEqual(Decimal(first[0]["market_value"]), buy.quantity * Decimal("12"))
            self.assertEqual(Decimal(first[1][0]["quantity"]), buy.quantity)
            self.assertEqual(Decimal(second[0]["market_value"]), 0)
            self.assertEqual(Decimal(second[0]["cash_balance"]), Decimal("100000") + buy.net_cash_amount + sell.net_cash_amount)
            backup = apply_plan(path, plan)
            self.assertTrue(backup.is_file())
            self.assertEqual(business(), before)
            self.assertEqual(app.integrity_check(), ())
            with self.assertRaisesRegex(ValueError, "发生变化"):
                apply_plan(path, plan)
            provider.fetch.side_effect = lambda symbol, start, end: ({"2026-09-07": Decimal("12")} if symbol == "000001.SH" else {})
            missing = build_plan(path, date(2026, 9, 7), date(2026, 9, 7), provider, datetime(2026, 9, 9, 16, tzinfo=TZ))
            self.assertEqual(len(missing.skipped), 1)
            self.assertEqual(missing.valuations, [])
