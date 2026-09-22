from datetime import datetime
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from trading_ledger.application.contracts import (
    AddTrackedInstrumentCommand, ApplicationError, ConfirmManualTradeCommand,
    CreateProjectCommand, GetProjectQuery, ListPositionsQuery, ListTrackingQuery,
    PreviewManualTradeCommand, RecordConfirmedTradeCommand, ReverseTradeCommand,
    UpdateProjectCommand,
)
from trading_ledger.domain import TradeSide
from trading_ledger.infrastructure.database import LedgerDatabase
from trading_ledger.infrastructure.sqlite_application import SQLiteTradingLedgerApplication

TZ = ZoneInfo("Asia/Shanghai")


class TPlusOneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = SQLiteTradingLedgerApplication(LedgerDatabase(Path(self.temp.name) / "ledger.sqlite3"))
        self.app.initialize()
        self.now = datetime(2026, 9, 22, 14, tzinfo=TZ)
        self.app._now = Mock(return_value=self.now)
        self.project = self.app.create_project(CreateProjectCommand("正式项目", Decimal("100000"), "test"))
        self.key = self.project.project_key
        self.app.add_tracking(AddTrackedInstrumentCommand(self.key, "600000.SH", "浦发银行", "测试", "test"))
        self.sequence = 0

    def setting(self, enabled):
        return self.app.update_project(UpdateProjectCommand(self.key, "正式项目", "test", t_plus_one=enabled))

    def trade(self, side, quantity, timestamp):
        self.sequence += 1
        return self.app.record_confirmed_trade(RecordConfirmedTradeCommand(
            project_key=self.key, request_id=f"request-{self.sequence}", external_trade_id=f"external-{self.sequence}",
            account_code="primary-cny", market="CN_STOCK", venue="SH", symbol="600000.SH",
            side=side, trade_time=timestamp, quantity=Decimal(quantity), price=Decimal("10"),
            signal_text="测试", actor="test",
        ))

    def preview(self, ratio="1"):
        return self.app.preview_manual_trade(PreviewManualTradeCommand(
            self.key, "600000.SH", TradeSide.SELL, Decimal(ratio), Decimal("10"), "卖出",
        ))

    def sell(self, ratio="1", timestamp=None):
        return self.app.confirm_manual_trade(ConfirmManualTradeCommand(
            self.key, "600000.SH", TradeSide.SELL, Decimal(ratio), Decimal("10"), "卖出", "test",
            trade_time=timestamp,
        ))

    def position(self):
        return self.app.list_positions(ListPositionsQuery(self.key))[0]

    def test_default_enabled_and_edit_is_project_isolated(self):
        self.assertTrue(self.project.t_plus_one)
        other = self.app.create_project(CreateProjectCommand("其他项目", Decimal("100000"), "test"))
        self.assertFalse(self.setting(False).t_plus_one)
        renamed = self.app.update_project(UpdateProjectCommand(self.key, "正式项目", "test", color_key="blue"))
        self.assertFalse(renamed.t_plus_one)
        self.assertTrue(self.app.get_project(GetProjectQuery(other.project_key)).t_plus_one)

    def test_mixed_batches_sell_only_old_remaining_lots_without_archiving(self):
        # Record today's purchase first to ensure eligibility is not recording order.
        today = self.trade(TradeSide.BUY, "500", self.now.replace(hour=10))
        self.trade(TradeSide.BUY, "1000", self.now.replace(day=21, hour=10))
        self.trade(TradeSide.BUY, "200", self.now.replace(day=18, hour=10))
        self.assertEqual(self.position().quantity, Decimal("1700"))
        self.assertEqual(self.preview("0.5").quantity, Decimal("600"))
        self.assertEqual(self.sell("0.5").quantity, Decimal("600"))
        self.assertEqual(self.position().sellable_quantity, Decimal("600"))
        self.assertEqual(self.sell().quantity, Decimal("600"))
        self.assertEqual(self.position().quantity, Decimal("500"))
        self.assertEqual(self.position().sellable_quantity, Decimal("0"))
        rows = self.app.list_tracking(ListTrackingQuery(self.key)).rows
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].tracking_status, "HOLDING")
        with self.app.database.read() as db:
            self.assertEqual(db.execute("SELECT remaining_quantity FROM position_lots WHERE source_trade_id=?", (today.trade_id,)).fetchone()[0], "500")

    def test_today_only_blocks_preview_manual_and_external_then_unlocks_next_day(self):
        self.trade(TradeSide.BUY, "1000", self.now.replace(hour=10))
        self.assertEqual(self.position().sellable_quantity, Decimal("0"))
        for attempt in (self.preview, self.sell, lambda: self.trade(TradeSide.SELL, "100", self.now)):
            with self.assertRaises(ApplicationError):
                attempt()
        self.assertEqual(self.position().quantity, Decimal("1000"))
        self.app._now.return_value = self.now.replace(day=23)
        self.assertEqual(self.preview().quantity, Decimal("1000"))
        self.sell()
        self.assertEqual(self.app.list_tracking(ListTrackingQuery(self.key)).rows, ())

    def test_backdated_sell_uses_trade_date_not_wall_clock(self):
        self.trade(TradeSide.BUY, "1000", self.now.replace(day=21, hour=10))
        self.assertEqual(self.preview().quantity, Decimal("1000"))
        with self.assertRaises(ApplicationError):
            self.sell(timestamp=self.now.replace(day=21, hour=14))
        with self.assertRaises(ApplicationError):
            self.trade(TradeSide.SELL, "100", self.now.replace(day=21, hour=14))

    def test_date_boundary_uses_business_timezone(self):
        utc = ZoneInfo("UTC")
        self.trade(TradeSide.BUY, "500", datetime(2026, 9, 21, 16, 30, tzinfo=utc))  # Sep 22 CST
        self.trade(TradeSide.BUY, "1000", datetime(2026, 9, 21, 15, 30, tzinfo=utc))  # Sep 21 CST
        self.assertEqual(self.position().sellable_quantity, Decimal("1000"))
        self.assertEqual(self.preview().quantity, Decimal("1000"))

    def test_switching_on_does_not_invalidate_old_same_day_sales_during_replay(self):
        self.setting(False)
        self.trade(TradeSide.BUY, "1000", self.now.replace(hour=10))
        self.sell("0.5")
        extra = self.trade(TradeSide.BUY, "100", self.now)
        self.setting(True)
        self.app.reverse_trade(ReverseTradeCommand(self.key, extra.trade_id, "reverse-extra", "测试冲正", "test"))
        self.assertEqual(self.position().quantity, Decimal("500"))
        self.assertEqual(self.position().sellable_quantity, Decimal("0"))
        self.setting(False)
        self.assertEqual(self.preview().quantity, Decimal("500"))

    def test_replay_preserves_t_plus_one_consumption_after_switching_off(self):
        today = self.trade(TradeSide.BUY, "500", self.now.replace(hour=10))
        self.trade(TradeSide.BUY, "1000", self.now.replace(day=21, hour=10))
        self.sell()
        extra = self.trade(TradeSide.BUY, "100", self.now)
        self.setting(False)
        self.app.reverse_trade(ReverseTradeCommand(self.key, extra.trade_id, "reverse-extra", "测试冲正", "test"))
        with self.app.database.read() as db:
            self.assertEqual(db.execute("SELECT remaining_quantity FROM position_lots WHERE source_trade_id=?", (today.trade_id,)).fetchone()[0], "500")
        self.assertEqual(self.app.integrity_check(), ())
