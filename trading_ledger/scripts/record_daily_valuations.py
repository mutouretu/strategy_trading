#!/usr/bin/env python3
"""Refresh quotes and record today's valuations for active projects."""

import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_ledger.application.daily_valuation import run_daily_valuations
from trading_ledger.config import Settings
from trading_ledger.infrastructure.database import LedgerDatabase
from trading_ledger.infrastructure.sqlite_application import SQLiteTradingLedgerApplication


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()
    # A mistyped database path must not silently create an empty scheduled ledger.
    if not settings.database_path.is_file():
        logging.error("账本数据库不存在，请先打开账本创建项目：%s", settings.database_path)
        return 1
    application = SQLiteTradingLedgerApplication(
        LedgerDatabase(settings.database_path), timezone_name=settings.business_timezone
    )
    application.initialize()
    failures = run_daily_valuations(
        application,
        now=datetime.now(ZoneInfo(settings.business_timezone)),
        actor="system:daily-valuation",
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
