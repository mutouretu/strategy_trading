"""Explicit, backed-up historical valuation repair; never replay into live positions."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time
import hashlib
from pathlib import Path
import sqlite3
from uuid import uuid4
from zoneinfo import ZoneInfo

from .contracts import RecordDailyValuationCommand, RecordReferencePriceCommand
from trading_ledger.infrastructure.database import DATABASE_IDENTITY, LedgerDatabase
from trading_ledger.infrastructure.sqlite_application import SQLiteTradingLedgerApplication


@dataclass
class BackfillPlan:
    fingerprint: str
    valuations: list[tuple[dict, list[dict]]]
    skipped: list[str]


def fingerprint(db):
    return hashlib.sha256("\n".join(db.iterdump()).encode()).hexdigest()


class MemoryDatabase(LedgerDatabase):
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def read(self):
        yield self.connection

    @contextmanager
    def transaction(self):
        yield self.connection


def save_snapshot(db, valuation, positions):
    row = dict(valuation)
    existing = db.execute("SELECT valuation_id FROM daily_account_valuations WHERE account_id=? AND valuation_date=?",
                          (row["account_id"], row["valuation_date"])).fetchone()
    if existing:
        row["valuation_id"] = existing[0]
        db.execute("DELETE FROM daily_position_valuations WHERE valuation_id=?", (existing[0],))
    columns = list(row)
    db.execute(f"INSERT INTO daily_account_valuations ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
               "ON CONFLICT(valuation_id) DO UPDATE SET " + ','.join(f'{c}=excluded.{c}' for c in columns),
               tuple(row.values()))
    for position in positions:
        item = dict(position, valuation_id=row["valuation_id"])
        db.execute(f"INSERT INTO daily_position_valuations ({','.join(item)}) VALUES ({','.join('?' for _ in item)})",
                   tuple(item.values()))
    return row["valuation_id"]


def build_plan(path: Path, start: date, end: date, provider, now: datetime) -> BackfillPlan:
    if start > end or end > now.date():
        raise ValueError("补算范围无效，不能补算未来日期")
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as source:
        base = sqlite3.connect(":memory:")
        source.backup(base)
    base.row_factory = sqlite3.Row
    try:
        metadata = dict(base.execute("SELECT key,value FROM ledger_metadata").fetchall())
        if metadata.get("database_identity") != DATABASE_IDENTITY:
            raise ValueError("不是当前交易账本数据库，拒绝补算")
        original_hash = fingerprint(base)
        calendar = provider.fetch("000001.SH", start, end)
        dates = [d for d in sorted(calendar) if d < now.date().isoformat() or now.time() >= time(15, 15)]
        accounts = base.execute("""SELECT a.*, p.project_key FROM accounts a JOIN projects p USING(project_id)
            WHERE a.account_code='primary-cny' AND a.status='ACTIVE' AND p.status='ACTIVE'""").fetchall()
        # Earlier snapshots stay intact; rebuilt days form the new peak sequence.
        base.execute("DELETE FROM daily_position_valuations WHERE valuation_id IN (SELECT valuation_id FROM daily_account_valuations WHERE valuation_date>=?)", (start.isoformat(),))
        base.execute("DELETE FROM daily_account_valuations WHERE valuation_date>=?", (start.isoformat(),))
        base.commit()
        prices = {}
        errors = {}
        instruments = base.execute("""SELECT DISTINCT i.instrument_id,i.symbol,i.venue,i.market FROM instruments i
            JOIN trade_records tr USING(instrument_id) WHERE tr.record_status='CONFIRMED'
            AND tr.reversal_of_trade_id IS NULL AND substr(tr.trade_time,1,10)<=?""", (end.isoformat(),)).fetchall()
        for instrument in instruments:
            try:
                prices[instrument["symbol"]] = provider.fetch(instrument["symbol"], start, end)
            except ValueError as error:
                errors[instrument["symbol"]] = str(error)
        plan = BackfillPlan(original_hash, [], [])
        for day in dates:
            for account in accounts:
                account_id = account["account_id"]
                initial = base.execute("SELECT MIN(occurred_at) FROM cash_ledger WHERE account_id=? AND entry_type='INITIAL_CAPITAL'", (account_id,)).fetchone()[0]
                if initial and initial[:10] > day:
                    continue
                db = sqlite3.connect(":memory:")
                base.backup(db)
                db.row_factory = sqlite3.Row
                try:
                    cutoff = min(datetime.combine(date.fromisoformat(day), time(23, 59, 59), tzinfo=now.tzinfo), now)
                    db.execute("UPDATE trade_records SET record_status='DRAFT' WHERE account_id=? AND trade_time>? AND record_status='CONFIRMED'", (account_id, cutoff.isoformat()))
                    db.execute("DELETE FROM cash_ledger WHERE account_id=? AND entry_type IN ('DEPOSIT','WITHDRAWAL','INITIAL_CAPITAL') AND occurred_at>?", (account_id, cutoff.isoformat()))
                    app = SQLiteTradingLedgerApplication(MemoryDatabase(db))
                    app._replay_effective_trades(db, account["project_id"], account_id)
                    held = db.execute("""SELECT i.* FROM positions p JOIN instruments i USING(instrument_id)
                        WHERE p.account_id=? AND CAST(p.quantity AS NUMERIC)>0""", (account_id,)).fetchall()
                    missing = [i["symbol"] for i in held if day not in prices.get(i["symbol"], {})]
                    if missing:
                        plan.skipped.append(f'{account["project_key"]} {day} 缺少当日收盘价：' + '、'.join(missing) + ' ' + ';'.join(errors.get(s, '') for s in missing))
                        continue
                    for instrument in held:
                        db.execute("DELETE FROM reference_prices WHERE instrument_id=?", (instrument["instrument_id"],))
                        app.record_reference_price(RecordReferencePriceCommand(
                            market=instrument["market"], venue=instrument["venue"], symbol=instrument["symbol"],
                            price=prices[instrument["symbol"]][day],
                            price_time=datetime.combine(date.fromisoformat(day), time(15), tzinfo=now.tzinfo),
                            source_name="腾讯历史不复权收盘价", request_id=f'backfill-{instrument["instrument_id"]}-{day}', actor="system:historical-valuation",
                        ))
                    app.record_daily_valuation(RecordDailyValuationCommand(
                        project_key=account["project_key"], actor="system:historical-valuation", valuation_time=cutoff,
                    ))
                    valuation = dict(db.execute("SELECT * FROM daily_account_valuations WHERE account_id=? AND valuation_date=?", (account_id, day)).fetchone())
                    positions = [dict(r) for r in db.execute("SELECT * FROM daily_position_valuations WHERE valuation_id=?", (valuation["valuation_id"],))]
                    plan.valuations.append((valuation, positions))
                    save_snapshot(base, valuation, positions)
                    base.commit()
                finally:
                    db.close()
        return plan
    finally:
        base.close()


def apply_plan(path: Path, plan: BackfillPlan) -> Path:
    if not plan.valuations:
        raise ValueError("没有可写入的完整估值")
    database = LedgerDatabase(path)
    backup_dir = path.parent.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"before-historical-valuation-{uuid4().hex}.sqlite3"
    with database.transaction() as db:
        if fingerprint(db) != plan.fingerprint:
            raise ValueError("账本在预演后发生变化，请重新预演")
        # A separate read connection captures the locked, pre-write database, including WAL.
        with database.read() as source:
            backup = sqlite3.connect(backup_path)
            try:
                source.backup(backup)
                if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("备份校验失败")
            finally:
                backup.close()
        for valuation, positions in plan.valuations:
            valuation_id = save_snapshot(db, valuation, positions)
            SQLiteTradingLedgerApplication._audit(
                db, project_id=valuation["project_id"], object_type="VALUATION", object_id=valuation_id,
                action="HISTORICAL_VALUATION_REBUILT", actor="system:historical-valuation",
                after={"valuation_date": valuation["valuation_date"], "equity": valuation["equity"], "source": "腾讯历史不复权收盘价"},
                occurred_at=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            )
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("补算后的数据库校验失败")
    return backup_path
