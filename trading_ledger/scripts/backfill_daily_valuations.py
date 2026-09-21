#!/usr/bin/env python3
"""Explicit historical replay. Preview by default; --apply backs up before writing valuations."""
import argparse
from collections import Counter
from datetime import date, datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from trading_ledger.application.historical_valuation import build_plan, apply_plan
from trading_ledger.config import Settings
from trading_ledger.infrastructure.history_quotes import HistoricalQuoteProvider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    now = datetime.now(ZoneInfo(settings.business_timezone))
    plan = build_plan(settings.database_path, args.start, args.end or now.date(), HistoricalQuoteProvider(), now)
    for valuation, _ in plan.valuations:
        print(valuation["account_id"], valuation["valuation_date"], "现金", valuation["cash_balance"], "持仓市值", valuation["market_value"], "总权益", valuation["equity"])
    print("完整估值数：", dict(Counter(v["account_id"] for v, _ in plan.valuations)))
    for message in plan.skipped:
        print("跳过：", message)
    if args.apply:
        print("已写入估值；备份：", apply_plan(settings.database_path, plan))
    else:
        print("预演完成，账本未修改。确认后加 --apply 写入。")


if __name__ == "__main__":
    main()
