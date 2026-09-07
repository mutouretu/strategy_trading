"""Scheduled end-of-day valuation, independent of any browser session."""

from __future__ import annotations

import logging
from datetime import datetime, time

from .contracts import (
    ListPositionsQuery,
    ListProjectsQuery,
    RecordDailyValuationCommand,
    RefreshTrackingPricesCommand,
)
from .service import TradingLedgerApplication


LOGGER = logging.getLogger(__name__)


def run_daily_valuations(
    application: TradingLedgerApplication, *, now: datetime, actor: str
) -> int:
    """Return the number of failed projects; `now` uses the business timezone."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must include the business timezone")
    if now.weekday() >= 5 or now.time() < time(15, 15):
        LOGGER.info("未到工作日 15:15，跳过自动估值。")
        return 0

    failures = 0
    for project in application.list_projects(ListProjectsQuery(include_archived=False)):
        try:
            positions = application.list_positions(ListPositionsQuery(project.project_key))
            if positions:
                refresh = application.refresh_tracking_prices(
                    RefreshTrackingPricesCommand(project_key=project.project_key, actor=actor)
                )
                failed_holdings = sorted(
                    {item.symbol for item in positions} & set(refresh.failed_symbols)
                )
                if failed_holdings:
                    raise ValueError("持仓行情刷新失败：" + "、".join(failed_holdings))
            valuation = application.record_daily_valuation(
                RecordDailyValuationCommand(
                    project_key=project.project_key,
                    actor=actor,
                    require_fresh_prices=True,
                )
            )
            LOGGER.info(
                "%s %s 估值已记录，总权益 %s",
                project.project_name, valuation.valuation_date, valuation.equity,
            )
        except Exception:
            failures += 1
            LOGGER.exception("%s 自动估值失败，继续处理其他项目。", project.project_name)
    return failures
