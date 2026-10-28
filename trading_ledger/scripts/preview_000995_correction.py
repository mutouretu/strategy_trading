#!/usr/bin/env python3
"""Read-only rehearsal for the 2026-09-23 000995.SZ price correction.

Run from the trading_ledger directory. This script intentionally has no apply mode.
It neither initializes the database nor changes trades, snapshots, or market data.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from zoneinfo import ZoneInfo

source_root = Path.cwd() / "src"
if not (source_root / "trading_ledger").is_dir():
    source_root = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(source_root))

from trading_ledger.domain.accounting import calculate_buy, calculate_sell, round_money
from trading_ledger.infrastructure.database import DATABASE_IDENTITY


TARGET_ID = "trade-4bc96cbed9d0445583267319b25c1935"
PROJECT_KEY = "project-a576c61e5ac5"
TZ = ZoneInfo("Asia/Shanghai")


def timestamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError(f"时间缺少时区，停止预演：{value}")
    return result.astimezone(TZ)


def replay(db, account_id, target_id, *, correct=False, cutoff=None, trace=None):
    trades = db.execute("""SELECT t.rowid AS record_order, t.*, i.symbol
        FROM trade_records t JOIN instruments i USING(instrument_id)
        WHERE t.account_id=? AND t.record_status='CONFIRMED'
          AND t.reversal_of_trade_id IS NULL""", (account_id,)).fetchall()
    flows = db.execute("""SELECT rowid AS record_order, * FROM cash_ledger
        WHERE account_id=? AND entry_type IN ('INITIAL_CAPITAL','DEPOSIT','WITHDRAWAL')""",
        (account_id,)).fetchall()
    original_lot_ids = {row["source_trade_id"]: row["lot_id"] for row in db.execute(
        "SELECT source_trade_id,lot_id FROM position_lots WHERE account_id=?", (account_id,))}
    events = []
    for row in flows:
        when = datetime.min.replace(tzinfo=TZ) if row["entry_type"] == "INITIAL_CAPITAL" else timestamp(row["occurred_at"])
        events.append((when, 0, when, row["record_order"], "cash", row))
    for row in trades:
        events.append((timestamp(row["trade_time"]), 1, timestamp(row["created_at"]), row["record_order"], "trade", row))
    cash = D("0")
    lots = defaultdict(list)
    realized = defaultdict(lambda: D("0"))
    results = {}
    affected = False
    for when, _, _, _, kind, row in sorted(events, key=lambda e: e[:4]):
        if cutoff is not None and when > cutoff:
            continue
        if kind == "cash":
            cash = round_money(cash + D(row["amount"]))
            if cash < 0:
                raise ValueError(f"{row['occurred_at']} 外部资金流水导致负现金")
            if trace is not None:
                trace.setdefault("cash_flows", {})[row["cash_entry_id"]] = cash
            continue
        trade_id, instrument_id = row["trade_id"], row["instrument_id"]
        affected = affected or trade_id == target_id
        metadata = json.loads(row["metadata_json"] or "{}")
        t_plus_one = metadata.get("_ledger_t_plus_one", False)
        if not isinstance(t_plus_one, bool):
            raise ValueError(f"{trade_id} 的历史 T+1 标记无效")
        eligible = sorted((lot for lot in lots[instrument_id]
                           if lot["quantity"] > 0 and (not t_plus_one or lot["day"] < when.date())),
                          key=lambda lot: lot["fifo"])
        sellable = sum((lot["quantity"] for lot in eligible), D("0"))
        before_cash = cash
        if trace is not None:
            trace.setdefault("holdings_before", {})[trade_id] = {
                key: sum((lot["quantity"] for lot in batches), D("0"))
                for key, batches in lots.items()
            }
        price = D("11.95") if correct and trade_id == target_id else D(row["price"])
        if correct and affected:
            if row["execution_source"] != "MANUAL" or row["allocation_ratio"] is None:
                raise ValueError(f"{trade_id} 并非带比例的人工交易，不能擅自重算")
            ratio = D(row["allocation_ratio"])
            basis = "AVAILABLE_CASH" if row["side"] == "BUY" else "SELLABLE_POSITION"
            if not ratio.is_finite() or not D("0") < ratio <= 1 or row["ratio_basis"] != basis:
                raise ValueError(f"{trade_id} 的比例或计算基数无效")
            try:
                calculation = (calculate_buy(cash, ratio, price) if row["side"] == "BUY"
                               else calculate_sell(sellable, ratio, price))
            except ValueError as error:
                raise ValueError(f"{row['trade_time']} {row['symbol']} {trade_id} 无法重算：{error}") from error
            quantity, gross = calculation.quantity, calculation.gross_amount
            commission, stamp, transfer = calculation.commission_amount, calculation.stamp_tax_amount, calculation.transfer_fee_amount
            net = calculation.net_cash_amount
        else:
            quantity, gross = D(row["quantity"]), D(row["gross_amount"])
            commission, stamp, transfer = (D(row[name]) for name in ("commission_amount", "stamp_tax_amount", "transfer_fee_amount"))
            net = D(row["net_cash_amount"])
            expected_net = -(gross + commission + stamp + transfer) if row["side"] == "BUY" else gross - commission - stamp - transfer
            if gross != round_money(quantity * price) or net != expected_net:
                raise ValueError(f"原交易金额与税费不一致，停止：{trade_id}")
        if quantity <= 0:
            raise ValueError(f"成交数量必须大于零：{trade_id}")
        profit = D("0")
        if row["side"] == "BUY":
            lots[instrument_id].append({
                "quantity": quantity, "cost": -net / quantity, "day": when.date(),
                "fifo": (timestamp(row["created_at"]), original_lot_ids.get(trade_id, trade_id)),
                "source_trade_id": trade_id, "original_quantity": quantity,
            })
        else:
            if quantity > sellable:
                raise ValueError(f"{trade_id} 卖出 {quantity} 股超过当时可卖 {sellable} 股")
            remaining, cost = quantity, D("0")
            for lot in eligible:
                consumed = min(remaining, lot["quantity"])
                cost += consumed * lot["cost"]
                lot["quantity"] -= consumed
                remaining -= consumed
                if remaining == 0:
                    break
            profit = round_money(net - round_money(cost))
            realized[instrument_id] += profit
        cash = round_money(cash + net)
        if cash < 0:
            raise ValueError(f"{row['trade_time']} {row['symbol']} 交易后现金为负：{cash}")
        results[trade_id] = {
            "trade_id": trade_id, "time": row["trade_time"], "symbol": row["symbol"], "side": row["side"],
            "ratio": row["allocation_ratio"], "t_plus_one": t_plus_one, "affected": affected,
            "price": price, "quantity": quantity, "gross": gross, "commission": commission,
            "stamp": stamp, "transfer": transfer, "net_cash": net,
            "realized_pnl": profit, "cash_before": before_cash, "cash_after": cash,
        }
    positions = {}
    for instrument_id, batches in lots.items():
        quantity = sum((lot["quantity"] for lot in batches), D("0"))
        cost = sum((lot["quantity"] * lot["cost"] for lot in batches), D("0"))
        positions[instrument_id] = {"quantity": quantity, "average_cost": cost / quantity if quantity else D("0"),
                                    "realized_pnl": round_money(realized[instrument_id])}
    if trace is not None:
        trace["lots"] = dict(lots)
    return cash, positions, results


def preview(path, *, target_id=TARGET_ID, project_key=PROJECT_KEY):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        metadata = dict(db.execute("SELECT key,value FROM ledger_metadata"))
        if metadata.get("database_identity") != DATABASE_IDENTITY or metadata.get("schema_version") not in {"4", "5", "6"}:
            raise ValueError("数据库身份或版本不符，要求交易账本 Schema v4/v5/v6")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("数据库完整性检查失败")
        target = db.execute("""SELECT t.*, p.project_key, p.project_name, i.symbol
            FROM trade_records t JOIN projects p USING(project_id) JOIN instruments i USING(instrument_id)
            WHERE t.trade_id=?""", (target_id,)).fetchone()
        if target is None or any((target["project_key"] != project_key, target["symbol"] != "000995.SZ",
                                  target["side"] != "BUY", target["record_status"] != "CONFIRMED",
                                  target["reversal_of_trade_id"] is not None,
                                  target["trade_time"] != "2026-09-23T10:55:18+08:00",
                                  D(target["price"]) != D("1.95"), D(target["quantity"]) != D("247500"),
                                  D(target["allocation_ratio"] or "0") != D("0.17"))):
            raise ValueError("目标交易与已确认的错误记录不符，可能已更正，停止")
        account_id = target["account_id"]
        old_cash, old_positions, old_trades = replay(db, account_id, target_id)
        actual_cash = D(db.execute("SELECT cash_balance FROM accounts WHERE account_id=?", (account_id,)).fetchone()[0])
        if old_cash != actual_cash:
            raise ValueError(f"原记录回放现金 {old_cash} 与现存现金 {actual_cash} 不一致，需先核对")
        actual_positions = {row["instrument_id"]: D(row["quantity"]) for row in db.execute(
            "SELECT instrument_id,quantity FROM positions WHERE account_id=?", (account_id,))}
        for instrument_id in set(old_positions) | set(actual_positions):
            if old_positions.get(instrument_id, {}).get("quantity", D("0")) != actual_positions.get(instrument_id, D("0")):
                raise ValueError(f"原记录回放持仓与现存持仓不一致：instrument_id={instrument_id}")
        if old_trades[target_id]["cash_before"] != D("2839541.01"):
            raise ValueError("目标交易前的资金与服务器已提供的流水不一致，停止")
        new_cash, positions, new_trades = replay(db, account_id, target_id, correct=True)
        symbols = {row["instrument_id"]: row["symbol"] for row in db.execute("SELECT instrument_id,symbol FROM instruments")}
        dates = [row[0] for row in db.execute("""SELECT valuation_date FROM daily_account_valuations
            WHERE account_id=? AND valuation_date>='2026-09-23' ORDER BY valuation_date""", (account_id,))]
        return {
            "project": target["project_name"], "project_key": project_key, "target_trade_id": target_id,
            "database_fingerprint": hashlib.sha256("\n".join(db.iterdump()).encode()).hexdigest(),
            "cash_before": old_cash, "cash_after": new_cash, "cash_delta": new_cash - old_cash,
            "changes": [{"before": old_trades[key], "after": row} for key, row in new_trades.items() if row["affected"]],
            "positions": [{"symbol": symbols[key], "before": old_positions.get(key), "after": row}
                          for key, row in positions.items()],
            "valuation_dates_to_rebuild": dates,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/trading_ledger.sqlite3"))
    args = parser.parse_args()
    try:
        report = preview(args.db)
    except (ValueError, sqlite3.Error) as error:
        print(f"预演停止，数据库未修改：{error}", file=sys.stderr)
        return 1
    print("只读预演；不修改交易、资金、持仓或估值。项目：", report["project"])
    print("数据库校验指纹：", report["database_fingerprint"])
    print("重算交易数（含错误买入）：", len(report["changes"]))
    for change in report["changes"]:
        before, after = change["before"], change["after"]
        print(json.dumps({"交易ID": after["trade_id"], "时间": after["time"], "股票": after["symbol"],
                          "操作": after["side"], "比例": after["ratio"], "T+1": after["t_plus_one"],
                          "原价格": before["price"], "新价格": after["price"],
                          "原股数": before["quantity"], "新股数": after["quantity"],
                          "原净现金变动": before["net_cash"], "新净现金变动": after["net_cash"],
                          "新手续费": after["commission"], "新印花税": after["stamp"], "新过户费": after["transfer"],
                          "新已实现盈亏": after["realized_pnl"], "更正后交易前现金": after["cash_before"],
                          "更正后交易后现金": after["cash_after"]}, ensure_ascii=False, default=str))
    print("当前现金：", report["cash_before"], "→", report["cash_after"], "差额：", report["cash_delta"])
    print("当前持仓及累计已实现盈亏预演：")
    for position in report["positions"]:
        print(json.dumps(position, ensure_ascii=False, default=str))
    print("待重算的已有估值日期：", report["valuation_dates_to_rebuild"])
    print("预演成功：数据库未修改。尚未修复历史估值及总仓占比；请先核对输出，再进行正式修复。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
