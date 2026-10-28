from contextlib import closing
from datetime import datetime
from decimal import Decimal as D
import json
import sqlite3
import unittest
from unittest.mock import patch

from scripts.correct_000995_correction import (
    App, apply_plan, build_plan, fingerprint, replay, TZ,
)
from . import test_correction_preview as fixture


class CorrectionApplyTests(unittest.TestCase):
    setUp = fixture.CorrectionPreviewTests.setUp
    trade = fixture.CorrectionPreviewTests.trade
    dump = fixture.CorrectionPreviewTests.dump

    def plan(self):
        return build_plan(self.path, target_id=self.target.trade_id,
                          project_key=self.project.project_key,
                          now=datetime(2026, 10, 20, 12, tzinfo=TZ))

    def seed_snapshots(self):
        # Build snapshots at real historical cutoffs, including a cash-only
        # weekend immediately after liquidation and before the next purchase.
        from trading_ledger.application.contracts import RecordDailyValuationCommand
        from trading_ledger.application.historical_valuation import MemoryDatabase, save_snapshot
        with self.app.database.transaction() as db:
            db.execute("UPDATE trade_records SET trade_time='2026-09-28T14:30:00+08:00', created_at='2026-09-28T14:30:00+08:00' WHERE trade_id=?",
                       (self.later_buy.trade_id,))
            db.execute("UPDATE trade_records SET trade_time='2026-09-29T10:00:00+08:00', created_at='2026-09-29T10:00:00+08:00' WHERE trade_id=?",
                       (self.later_sale.trade_id,))
            for trade in (self.later_buy, self.later_sale):
                when = db.execute("SELECT trade_time FROM trade_records WHERE trade_id=?", (trade.trade_id,)).fetchone()[0]
                db.execute("UPDATE cash_ledger SET occurred_at=?, created_at=? WHERE trade_id=?", (when, when, trade.trade_id))
                db.execute("UPDATE position_lots SET created_at=?, available_date=? WHERE source_trade_id=?", (when, when[:10], trade.trade_id))
        for when in (datetime(2026, 9, 23, 16, tzinfo=TZ), datetime(2026, 9, 26, 16, tzinfo=TZ),
                     datetime(2026, 9, 29, 16, tzinfo=TZ)):
            with closing(sqlite3.connect(":memory:")) as memory:
                with self.app.database.read() as source:
                    source.backup(memory)
                memory.row_factory = sqlite3.Row
                memory.execute("UPDATE trade_records SET record_status='DRAFT' WHERE trade_time>?", (when.isoformat(),))
                app = App(MemoryDatabase(memory))
                app._replay_effective_trades(memory, self.project.project_id, 1)
                # All fixture quotes are manual; set each price to its intended
                # historical snapshot price, without depending on latest quotes.
                memory.execute("UPDATE reference_prices SET price='12'")
                app.record_daily_valuation(RecordDailyValuationCommand(
                    project_key=self.project.project_key, actor="test", valuation_time=when))
                snapshot = dict(memory.execute("SELECT * FROM daily_account_valuations WHERE account_id=1 AND valuation_date=?",
                                               (when.date().isoformat(),)).fetchone())
                positions = [dict(r) for r in memory.execute("SELECT * FROM daily_position_valuations WHERE valuation_id=?",
                                                             (snapshot["valuation_id"],))]
                with self.app.database.transaction() as db:
                    save_snapshot(db, snapshot, positions)

    def test_repair_is_atomic_audited_and_preserves_other_project_and_identifiers(self):
        self.seed_snapshots()
        before = self.dump()
        plan = self.plan()
        self.assertEqual(before, self.dump())
        self.assertEqual(len(plan.snapshots), 3)
        with self.app.database.read() as db:
            other = [tuple(r) for r in db.execute("SELECT * FROM accounts WHERE project_id<>?", (self.project.project_id,))]
            tracking = [tuple(r) for r in db.execute("SELECT * FROM tracked_instruments")]
            lots = [r[0] for r in db.execute("SELECT lot_id FROM position_lots")]
            trades = [tuple(r) for r in db.execute("SELECT rowid,trade_id,trade_time,created_at,signal_text,allocation_ratio FROM trade_records")]
            original_equity = json.loads(db.execute("SELECT metadata_json FROM trade_records WHERE trade_id=?",
                                                    (self.target.trade_id,)).fetchone()[0])["_ledger_equity_before"]
        backup = apply_plan(self.path, plan, plan.source_hash)
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(before, list(db.iterdump()))
        with self.app.database.read() as db:
            self.assertEqual(other, [tuple(r) for r in db.execute("SELECT * FROM accounts WHERE project_id<>?", (self.project.project_id,))])
            self.assertEqual(tracking, [tuple(r) for r in db.execute("SELECT * FROM tracked_instruments")])
            self.assertEqual(lots, [r[0] for r in db.execute("SELECT lot_id FROM position_lots")])
            self.assertEqual(trades, [tuple(r) for r in db.execute("SELECT rowid,trade_id,trade_time,created_at,signal_text,allocation_ratio FROM trade_records")])
            target = db.execute("SELECT * FROM trade_records WHERE trade_id=?", (self.target.trade_id,)).fetchone()
            self.assertEqual(D(target["quantity"]), D("40300"))
            metadata = json.loads(target["metadata_json"])
            self.assertNotEqual(metadata["_ledger_equity_before"], original_equity)
            self.assertTrue(metadata["_ledger_t_plus_one"])
            self.assertEqual(D(metadata["_ledger_equity_before"]), D("4431281.01"))
            sale = db.execute("SELECT * FROM trade_records WHERE trade_id=?", (self.sale.trade_id,)).fetchone()
            self.assertEqual(D(sale["quantity"]), D("173500"))
            cash, positions, _ = replay(db, 1, self.target.trade_id)
            self.assertEqual(cash, plan.report["cash_after"])
            cash_sum = sum((D(r[0]) for r in db.execute("SELECT amount FROM cash_ledger WHERE account_id=1")), D("0"))
            self.assertEqual(cash_sum, cash)
            first = db.execute("SELECT * FROM daily_account_valuations WHERE account_id=1 AND valuation_date='2026-09-23'").fetchone()
            self.assertEqual(D(first["cash_balance"]), D("2357915.08"))
            self.assertEqual(D(first["market_value"]), D("173500") * 12)
            second = db.execute("SELECT * FROM daily_account_valuations WHERE account_id=1 AND valuation_date='2026-09-26'").fetchone()
            self.assertEqual(D(second["market_value"]), 0)
            self.assertEqual(D(second["equity"]), D("4404706.38"))
            third = db.execute("SELECT * FROM daily_account_valuations WHERE account_id=1 AND valuation_date='2026-09-29'").fetchone()
            expected_peak = max(D(first["equity"]), D(second["equity"]), D(third["equity"]))
            self.assertEqual(D(third["peak_equity"]), expected_peak)
            self.assertEqual(D(third["drawdown"]), (D(third["equity"]) - expected_peak) / expected_peak)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM audit_events WHERE action='HISTORICAL_CHAIN_CORRECTED'").fetchone()[0],
                             len(plan.changes))
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        with self.assertRaisesRegex(ValueError, "目标交易"):
            self.plan()

    def test_changed_database_or_wrong_fingerprint_cannot_be_applied(self):
        plan = self.plan()
        with self.assertRaisesRegex(ValueError, "指定预演指纹"):
            apply_plan(self.path, plan, "0" * 64)
        with self.app.database.transaction() as db:
            db.execute("UPDATE projects SET description='new edit' WHERE project_id=?", (self.project.project_id,))
        before = self.dump()
        with self.assertRaisesRegex(ValueError, "预演后发生变化"):
            apply_plan(self.path, plan, plan.source_hash)
        self.assertEqual(before, self.dump())
        self.assertEqual(list(self.path.parent.glob("before-correct-*.sqlite3")), [])

    def test_failed_audit_rolls_back_all_writes_and_preserves_backup(self):
        before = self.dump()
        plan = self.plan()
        with patch.object(App, "_audit", side_effect=ValueError("injected audit error")):
            with self.assertRaisesRegex(ValueError, "injected audit error"):
                apply_plan(self.path, plan, plan.source_hash)
        self.assertEqual(before, self.dump())
        backups = list(self.path.parent.glob("before-correct-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as db:
            self.assertEqual(fingerprint(db), plan.source_hash)

    def test_rejects_wrong_snapshot_cash_without_writing(self):
        self.seed_snapshots()
        with self.app.database.transaction() as db:
            db.execute("UPDATE daily_account_valuations SET cash_balance='0' WHERE valuation_date='2026-09-23'")
        before = self.dump()
        with self.assertRaisesRegex(ValueError, "原估值现金"):
            self.plan()
        self.assertEqual(before, self.dump())

    def test_rejects_known_wrong_snapshot_price(self):
        self.seed_snapshots()
        with self.app.database.transaction() as db:
            db.execute("""UPDATE daily_position_valuations SET last_price='1.95'
                WHERE valuation_id IN (SELECT valuation_id FROM daily_account_valuations WHERE valuation_date='2026-09-23')""")
        with self.assertRaisesRegex(ValueError, "估值价仍为"):
            self.plan()

    def test_rejects_missing_equity_reference_price(self):
        from trading_ledger.domain import TradeSide
        self.app._now.return_value = datetime(2026, 10, 20, 11, tzinfo=TZ)
        self.app.add_tracking(fixture.AddTrackedInstrumentCommand(
            self.project.project_key, "000995.SZ", "测试", "重新观察", "test"))
        self.trade("000995.SZ", TradeSide.BUY, "0.01", "12")
        with self.app.database.transaction() as db:
            db.execute("DELETE FROM reference_prices WHERE instrument_id IN (SELECT instrument_id FROM instruments WHERE symbol='600000.SH')")
        with self.assertRaisesRegex(ValueError, "缺少历史价格"):
            self.plan()

    def test_rejects_inconsistent_original_lot_cost(self):
        with self.app.database.transaction() as db:
            db.execute("UPDATE position_lots SET unit_cost='0' WHERE source_trade_id=?", (self.target.trade_id,))
        with self.assertRaisesRegex(ValueError, "原 unit_cost"):
            self.plan()
