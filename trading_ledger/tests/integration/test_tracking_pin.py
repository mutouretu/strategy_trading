from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from trading_ledger.application.contracts import (
    AddTrackedInstrumentCommand, ApplicationError, ArchiveProjectCommand,
    ArchiveTrackedInstrumentCommand, ConfirmManualTradeCommand, CreateProjectCommand,
    ListTrackingQuery, SetTrackingPinnedCommand,
    ReorderTrackingCommand,
)
from trading_ledger.domain import TradeSide
from trading_ledger.infrastructure.database import DATABASE_IDENTITY, MIGRATION_PATHS, LedgerDatabase
from trading_ledger.infrastructure.sqlite_application import SQLiteTradingLedgerApplication


class TrackingPinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ledger.sqlite3"
        self.app = SQLiteTradingLedgerApplication(LedgerDatabase(self.path))
        self.app.initialize()
        self.now = datetime(2026, 10, 28, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.app._now = Mock(return_value=self.now)
        self.project = self.app.create_project(CreateProjectCommand("置顶测试", Decimal("100000"), "test"))
        self.other = self.app.create_project(CreateProjectCommand("其他项目", Decimal("100000"), "test"))
        self.old = self.add("600000.SH", self.now - timedelta(days=10))
        self.new = self.add("600001.SH", self.now)

    def add(self, symbol, when):
        return self.app.add_tracking(AddTrackedInstrumentCommand(
            self.project.project_key, symbol, symbol, "观察", "test", added_at=when))

    def pin(self, row, pinned=True):
        return self.app.set_tracking_pinned(SetTrackingPinnedCommand(
            self.project.project_key, row.tracking_id, pinned, "test"))

    def rows(self):
        return self.app.list_tracking(ListTrackingQuery(self.project.project_key)).rows

    def test_pin_order_persistence_and_unpin_restore_original_order(self):
        self.assertEqual([r.tracking_id for r in self.rows()], [self.new.tracking_id, self.old.tracking_id])
        self.assertTrue(self.pin(self.old).is_pinned)
        self.assertEqual(self.rows()[0].tracking_id, self.old.tracking_id)
        self.app._now.return_value = self.now + timedelta(seconds=1)
        self.pin(self.new)
        self.assertEqual(self.rows()[0].tracking_id, self.new.tracking_id)
        # Repeating the same command must not move an already pinned item.
        self.app._now.return_value = self.now + timedelta(seconds=2)
        self.pin(self.old)
        restarted = SQLiteTradingLedgerApplication(LedgerDatabase(self.path))
        restarted.initialize()
        self.assertEqual([r.tracking_id for r in restarted.list_tracking(ListTrackingQuery(self.project.project_key)).rows],
                         [self.new.tracking_id, self.old.tracking_id])
        self.assertFalse(self.pin(self.new, False).is_pinned)
        self.assertEqual(self.rows()[0].tracking_id, self.old.tracking_id)
        self.pin(self.old, False)
        self.assertEqual(self.rows()[0].tracking_id, self.new.tracking_id)

    def test_holding_priority_overrides_pin_without_changing_financial_data(self):
        self.app.confirm_manual_trade(ConfirmManualTradeCommand(
            self.project.project_key, self.new.symbol, TradeSide.BUY, Decimal("0.5"), Decimal("10"), "买入", "test"))
        tables = ("accounts", "trade_records", "cash_ledger", "position_lots", "positions", "daily_account_valuations")
        with self.app.database.read() as db:
            before = {table: [tuple(r) for r in db.execute(f"SELECT * FROM {table}")] for table in tables}
        self.pin(self.old)
        self.assertEqual(self.rows()[0].tracking_id, self.new.tracking_id)
        with self.app.database.read() as db:
            self.assertEqual(before, {table: [tuple(r) for r in db.execute(f"SELECT * FROM {table}")] for table in tables})

    def test_project_isolation_and_archived_project_read_only(self):
        with self.assertRaises(ApplicationError):
            self.app.set_tracking_pinned(SetTrackingPinnedCommand(self.other.project_key, self.old.tracking_id, True, "test"))
        self.assertFalse(any(row.is_pinned for row in self.rows()))
        self.app.archive_project(ArchiveProjectCommand(self.project.project_key, "test"))
        with self.assertRaises(ApplicationError):
            self.pin(self.old)

    def test_archived_tracking_cannot_be_pinned_and_reentry_resets_pin(self):
        self.pin(self.old)
        self.app.archive_tracking(ArchiveTrackedInstrumentCommand(self.project.project_key, self.old.tracking_id, "test"))
        with self.assertRaises(ApplicationError):
            self.pin(self.old)
        reopened = self.add(self.old.symbol, self.now)
        self.assertFalse(reopened.is_pinned)

    def test_version_four_upgrade_preserves_existing_tracking(self):
        path = Path(self.temp.name) / "v4.sqlite3"
        with sqlite3.connect(path) as db:
            for version in range(1, 5):
                db.executescript(MIGRATION_PATHS[version].read_text())
                db.execute("INSERT INTO schema_migrations VALUES (?, ?)", (version, self.now.isoformat()))
            db.execute("INSERT INTO ledger_metadata VALUES ('database_identity', ?)", (DATABASE_IDENTITY,))
            db.execute("INSERT INTO ledger_metadata VALUES ('schema_version', '4')")
            db.execute("""INSERT INTO projects(project_id,project_key,project_name,project_name_normalized,status,created_at)
                VALUES (1,'old-project','旧项目','旧项目','ACTIVE',?)""", (self.now.isoformat(),))
            db.execute("""INSERT INTO instruments(instrument_id,market,venue,symbol,name,created_at,updated_at)
                VALUES (1,'CN_STOCK','SH','600000.SH','浦发银行',?,?)""", (self.now.isoformat(), self.now.isoformat()))
            db.execute("""INSERT INTO tracked_instruments(project_id,instrument_id,source_text,tracking_status,added_at,created_at,updated_at)
                VALUES (1,1,'原观察说明','WATCHING',?,?,?)""", (self.now.isoformat(),)*3)
            original = db.execute("SELECT * FROM tracked_instruments").fetchone()
        database = LedgerDatabase(path)
        database.initialize()
        database.initialize()
        with database.read() as db:
            row = tuple(db.execute("SELECT * FROM tracked_instruments").fetchone())
            self.assertEqual(row[:-2], original)
            self.assertEqual(row[-2:], (None, None))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=5").fetchone()[0], 1)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_reorder_persists_only_this_page_and_does_not_change_financial_records(self):
        for index in range(2, 13):
            self.add(f"{600000+index}.SH", self.now + timedelta(seconds=index))
        page = self.app.list_tracking(ListTrackingQuery(self.project.project_key))
        ids = tuple(row.tracking_id for row in page.rows)
        with self.app.database.read() as db:
            balances = [tuple(row) for row in db.execute("SELECT * FROM accounts")]
        self.app.reorder_tracking(ReorderTrackingCommand(
            self.project.project_key, ids[:10], tuple(reversed(ids[:10])), page.order_revision, "test"))
        app = SQLiteTradingLedgerApplication(LedgerDatabase(self.path))
        ordered = app.list_tracking(ListTrackingQuery(self.project.project_key))
        self.assertEqual(tuple(row.tracking_id for row in ordered.rows), tuple(reversed(ids[:10])) + ids[10:])
        self.assertNotEqual(ordered.order_revision, page.order_revision)
        with self.app.database.read() as db:
            self.assertEqual(balances, [tuple(row) for row in db.execute("SELECT * FROM accounts")])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM trade_records").fetchone()[0], 0)
        with self.assertRaisesRegex(ApplicationError, "列表已被更新"):
            self.app.reorder_tracking(ReorderTrackingCommand(
                self.project.project_key, ids[:10], tuple(reversed(ids[:10])), page.order_revision, "test"))

    def test_reorder_rejects_cross_group_and_cross_project(self):
        self.pin(self.old)
        page = self.app.list_tracking(ListTrackingQuery(self.project.project_key))
        ids = tuple(row.tracking_id for row in page.rows)
        with self.assertRaisesRegex(ApplicationError, "相同持仓和置顶"):
            self.app.reorder_tracking(ReorderTrackingCommand(self.project.project_key, ids, ids[::-1], page.order_revision, "test"))
        with self.assertRaises(ApplicationError):
            self.app.reorder_tracking(ReorderTrackingCommand(self.other.project_key, ids, ids[::-1], page.order_revision, "test"))
        self.pin(self.old, False)
        self.app.confirm_manual_trade(ConfirmManualTradeCommand(
            self.project.project_key, self.old.symbol, TradeSide.BUY, Decimal("0.1"), Decimal("10"), "买入", "test"))
        page = self.app.list_tracking(ListTrackingQuery(self.project.project_key))
        ids = tuple(row.tracking_id for row in page.rows)
        with self.assertRaisesRegex(ApplicationError, "相同持仓和置顶"):
            self.app.reorder_tracking(ReorderTrackingCommand(self.project.project_key, ids, ids[::-1], page.order_revision, "test"))

    def test_reorder_rejects_partial_page_duplicate_ids_and_archived_project(self):
        page = self.app.list_tracking(ListTrackingQuery(self.project.project_key))
        ids = tuple(row.tracking_id for row in page.rows)
        with self.assertRaises(ValueError):
            ReorderTrackingCommand(self.project.project_key, ids, (ids[0], ids[0]), page.order_revision, "test")
        with self.assertRaisesRegex(ApplicationError, "完整页面"):
            self.app.reorder_tracking(ReorderTrackingCommand(self.project.project_key, ids[:1], ids[:1], page.order_revision, "test"))
        self.app.archive_project(ArchiveProjectCommand(self.project.project_key, "test"))
        with self.assertRaises(ApplicationError):
            self.app.reorder_tracking(ReorderTrackingCommand(self.project.project_key, ids, ids[::-1], page.order_revision, "test"))

    def test_order_uses_actual_quantity_and_detects_stale_holdings(self):
        page = self.app.list_tracking(ListTrackingQuery(self.project.project_key))
        ids = tuple(row.tracking_id for row in page.rows)
        self.app.confirm_manual_trade(ConfirmManualTradeCommand(
            self.project.project_key, self.old.symbol, TradeSide.BUY, Decimal("0.1"), Decimal("10"), "买入", "test"))
        with self.app.database.transaction() as db:
            db.execute("UPDATE tracked_instruments SET tracking_status='WATCHING' WHERE tracking_id=?", (self.old.tracking_id,))
        self.assertEqual(self.rows()[0].tracking_id, self.old.tracking_id)
        with self.assertRaisesRegex(ApplicationError, "列表已被更新"):
            self.app.reorder_tracking(ReorderTrackingCommand(self.project.project_key, ids, ids[::-1], page.order_revision, "test"))
