#!/usr/bin/env python3
"""Create an isolated August 2026 demo project with synthetic ledger data."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo


MODULE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = MODULE_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from trading_ledger.application.contracts import (  # noqa: E402
    AddTrackedInstrumentCommand,
    ConfirmManualTradeCommand,
    CreateProjectCommand,
    GetMonthlyStatisticsQuery,
    ListProjectsQuery,
    RecordDailyValuationCommand,
    RecordReferencePriceCommand,
    UpdateProjectCommand,
)
from trading_ledger.config import Settings  # noqa: E402
from trading_ledger.domain import TradeSide  # noqa: E402
from trading_ledger.infrastructure.database import LedgerDatabase  # noqa: E402
from trading_ledger.infrastructure.sqlite_application import (  # noqa: E402
    SQLiteTradingLedgerApplication,
)


PROJECT_NAME = "2026年8月模拟"
ACTOR = "local:august-demo"
TZ = ZoneInfo("Asia/Shanghai")

INSTRUMENTS = (
    ("600036.SH", "招商银行", "银行板块低估值与盈利稳定性观察"),
    ("300750.SZ", "宁德时代", "新能源龙头趋势与景气度观察"),
    ("000858.SZ", "五粮液", "消费龙头估值修复观察"),
)

TRADES = {
    3: (("600036.SH", TradeSide.BUY, "0.25", "42.10", "低估值区间首次建仓"),),
    7: (("300750.SZ", TradeSide.BUY, "0.30", "245.00", "放量突破后分批建仓"),),
    12: (("000858.SZ", TradeSide.BUY, "0.20", "122.00", "消费板块企稳试仓"),),
    18: (("600036.SH", TradeSide.SELL, "0.35", "44.20", "短期达到第一止盈位"),),
    21: (("600036.SH", TradeSide.BUY, "0.15", "44.00", "回踩支撑后补回仓位"),),
    25: (("300750.SZ", TradeSide.SELL, "0.50", "248.00", "波动放大减半控制风险"),),
    28: (("000858.SZ", TradeSide.SELL, "0.25", "127.00", "阶段目标达成部分止盈"),),
    31: (("300750.SZ", TradeSide.BUY, "0.10", "262.00", "月末趋势重新转强"),),
}

DAILY_PRICES = {
    3: ("42.10", "242.00", "120.00"),
    5: ("42.55", "244.00", "121.00"),
    7: ("42.30", "245.00", "120.80"),
    10: ("43.10", "250.00", "122.20"),
    12: ("42.75", "247.00", "122.00"),
    14: ("43.60", "252.00", "124.50"),
    18: ("44.20", "249.00", "123.00"),
    20: ("43.80", "254.00", "125.20"),
    21: ("44.00", "256.00", "126.00"),
    24: ("43.20", "251.00", "124.00"),
    25: ("42.85", "248.00", "123.20"),
    28: ("43.50", "258.00", "127.00"),
    31: ("44.10", "262.00", "129.00"),
}


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=TZ)


def seed(database_path: Path) -> str:
    application = SQLiteTradingLedgerApplication(LedgerDatabase(database_path))
    application.initialize()
    existing = next(
        (
            project
            for project in application.list_projects(ListProjectsQuery())
            if project.project_name == PROJECT_NAME
        ),
        None,
    )
    if existing is not None:
        return f"项目已存在，未重复写入：{existing.project_key}"

    project = application.create_project(
        CreateProjectCommand(
            project_name=PROJECT_NAME,
            initial_capital=Decimal("500000.00"),
            description="2026 年 8 月合成演示数据，不代表真实成交。",
            actor=ACTOR,
        )
    )
    application.update_project(
        UpdateProjectCommand(
            project_key=project.project_key,
            project_name=project.project_name,
            description=project.description,
            color_key="purple",
            actor=ACTOR,
        )
    )
    for symbol, name, source_text in INSTRUMENTS:
        application.add_tracking(
            AddTrackedInstrumentCommand(
                project_key=project.project_key,
                symbol=symbol,
                name=name,
                source_text=source_text,
                added_at=at(3, 9),
                actor=ACTOR,
            )
        )

    for day, prices in DAILY_PRICES.items():
        for symbol, side, ratio, price, signal in TRADES.get(day, ()):
            application.confirm_manual_trade(
                ConfirmManualTradeCommand(
                    project_key=project.project_key,
                    symbol=symbol,
                    side=side,
                    allocation_ratio=Decimal(ratio),
                    price=Decimal(price),
                    signal_text=signal,
                    trade_time=at(day, 10),
                    actor=ACTOR,
                )
            )
        for (symbol, _name, _source), price in zip(INSTRUMENTS, prices, strict=True):
            venue = "SSE" if symbol.endswith(".SH") else "SZSE"
            application.record_reference_price(
                RecordReferencePriceCommand(
                    market="CN_STOCK",
                    venue=venue,
                    symbol=symbol,
                    price=Decimal(price),
                    price_time=at(day, 15),
                    source_name="8月模拟行情",
                    request_id=f"august-demo-price-202608{day:02d}-{symbol[:6]}",
                    actor=ACTOR,
                )
            )
        application.record_daily_valuation(
            RecordDailyValuationCommand(
                project_key=project.project_key,
                valuation_time=at(day, 15, 5),
                actor=ACTOR,
            )
        )

    report = application.monthly_statistics(
        GetMonthlyStatisticsQuery(project_key=project.project_key, month="2026-08")
    )
    if application.integrity_check():
        raise RuntimeError("账本完整性检查未通过。")
    return (
        f"已创建 {project.project_key}：{report.buy_count} 笔买入、"
        f"{report.sell_count} 笔卖出、{len(report.valuation_points)} 个估值日，"
        f"期末权益 {report.closing_capital:.2f} CNY"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=Settings.from_env().database_path,
        help="目标 SQLite 文件，默认使用 TRADING_LEDGER_DB_PATH 配置。",
    )
    args = parser.parse_args()
    print(seed(args.database.expanduser().resolve()))


if __name__ == "__main__":
    main()
