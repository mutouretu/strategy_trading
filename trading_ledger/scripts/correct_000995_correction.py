#!/usr/bin/env python3
"""One-off, audited correction for 000995.SZ; default is a read-only full rehearsal.

Run from trading_ledger. Keep preview_000995_correction.py beside this file.
Reuses existing snapshot prices, NOT today's quotes. Refuses incomplete or
inconsistent snapshots and unsupported reversal histories. Never initializes DB.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal as D
import json
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

try:
    from .preview_000995_correction import PROJECT_KEY, TARGET_ID, TZ, preview, replay, timestamp
except ImportError:
    from preview_000995_correction import PROJECT_KEY, TARGET_ID, TZ, preview, replay, timestamp

from trading_ledger.application.historical_valuation import fingerprint
from trading_ledger.domain.accounting import round_money
from trading_ledger.infrastructure.sqlite_application import SQLiteTradingLedgerApplication as App


ACTOR = "repair:000995-20260923"
REASON = "皇台酒业买入价 1.95 更正为 11.95；按原交易比例顺序重算后续人工交易"
KEYS = {
    "trade_records": ("trade_id",), "cash_ledger": ("cash_entry_id",),
    "position_lots": ("lot_id",), "positions": ("account_id", "instrument_id"),
    "accounts": ("account_id",), "reference_prices": ("reference_price_id",),
    "daily_account_valuations": ("valuation_id",),
    "daily_position_valuations": ("valuation_id", "instrument_id"),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_database(db):
    require(db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            and not db.execute("PRAGMA foreign_key_check").fetchall(), "数据库完整性校验失败")


@dataclass
class Change:
    table: str
    before: dict
    after: dict

    def write(self, db):
        keys = KEYS[self.table]
        where = " AND ".join(f"{key}=?" for key in keys)
        values = tuple(self.before[key] for key in keys)
        row = db.execute(f"SELECT * FROM {self.table} WHERE {where}", values).fetchone()
        require(row is not None and dict(row) == self.before, f"{self.table} 待修改记录发生变化")
        updates = {key: value for key, value in self.after.items() if value != self.before[key]}
        require(not set(updates) & set(keys), "不允许更改记录主键")
        db.execute(f"UPDATE {self.table} SET " + ",".join(f"{key}=?" for key in updates)
                   + f" WHERE {where}", (*updates.values(), *values))


@dataclass
class RepairPlan:
    source_hash: str
    report: dict
    project_id: int
    account_id: int
    changes: list[Change]
    snapshots: list[dict]
    created_at: str


def add_change(changes, table, row, **updates):
    before = dict(row)
    after = dict(before, **{key: str(value) if isinstance(value, D) else value
                           for key, value in updates.items()})
    if before != after:
        changes.append(Change(table, before, after))


def rebuild_snapshots(db, target, changes, *, target_id):
    account_id = target["account_id"]
    initial = D(db.execute("SELECT initial_capital FROM accounts WHERE account_id=?", (account_id,)).fetchone()[0])
    start = target["trade_time"][:10]
    peak = max((D(r[0]) for r in db.execute(
        "SELECT peak_equity FROM daily_account_valuations WHERE account_id=? AND valuation_date<?",
        (account_id, start))), default=D("0"))
    snapshots = []
    for row in db.execute("""SELECT * FROM daily_account_valuations
            WHERE account_id=? AND valuation_date>=? ORDER BY valuation_date""", (account_id, start)):
        day, cutoff = row["valuation_date"], timestamp(row["valuation_time"])
        require(cutoff.date().isoformat() == day, f"{day} 估值时间不符")
        require(not row["is_partial"] and not json.loads(row["missing_symbols_json"]),
                f"{day} 原估值不完整，需先核对历史价格")
        old_cash, old_positions, _ = replay(db, account_id, target_id, cutoff=cutoff)
        cash, positions, _ = replay(db, account_id, target_id, correct=True, cutoff=cutoff)
        require(old_cash == D(row["cash_balance"]), f"{day} 原估值现金与当时流水不一致")
        rows = {r["instrument_id"]: r for r in db.execute(
            "SELECT * FROM daily_position_valuations WHERE valuation_id=?", (row["valuation_id"],))}
        old_held = {key for key, value in old_positions.items() if value["quantity"] > 0}
        new_held = {key for key, value in positions.items() if value["quantity"] > 0}
        require(set(rows) == old_held == new_held, f"{day} 持仓集合改变或原快照缺少持仓，停止自动修复")
        old_market, market = D("0"), D("0")
        for instrument_id, position in rows.items():
            price = D(position["last_price"]) if position["last_price"] else D("0")
            require(not position["is_price_missing"] and price.is_finite() and price > 0,
                    f"{day} instrument_id={instrument_id} 缺少有效历史估值价")
            # Never silently reuse the known erroneous price as a historical close.
            require(not (instrument_id == target["instrument_id"] and price == D("1.95")),
                    f"{day} 皇台酒业估值价仍为 1.95，需取得正确历史价格后再修复")
            require(D(position["quantity"]) == old_positions[instrument_id]["quantity"],
                    f"{day} 原快照股数与历史交易不一致")
            old_value = round_money(D(position["quantity"]) * price)
            require(old_value == D(position["market_value"]), f"{day} 原快照市值不一致")
            old_market += old_value
            item = positions[instrument_id]
            value = round_money(item["quantity"] * price)
            market += value
            add_change(changes, "daily_position_valuations", position,
                       quantity=item["quantity"], average_cost=item["average_cost"],
                       market_value=value,
                       unrealized_pnl=round_money(value - item["quantity"] * item["average_cost"]))
        require(old_market == D(row["market_value"]) and old_cash + old_market == D(row["equity"]),
                f"{day} 原账户估值与持仓明细不一致")
        external = sum((D(r["amount"]) for r in db.execute(
            "SELECT amount,occurred_at FROM cash_ledger WHERE account_id=? AND entry_type IN ('DEPOSIT','WITHDRAWAL')",
            (account_id,)) if timestamp(r["occurred_at"]) <= cutoff), D("0"))
        require(external == D(row["external_net_flow"]), f"{day} 外部资金流与原估值不一致")
        equity = round_money(cash + market)
        pnl = round_money(equity - initial - external)
        peak = max(peak, equity)
        add_change(changes, "daily_account_valuations", row, cash_balance=cash,
                   market_value=market, equity=equity, pnl=pnl,
                   return_rate=pnl / initial if initial > 0 else None,
                   peak_equity=peak, drawdown=(equity - peak) / peak if peak > 0 else D("0"))
        snapshots.append({"date": day, "old_equity": row["equity"], "new_equity": str(equity)})
    return snapshots


def build_plan(path, *, target_id=TARGET_ID, project_key=PROJECT_KEY, now=None):
    path = Path(path).resolve()
    report = preview(path, target_id=target_id, project_key=project_key)
    with closing(sqlite3.connect(":memory:")) as db:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
            source.backup(db)
        db.row_factory = sqlite3.Row
        require(fingerprint(db) == report["database_fingerprint"], "账本在预演期间发生变化，请暂停服务后重试")
        target = dict(db.execute("SELECT * FROM trade_records WHERE trade_id=?", (target_id,)).fetchone())
        project_id, account_id = target["project_id"], target["account_id"]
        now = now or datetime.now(TZ)
        require(now.tzinfo is not None, "修复时间缺少时区")
        now = now.astimezone(TZ)
        now_text = now.isoformat()
        require(not db.execute("""SELECT 1 FROM trade_records WHERE account_id=?
                AND (record_status='REVERSED' OR reversal_of_trade_id IS NOT NULL)""", (account_id,)).fetchone(),
                "该账户存在冲正记录，不适用本次专用修复脚本")
        trades = {r["trade_id"]: dict(r) for r in db.execute(
            "SELECT * FROM trade_records WHERE account_id=? AND record_status='CONFIRMED'", (account_id,))}
        require(all(timestamp(r["trade_time"]) <= now for r in trades.values()), "存在未来交易，停止")
        old_trace, new_trace = {}, {}
        _, old_positions, old_results = replay(db, account_id, target_id, trace=old_trace)
        cash, positions, results = replay(db, account_id, target_id, correct=True, trace=new_trace)
        require(cash == report["cash_after"], "修复结果与预演不符")
        changes = []
        # The erroneous trade's manual fallback is not a market quote. Correct only
        # this exact attributable row; never refresh prices or alter real quotes.
        quotes = db.execute("""SELECT * FROM reference_prices WHERE instrument_id=?
            AND price_time=? AND source_name='人工成交'""", (target["instrument_id"], target["trade_time"])).fetchall()
        for quote in quotes:
            require(quote["is_manual"] == 1 and D(quote["price"]) == D("1.95")
                    and quote["created_by"] == target["created_by"]
                    and quote["collected_at"] == target["created_at"], "错误人工参考价无法唯一归属于目标交易")
            require(not db.execute("""SELECT 1 FROM trade_records WHERE instrument_id=? AND trade_time=?
                AND trade_id<>?""", (target["instrument_id"], target["trade_time"], target_id)).fetchone(),
                "同一时间另有该股票交易，不能自动改共享人工参考价")
            add_change(changes, "reference_prices", quote, price=D("11.95"))
            changes[-1].write(db)  # Only the in-memory pricing context is changed.
        for trade_id, value in results.items():
            original = trades[trade_id]
            require(D(original["realized_pnl"]) == old_results[trade_id]["realized_pnl"],
                    f"{trade_id} 原已实现盈亏与批次回放不一致")
            if not value["affected"]:
                continue
            metadata = json.loads(original["metadata_json"])
            equity = App._trade_equity_before(
                db, value["cash_before"], new_trace["holdings_before"][trade_id],
                original["instrument_id"], value["price"], original["trade_time"], original["created_at"])
            require(equity is not None and equity > 0, f"{trade_id} 缺少历史价格，不能重建总仓占比")
            metadata["_ledger_equity_before"] = str(equity)
            add_change(changes, "trade_records", original, price=value["price"], quantity=value["quantity"],
                       gross_amount=value["gross"], commission_amount=value["commission"],
                       stamp_tax_amount=value["stamp"], transfer_fee_amount=value["transfer"],
                       net_cash_amount=value["net_cash"], realized_pnl=value["realized_pnl"],
                       metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True))
        seen_trades = set()
        for row in db.execute("SELECT * FROM cash_ledger WHERE account_id=?", (account_id,)):
            require(row["reversal_of_entry_id"] is None, "存在冲正资金流水，停止")
            if row["entry_type"] in ("INITIAL_CAPITAL", "DEPOSIT", "WITHDRAWAL"):
                require(row["trade_id"] is None, "外部资金流水不应关联交易")
                require(D(row["balance_after"]) == old_trace["cash_flows"][row["cash_entry_id"]],
                        "原外部资金余额与时序回放不一致")
                add_change(changes, "cash_ledger", row,
                           balance_after=new_trace["cash_flows"][row["cash_entry_id"]])
                continue
            key = row["trade_id"]
            require(key in trades and key not in seen_trades and row["entry_type"] == trades[key]["side"],
                    "交易资金流水缺失、重复或类型不符")
            require(row["occurred_at"] == trades[key]["trade_time"]
                    and D(row["amount"]) == old_results[key]["net_cash"]
                    and D(row["balance_after"]) == old_results[key]["cash_after"], "原交易资金流水与回放不一致")
            seen_trades.add(key)
            add_change(changes, "cash_ledger", row, amount=results[key]["net_cash"],
                       balance_after=results[key]["cash_after"])
        require(seen_trades == set(trades), "有交易缺少资金流水")
        old_lots = {lot["source_trade_id"]: lot for batches in old_trace["lots"].values() for lot in batches}
        new_lots = {lot["source_trade_id"]: lot for batches in new_trace["lots"].values() for lot in batches}
        seen_lots = set()
        for row in db.execute("SELECT * FROM position_lots WHERE account_id=?", (account_id,)):
            key = row["source_trade_id"]
            require(key in old_lots and key not in seen_lots, "买入批次缺失、重复或无法识别")
            seen_lots.add(key)
            for column, value in (("original_quantity", old_lots[key]["original_quantity"]),
                                  ("remaining_quantity", old_lots[key]["quantity"]), ("unit_cost", old_lots[key]["cost"])):
                require(D(row[column]) == value, f"批次 {row['lot_id']} 原 {column} 与回放不一致")
            lot = new_lots[key]
            add_change(changes, "position_lots", row, original_quantity=lot["original_quantity"],
                       remaining_quantity=lot["quantity"], unit_cost=lot["cost"])
        require(seen_lots == set(old_lots), "部分原买入批次已缺失，不能保证 FIFO 次序")
        t_plus_one = db.execute("SELECT t_plus_one FROM projects WHERE project_id=?", (project_id,)).fetchone()[0]
        seen_positions = set()
        for row in db.execute("SELECT * FROM positions WHERE account_id=?", (account_id,)):
            key = row["instrument_id"]
            require(key in positions, "存在无法回放的持仓")
            seen_positions.add(key)
            item = positions[key]
            require((item["quantity"] > 0) == (D(row["quantity"]) > 0), "最终持仓状态改变，需单独核对归档")
            for column in ("quantity", "average_cost", "realized_pnl"):
                require(D(row[column]) == old_positions[key][column], f"原持仓 {key} 的 {column} 不一致")
            available = sum((lot["quantity"] for lot in new_trace["lots"][key]
                             if not t_plus_one or lot["day"] < now.date()), D("0"))
            price = D(row["last_price"]) if row["last_price"] is not None else None
            require(price is None or price.is_finite() and price > 0, "当前持仓价格无效")
            require(not (key == target["instrument_id"] and price == D("1.95")), "当前持仓仍使用错误价格，停止")
            value = round_money(item["quantity"] * price) if price is not None else None
            add_change(changes, "positions", row, **item, available_quantity=available,
                       market_value=value, unrealized_pnl=(
                           round_money(value - item["quantity"] * item["average_cost"]) if value is not None else None),
                       updated_at=now_text)
        require(seen_positions == set(positions), "持仓汇总缺失")
        account = db.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()
        add_change(changes, "accounts", account, cash_balance=cash, updated_at=now_text)
        snapshots = rebuild_snapshots(db, target, changes, target_id=target_id)
        # Restore the in-memory quote before applying the complete plan.
        db.rollback()
        for change in changes:
            change.write(db)
        check_database(db)
        final_cash, final_positions, _ = replay(db, account_id, target_id)
        require(final_cash == cash and final_positions == positions, "更正后全链路复核失败")
        return RepairPlan(report["database_fingerprint"], report, project_id, account_id, changes, snapshots, now_text)


def apply_plan(path, plan, expected_fingerprint):
    require(expected_fingerprint == plan.source_hash, "数据库与指定预演指纹不一致；未写入，请重新核对")
    path = Path(path).resolve()
    backup_path = path.parent / f"before-correct-000995-{uuid4().hex}.sqlite3"
    backup_verified = False
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        try:
            require(fingerprint(db) == plan.source_hash, "账本在预演后发生变化；未写入，请暂停服务后重试")
            # Backup with a separate reader while the write reservation prevents
            # another writer racing the fingerprint/backup/commit boundary.
            backup_path.touch(mode=0o600, exist_ok=False)
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(backup_path)) as backup:
                    source.backup(backup)
                    check_database(backup)
                    require(fingerprint(backup) == plan.source_hash, "备份与原数据库不一致，停止")
            backup_verified = True
            for change in plan.changes:
                change.write(db)
                App._audit(
                    db, project_id=plan.project_id, object_type=change.table,
                    object_id="/".join(str(change.before[key]) for key in KEYS[change.table]),
                    action="HISTORICAL_CHAIN_CORRECTED", actor=ACTOR,
                    before=change.before, after=change.after,
                    reason=REASON + f"；源指纹 {plan.source_hash}", occurred_at=plan.created_at)
            check_database(db)
            cash, positions, results = replay(db, plan.account_id, plan.report["target_trade_id"])
            require(cash == plan.report["cash_after"], "提交前资金复核失败")
            require(D(db.execute("SELECT cash_balance FROM accounts WHERE account_id=?",
                                 (plan.account_id,)).fetchone()[0]) == cash, "提交前账户余额不符")
            for row in db.execute("SELECT * FROM positions WHERE account_id=?", (plan.account_id,)):
                require(all(D(row[key]) == positions[row["instrument_id"]][key]
                            for key in ("quantity", "average_cost", "realized_pnl")), "提交前持仓复核失败")
            for change in plan.report["changes"]:
                result = results[change["after"]["trade_id"]]
                require(all(result[key] == change["after"][key] for key in
                            ("quantity", "price", "gross", "net_cash", "realized_pnl")), "提交前交易复核失败")
            db.commit()
        except Exception:
            db.rollback()
            if backup_verified:
                print(f"已回滚，保留修复前备份：{backup_path}", file=sys.stderr)
            raise
    return backup_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/trading_ledger.sqlite3"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-fingerprint")
    args = parser.parse_args()
    try:
        require(not args.apply or bool(args.expected_fingerprint), "--apply 必须提供已核对的 --expected-fingerprint")
        plan = build_plan(args.db)
        print("项目：", plan.report["project"])
        print("数据库校验指纹：", plan.source_hash)
        print("重算交易数：", len(plan.report["changes"]))
        print("当前现金：", plan.report["cash_before"], "→", plan.report["cash_after"])
        print("变更记录数：", dict(Counter(c.table for c in plan.changes)))
        print("更正后非零持仓：")
        for item in plan.report["positions"]:
            if item["after"]["quantity"] > 0:
                print(item["symbol"], item["after"]["quantity"], "股")
        print("已有估值重算（沿用原快照历史价格，包含现金节假日）：")
        for row in plan.snapshots:
            print(row["date"], row["old_equity"], "→", row["new_equity"])
        if args.apply:
            backup = apply_plan(args.db, plan, args.expected_fingerprint)
            print("正式修复已提交；备份：", backup)
            print("交易、资金流水、FIFO 批次、当前持仓、历史估值及总仓占比已修正，保留逐行审计。")
        else:
            print("完整预演成功：数据库未修改。正式执行需 --apply --expected-fingerprint 上述指纹。")
        return 0
    except (ValueError, sqlite3.Error, ArithmeticError) as error:
        print(f"停止，未提交修改：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
