from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from scripts.preview_000995_correction import preview
from trading_ledger.application.contracts import (
    AddTrackedInstrumentCommand, ConfirmManualTradeCommand, CreateProjectCommand,
    RecordConfirmedTradeCommand,
)
from trading_ledger.domain import TradeSide
from trading_ledger.infrastructure.database import LedgerDatabase
from trading_ledger.infrastructure.sqlite_application import SQLiteTradingLedgerApplication

TZ = ZoneInfo("Asia/Shanghai")


class CorrectionPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ledger.sqlite3"
        self.app = SQLiteTradingLedgerApplication(LedgerDatabase(self.path))
        self.app.initialize()
        self.app._now = Mock(return_value=datetime(2026, 9, 1, 9, tzinfo=TZ))
        self.project = self.app.create_project(CreateProjectCommand("测试", D("4431416.31"), "test"))
        self.app.create_project(CreateProjectCommand("不涉及的项目", D("100000"), "test"))
        for symbol in ("000995.SZ", "600000.SH"):
            self.app.add_tracking(AddTrackedInstrumentCommand(self.project.project_key, symbol, symbol, "测试", "test"))
        self.app._now.return_value = datetime(2026, 9, 22, 10, tzinfo=TZ)
        self.app.record_confirmed_trade(RecordConfirmedTradeCommand(
            project_key=self.project.project_key, request_id="old-buy", external_trade_id="old-buy",
            account_code="primary-cny", market="CN_STOCK", venue="SZ", symbol="000995.SZ",
            side=TradeSide.BUY, trade_time=self.app._now(), quantity=D("133200"), price=D("11.95"),
            signal_text="原底仓", actor="test",
        ))
        self.app._now.return_value = datetime(2026, 9, 23, 10, 55, 18, tzinfo=TZ)
        self.target = self.trade("000995.SZ", TradeSide.BUY, "0.17", "1.95")
        self.assertEqual(self.target.quantity, D("247500"))
        self.app._now.return_value = datetime(2026, 9, 24, 9, 46, 57, tzinfo=TZ)
        self.sale = self.trade("000995.SZ", TradeSide.SELL, "1", "11.81")
        self.app._now.return_value = datetime(2026, 9, 24, 14, 30, tzinfo=TZ)
        self.later_buy = self.trade("600000.SH", TradeSide.BUY, "1", "10")
        self.app._now.return_value = datetime(2026, 9, 25, 10, tzinfo=TZ)
        self.later_sale = self.trade("600000.SH", TradeSide.SELL, "0.5", "11")

    def trade(self, symbol, side, ratio, price):
        return self.app.confirm_manual_trade(ConfirmManualTradeCommand(
            self.project.project_key, symbol, side, D(ratio), D(price), "测试", "test",
        ))

    def run_preview(self):
        return preview(self.path, target_id=self.target.trade_id, project_key=self.project.project_key)

    def dump(self):
        with self.app.database.read() as db:
            return list(db.iterdump())

    def test_cascades_proportions_without_writing_any_project(self):
        before = self.dump()
        result = self.run_preview()
        self.assertEqual(before, self.dump())
        self.assertEqual(len(result["changes"]), 4)
        changes = {item["after"]["trade_id"]: item for item in result["changes"]}
        buy = changes[self.target.trade_id]["after"]
        self.assertEqual(buy["quantity"], D("40300"))
        self.assertEqual(buy["price"], D("11.95"))
        self.assertEqual(buy["net_cash"], D("-481625.93"))
        sale = changes[self.sale.trade_id]["after"]
        self.assertEqual(sale["quantity"], D("173500"))
        self.assertEqual(sale["net_cash"], D("2046791.30"))
        later = changes[self.later_buy.trade_id]
        self.assertLess(later["after"]["quantity"], later["before"]["quantity"])
        self.assertEqual(later["after"]["ratio"], later["before"]["ratio"])
        self.assertEqual(later["after"]["price"], later["before"]["price"])
        self.assertGreaterEqual(result["cash_after"], 0)
        self.assertEqual(len(result["database_fingerprint"]), 64)

    def test_rejects_missing_ratio_instead_of_guessing(self):
        with self.app.database.transaction() as db:
            db.execute("UPDATE trade_records SET allocation_ratio=NULL WHERE trade_id=?", (self.later_buy.trade_id,))
        before = self.dump()
        with self.assertRaisesRegex(ValueError, "带比例"):
            self.run_preview()
        self.assertEqual(before, self.dump())

    def test_rejects_already_changed_target(self):
        with self.app.database.transaction() as db:
            db.execute("UPDATE trade_records SET price='11.95' WHERE trade_id=?", (self.target.trade_id,))
        with self.assertRaisesRegex(ValueError, "目标交易"):
            self.run_preview()

    def test_rejects_existing_cash_inconsistency(self):
        with self.app.database.transaction() as db:
            db.execute("UPDATE accounts SET cash_balance='1' WHERE project_id=?", (self.project.project_id,))
        with self.assertRaisesRegex(ValueError, "现存现金"):
            self.run_preview()

    def test_checks_historical_t_plus_one(self):
        with self.app.database.transaction() as db:
            db.execute("UPDATE trade_records SET trade_time='2026-09-23T14:00:00+08:00' WHERE trade_id=?", (self.sale.trade_id,))
        with self.assertRaisesRegex(ValueError, "超过当时可卖"):
            self.run_preview()
