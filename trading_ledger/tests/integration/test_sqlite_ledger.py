from __future__ import annotations

import tempfile
import unittest
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from trading_ledger.application.contracts import (
    AddTrackedInstrumentCommand,
    ApplicationError,
    ArchiveTrackedInstrumentCommand,
    ConfirmManualTradeCommand,
    CreateProjectCommand,
    ErrorCode,
    GetAccountSummaryQuery,
    GetMonthlyStatisticsQuery,
    ListOperationHistoryQuery,
    ListPositionsQuery,
    ListProjectsQuery,
    ListStatisticsMonthsQuery,
    ListTrackingQuery,
    PreviewManualTradeCommand,
    RecordCashEntryCommand,
    RecordConfirmedTradeCommand,
    RecordDailyValuationCommand,
    RecordReferencePriceCommand,
    RefreshTrackingPricesCommand,
    ReverseTradeCommand,
    UpdateProjectCommand,
    UpdateTrackedInstrumentCommand,
)
from trading_ledger.domain import (
    CashEntryType,
    ExecutionSource,
    OperationKind,
    TradeSide,
    TrackingStatus,
)
from trading_ledger.infrastructure.database import (
    DATABASE_IDENTITY,
    MIGRATION_PATHS,
    DatabaseIdentityError,
    LedgerDatabase,
)
from trading_ledger.infrastructure.sqlite_application import (
    SQLiteTradingLedgerApplication,
)
from trading_ledger.infrastructure.quotes import RealtimeQuote


TZ = ZoneInfo("Asia/Shanghai")


class SQLiteLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "ledger.sqlite3"
        self.application = SQLiteTradingLedgerApplication(
            LedgerDatabase(self.database_path)
        )
        self.application.initialize()
        self.project = self.application.create_project(
            CreateProjectCommand(
                project_name="长线组合",
                initial_capital=Decimal("100000.00"),
                actor="test-user",
            )
        )
        self.tracking = self.application.add_tracking(
            AddTrackedInstrumentCommand(
                project_key=self.project.project_key,
                symbol="600000",
                name="浦发银行",
                source_text="基本面观察",
                actor="test-user",
                added_at=datetime(2026, 9, 2, 9, 0, tzinfo=TZ),
            )
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _buy(self, trade_time: datetime | None = None):
        return self.application.confirm_manual_trade(
            ConfirmManualTradeCommand(
                project_key=self.project.project_key,
                symbol="600000.SH",
                side=TradeSide.BUY,
                allocation_ratio=Decimal("0.50"),
                price=Decimal("10.00"),
                signal_text="突破平台",
                actor="test-user",
                trade_time=trade_time or datetime(2026, 9, 2, 10, 0, tzinfo=TZ),
            )
        )

    def _sell(self):
        return self.application.confirm_manual_trade(
            ConfirmManualTradeCommand(
                project_key=self.project.project_key,
                symbol="600000.SH",
                side=TradeSide.SELL,
                allocation_ratio=Decimal("0.50"),
                price=Decimal("11.00"),
                signal_text="达到止盈位",
                actor="test-user",
                trade_time=datetime(2026, 9, 3, 10, 0, tzinfo=TZ),
            )
        )

    def test_new_database_starts_without_default_business_project(self) -> None:
        other_path = Path(self.temp_dir.name) / "empty.sqlite3"
        application = SQLiteTradingLedgerApplication(LedgerDatabase(other_path))
        application.initialize()
        self.assertEqual(application.list_projects(ListProjectsQuery()), ())

    def test_project_account_and_tracking_persist_after_restart(self) -> None:
        restarted = SQLiteTradingLedgerApplication(LedgerDatabase(self.database_path))
        restarted.initialize()
        projects = restarted.list_projects(ListProjectsQuery())
        tracking = restarted.list_tracking(
            ListTrackingQuery(project_key=self.project.project_key)
        )
        self.assertEqual(projects[0].project_name, "长线组合")
        self.assertEqual(projects[0].initial_capital, Decimal("100000.00"))
        self.assertEqual(tracking.rows[0].source_text, "基本面观察")

    def test_project_color_can_be_changed_and_persists(self) -> None:
        updated = self.application.update_project(
            UpdateProjectCommand(
                project_key=self.project.project_key,
                project_name=self.project.project_name,
                description=self.project.description,
                color_key="orange",
                actor="test-user",
            )
        )
        restarted = SQLiteTradingLedgerApplication(LedgerDatabase(self.database_path))
        restarted.initialize()
        project = restarted.list_projects(ListProjectsQuery())[0]
        self.assertEqual(updated.color_key, "orange")
        self.assertEqual(project.color_key, "orange")

    def test_editing_source_is_persisted(self) -> None:
        self.application.update_tracking(
            UpdateTrackedInstrumentCommand(
                project_key=self.project.project_key,
                tracking_id=self.tracking.tracking_id,
                symbol="600000.SH",
                name="浦发银行",
                source_text="更新后的观察说明",
                actor="test-user",
            )
        )
        restarted = SQLiteTradingLedgerApplication(LedgerDatabase(self.database_path))
        restarted.initialize()
        row = restarted.list_tracking(
            ListTrackingQuery(project_key=self.project.project_key)
        ).rows[0]
        self.assertEqual(row.source_text, "更新后的观察说明")

    def test_code_only_tracking_is_normalized_and_name_is_filled_by_quotes(self) -> None:
        provider = Mock()
        self.application.quote_provider = provider
        for code, normalized in (
            ("300377", "300377.SZ"), ("600036", "600036.SH"), ("920001", "920001.BJ")
        ):
            with self.subTest(code=code):
                row = self.application.add_tracking(AddTrackedInstrumentCommand(
                    project_key=self.project.project_key, symbol=code, name="",
                    source_text="只填代码", actor="test-user",
                ))
                self.assertEqual(row.symbol, normalized)
                self.assertEqual(row.name, "")
                provider.fetch_many.return_value = ({
                    normalized: RealtimeQuote(
                        normalized, "行情返回名称", Decimal("12.34"),
                        datetime(2026, 9, 8, 15, tzinfo=TZ), "测试行情",
                    )
                }, {})
                self.application.refresh_tracking_prices(RefreshTrackingPricesCommand(
                    self.project.project_key, "test-user"
                ))
                restarted = SQLiteTradingLedgerApplication(LedgerDatabase(self.database_path))
                result = restarted.list_tracking(ListTrackingQuery(
                    self.project.project_key, keyword=code
                )).rows[0]
                self.assertEqual(result.name, "行情返回名称")
                self.assertEqual(result.reference_price, Decimal("12.34"))

    def test_empty_name_does_not_erase_shared_instrument_name(self) -> None:
        second = self.application.create_project(CreateProjectCommand(
            "另一个项目", Decimal("10000"), "test-user"
        ))
        row = self.application.add_tracking(AddTrackedInstrumentCommand(
            second.project_key, "600000", "", "新的来源", "test-user"
        ))
        self.assertEqual(row.name, "浦发银行")
        updated = self.application.update_tracking(UpdateTrackedInstrumentCommand(
            second.project_key, row.tracking_id, "600000", "", "更新来源", "test-user"
        ))
        self.assertEqual(updated.name, "浦发银行")
        original = self.application.list_tracking(ListTrackingQuery(self.project.project_key))
        self.assertEqual(original.rows[0].name, "浦发银行")

    def test_quote_failure_keeps_code_only_tracking_editable(self) -> None:
        row = self.application.add_tracking(AddTrackedInstrumentCommand(
            self.project.project_key, "300377", "", "待刷新", "test-user"
        ))
        provider = Mock()
        provider.fetch_many.return_value = ({}, {"300377.SZ": "请求失败"})
        self.application.quote_provider = provider
        result = self.application.refresh_tracking_prices(RefreshTrackingPricesCommand(
            self.project.project_key, "test-user"
        ))
        self.assertIn("300377.SZ", result.failed_symbols)
        updated = self.application.update_tracking(UpdateTrackedInstrumentCommand(
            self.project.project_key, row.tracking_id, "300377", "", "更新来源", "test-user"
        ))
        self.assertEqual(updated.symbol, "300377.SZ")
        self.assertEqual(updated.name, "")
        self.assertEqual(updated.source_text, "更新来源")

    def test_buy_uses_cash_percentage_and_charges_commission(self) -> None:
        trade = self._buy()
        summary = self.application.account_summary(
            GetAccountSummaryQuery(self.project.project_key)
        )
        positions = self.application.list_positions(
            ListPositionsQuery(self.project.project_key)
        )
        self.assertEqual(trade.quantity, Decimal("4900"))
        self.assertEqual(trade.commission_amount, Decimal("5.00"))
        self.assertEqual(summary.cash_balance, Decimal("50995.00"))
        self.assertEqual(positions[0].quantity, Decimal("4900"))
        self.assertEqual(self.application.integrity_check(), ())

    def test_same_day_sell_uses_full_position(self) -> None:
        self._buy()
        trade = self.application.confirm_manual_trade(
            ConfirmManualTradeCommand(
                project_key=self.project.project_key,
                symbol="600000.SH",
                side=TradeSide.SELL,
                allocation_ratio=Decimal("0.50"),
                price=Decimal("11.00"),
                signal_text="当日卖出",
                actor="test-user",
                trade_time=datetime(2026, 9, 2, 14, 0, tzinfo=TZ),
            )
        )
        self.assertEqual(trade.quantity, Decimal("2400"))
        self.assertEqual(trade.stamp_tax_amount, Decimal("26.40"))
        self.assertEqual(trade.transfer_fee_amount, Decimal("0.26"))
        self.assertEqual(trade.net_cash_amount, Decimal("26368.34"))

    def test_failed_trade_rolls_back_without_cash_or_trade_changes(self) -> None:
        with self.assertRaises(ApplicationError):
            self.application.preview_manual_trade(
                PreviewManualTradeCommand(
                    project_key=self.project.project_key,
                    symbol="600000.SH",
                    side=TradeSide.BUY,
                    allocation_ratio=Decimal("0.01"),
                    price=Decimal("1000.00"),
                    signal_text="预算不足",
                )
            )
        summary = self.application.account_summary(
            GetAccountSummaryQuery(self.project.project_key)
        )
        history = self.application.operation_history(
            ListOperationHistoryQuery(project_key=self.project.project_key)
        )
        self.assertEqual(summary.cash_balance, Decimal("100000.00"))
        self.assertEqual(history.total_count, 1)

    def test_external_trade_request_is_idempotent(self) -> None:
        command = RecordConfirmedTradeCommand(
            project_key=self.project.project_key,
            request_id="request-1",
            external_trade_id="fill-1",
            account_code="primary-cny",
            market="CN_STOCK",
            venue="SSE",
            symbol="600000.SH",
            side=TradeSide.BUY,
            trade_time=datetime(2026, 9, 2, 10, 0, tzinfo=TZ),
            quantity=Decimal("100"),
            price=Decimal("10.00"),
            signal_text="自动交易成交",
            actor="test-client",
            execution_source=ExecutionSource.AUTO_TRADING,
        )
        first = self.application.record_confirmed_trade(command)
        second = self.application.record_confirmed_trade(command)
        self.assertEqual(first.trade_id, second.trade_id)
        self.assertEqual(
            self.application.operation_history(
                ListOperationHistoryQuery(project_key=self.project.project_key)
            ).total_count,
            2,
        )

    def test_concurrent_buys_cannot_make_cash_negative(self) -> None:
        def buy_once(index: int):
            try:
                return self.application.confirm_manual_trade(
                    ConfirmManualTradeCommand(
                        project_key=self.project.project_key,
                        symbol="600000.SH",
                        side=TradeSide.BUY,
                        allocation_ratio=Decimal("1"),
                        price=Decimal("10.00"),
                        signal_text=f"并发买入 {index}",
                        actor="test-user",
                        trade_time=datetime(2026, 9, 2, 10, index, tzinfo=TZ),
                    )
                )
            except ApplicationError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(buy_once, (1, 2)))
        summary = self.application.account_summary(
            GetAccountSummaryQuery(self.project.project_key)
        )
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertGreaterEqual(summary.cash_balance, 0)
        self.assertEqual(self.application.integrity_check(), ())

    def test_operation_history_uses_observation_source_and_trade_signal(self) -> None:
        self._buy()
        result = self.application.operation_history(
            ListOperationHistoryQuery(project_key=self.project.project_key)
        )
        self.assertEqual(result.total_count, 2)
        by_kind = {row.operation_kind: row for row in result.rows}
        self.assertEqual(by_kind[OperationKind.TRACKING].source_or_signal, "基本面观察")
        self.assertEqual(by_kind[OperationKind.TRADE].source_or_signal, "突破平台")

    def test_project_data_is_isolated(self) -> None:
        second = self.application.create_project(
            CreateProjectCommand(
                project_name="短线组合",
                initial_capital=Decimal("50000.00"),
                actor="test-user",
            )
        )
        second_tracking = self.application.list_tracking(
            ListTrackingQuery(project_key=second.project_key)
        )
        self.assertEqual(second_tracking.rows, ())
        self.assertEqual(second_tracking.account.cash_balance, Decimal("50000.00"))

    def test_cash_flow_is_not_counted_as_profit(self) -> None:
        self.application.record_cash_entry(
            RecordCashEntryCommand(
                project_key=self.project.project_key,
                account_code="primary-cny",
                entry_type=CashEntryType.DEPOSIT,
                amount=Decimal("10000.00"),
                occurred_at=datetime(2026, 9, 2, 8, 0, tzinfo=TZ),
                request_id="deposit-1",
                note="追加资金",
                actor="test-user",
            )
        )
        valuation = self.application.record_daily_valuation(
            RecordDailyValuationCommand(
                project_key=self.project.project_key,
                actor="test-user",
                valuation_time=datetime(2026, 9, 2, 15, 0, tzinfo=TZ),
            )
        )
        self.assertEqual(valuation.external_net_flow, Decimal("10000"))
        self.assertEqual(valuation.pnl, Decimal("0.00"))

    def test_cash_flow_is_not_counted_as_daily_volatility(self) -> None:
        self.application.record_daily_valuation(
            RecordDailyValuationCommand(
                project_key=self.project.project_key,
                actor="test-user",
                valuation_time=datetime(2026, 9, 2, 15, 0, tzinfo=TZ),
            )
        )
        self.application.record_cash_entry(
            RecordCashEntryCommand(
                project_key=self.project.project_key,
                account_code="primary-cny",
                entry_type=CashEntryType.DEPOSIT,
                amount=Decimal("10000.00"),
                occurred_at=datetime(2026, 9, 3, 8, 0, tzinfo=TZ),
                request_id="deposit-volatility",
                note="追加资金",
                actor="test-user",
            )
        )
        self.application.record_daily_valuation(
            RecordDailyValuationCommand(
                project_key=self.project.project_key,
                actor="test-user",
                valuation_time=datetime(2026, 9, 3, 15, 0, tzinfo=TZ),
            )
        )
        report = self.application.monthly_statistics(
            GetMonthlyStatisticsQuery(self.project.project_key, "2026-09")
        )
        self.assertEqual(report.pnl, Decimal("0.00"))
        self.assertEqual(report.annualized_volatility, Decimal("0.0"))

    def test_reversal_restores_cash_and_position_and_keeps_history(self) -> None:
        self._buy()
        sell = self._sell()
        reversal = self.application.reverse_trade(
            ReverseTradeCommand(
                project_key=self.project.project_key,
                trade_id=sell.trade_id,
                request_id="reverse-1",
                reason="录入错误",
                actor="test-user",
            )
        )
        summary = self.application.account_summary(
            GetAccountSummaryQuery(self.project.project_key)
        )
        positions = self.application.list_positions(
            ListPositionsQuery(self.project.project_key)
        )
        self.assertEqual(reversal.reversal_of_trade_id, sell.trade_id)
        self.assertEqual(summary.cash_balance, Decimal("50995.00"))
        self.assertEqual(positions[0].quantity, Decimal("4900"))
        self.assertEqual(self.application.integrity_check(), ())

    def _sell_all(self, price: str, trade_time: datetime):
        return self.application.confirm_manual_trade(
            ConfirmManualTradeCommand(
                project_key=self.project.project_key,
                symbol="600000.SH",
                side=TradeSide.SELL,
                allocation_ratio=Decimal("1"),
                price=Decimal(price),
                signal_text="清仓",
                actor="test-user",
                trade_time=trade_time,
            )
        )

    def test_closed_return_includes_fees_and_multiple_round_trips(self) -> None:
        total_cost = Decimal("0")
        total_proceeds = Decimal("0")
        for day, price in ((3, "9"), (5, "12")):
            with self.subTest(sell_price=price):
                buy = self._buy(datetime(2026, 9, day - 1, 10, tzinfo=TZ))
                sell = self._sell_all(price, datetime(2026, 9, day, 10, tzinfo=TZ))
                total_cost -= buy.net_cash_amount
                total_proceeds += sell.net_cash_amount
                self.application.record_daily_valuation(
                    RecordDailyValuationCommand(
                        project_key=self.project.project_key,
                        actor="test-user",
                        valuation_time=datetime(2026, 9, day, 15, tzinfo=TZ),
                    )
                )
                row = self.application.monthly_statistics(
                    GetMonthlyStatisticsQuery(self.project.project_key, "2026-09")
                ).rows[0]
                self.assertEqual(row.closing_status, "已清仓")
                self.assertEqual(
                    row.return_rate, (total_proceeds - total_cost) / total_cost
                )

    def test_liquidation_keeps_tracking_and_current_statistics_ignore_stale_snapshot(self) -> None:
        self.application._now = Mock(return_value=datetime(2026, 9, 8, 16, tzinfo=TZ))
        buy = self._buy()
        snapshot = self.application.record_daily_valuation(
            RecordDailyValuationCommand(
                project_key=self.project.project_key,
                actor="test-user",
                valuation_time=datetime(2026, 9, 2, 15, tzinfo=TZ),
            )
        )
        sell = self._sell_all("11", datetime(2026, 9, 3, 10, tzinfo=TZ))
        tracking = self.application.list_tracking(ListTrackingQuery(self.project.project_key))
        self.assertEqual(len(tracking.rows), 1)
        row = tracking.rows[0]
        self.assertEqual(row.tracking_id, self.tracking.tracking_id)
        self.assertEqual(row.source_text, self.tracking.source_text)
        self.assertEqual(row.tracking_status, TrackingStatus.WATCHING)
        self.assertEqual(row.quantity, Decimal("0"))
        self.assertIsNone(row.expires_at)
        report = self.application.monthly_statistics(
            GetMonthlyStatisticsQuery(self.project.project_key, "2026-09")
        )
        self.assertEqual(report.rows[0].closing_status, "已清仓")
        self.assertEqual(report.rows[0].closing_position_ratio, Decimal("0"))
        self.assertEqual(report.rows[0].unrealized_pnl_change, Decimal("0"))
        net_profit = buy.net_cash_amount + sell.net_cash_amount
        self.assertEqual(report.pnl, net_profit)
        self.assertEqual(report.closing_capital, Decimal("100000") + net_profit)
        self.assertEqual(report.rows[0].return_rate, net_profit / -buy.net_cash_amount)
        self.assertEqual(len(report.valuation_points), 1)
        self.assertEqual(report.valuation_points[0].equity, snapshot.equity)
        self.application.archive_tracking(
            ArchiveTrackedInstrumentCommand(
                project_key=self.project.project_key,
                tracking_id=row.tracking_id,
                actor="test-user",
            )
        )
        self.assertEqual(
            self.application.list_tracking(ListTrackingQuery(self.project.project_key)).rows, ()
        )
        self.assertEqual(
            self.application.monthly_statistics(
                GetMonthlyStatisticsQuery(self.project.project_key, "2026-09")
            ).rows[0].closing_status,
            "已清仓",
        )

    def test_historical_month_keeps_its_position_after_current_month_liquidation(self) -> None:
        self.application._now = Mock(return_value=datetime(2026, 10, 8, 16, tzinfo=TZ))
        self._buy()
        self.application.record_daily_valuation(
            RecordDailyValuationCommand(
                project_key=self.project.project_key,
                actor="test-user",
                valuation_time=datetime(2026, 9, 30, 15, tzinfo=TZ),
            )
        )
        self._sell_all("11", datetime(2026, 10, 3, 10, tzinfo=TZ))
        historical = self.application.monthly_statistics(
            GetMonthlyStatisticsQuery(self.project.project_key, "2026-09")
        )
        current = self.application.monthly_statistics(
            GetMonthlyStatisticsQuery(self.project.project_key, "2026-10")
        )
        self.assertEqual(historical.rows[0].closing_status, "持仓中")
        self.assertEqual(current.rows[0].closing_status, "已清仓")

    def test_reversing_buy_keeps_empty_instrument_in_tracking(self) -> None:
        buy = self._buy()
        self.application.reverse_trade(
            ReverseTradeCommand(
                project_key=self.project.project_key,
                trade_id=buy.trade_id,
                request_id="reverse-only-buy",
                reason="买入录错",
                actor="test-user",
            )
        )
        tracking = self.application.list_tracking(ListTrackingQuery(self.project.project_key))
        self.assertEqual(tracking.rows[0].quantity, Decimal("0"))
        self.assertEqual(tracking.rows[0].tracking_status, TrackingStatus.WATCHING)

    def test_closed_return_includes_prior_month_sales_not_later_trades(self) -> None:
        buy = self._buy()
        partial_sell = self._sell()
        final_sell = self._sell_all("12", datetime(2026, 10, 3, 10, tzinfo=TZ))
        self.application.record_daily_valuation(
            RecordDailyValuationCommand(
                project_key=self.project.project_key,
                actor="test-user",
                valuation_time=datetime(2026, 10, 3, 15, tzinfo=TZ),
            )
        )
        self.application.confirm_manual_trade(
            ConfirmManualTradeCommand(
                project_key=self.project.project_key,
                symbol="600000.SH",
                side=TradeSide.BUY,
                allocation_ratio=Decimal("0.5"),
                price=Decimal("8"),
                signal_text="下月重新买入",
                actor="test-user",
                trade_time=datetime(2026, 11, 2, 10, tzinfo=TZ),
            )
        )
        row = self.application.monthly_statistics(
            GetMonthlyStatisticsQuery(self.project.project_key, "2026-10")
        ).rows[0]
        expected_profit = (
            buy.net_cash_amount + partial_sell.net_cash_amount + final_sell.net_cash_amount
        )
        self.assertEqual(row.closing_status, "已清仓")
        self.assertEqual(row.return_rate, expected_profit / -buy.net_cash_amount)

    def test_closed_return_excludes_reversed_trades(self) -> None:
        buy = self._buy()
        reversed_sell = self._sell_all("9", datetime(2026, 9, 3, 10, tzinfo=TZ))
        self.application.reverse_trade(
            ReverseTradeCommand(
                project_key=self.project.project_key,
                trade_id=reversed_sell.trade_id,
                request_id="reverse-closed-return",
                reason="卖价录错",
                actor="test-user",
            )
        )
        sell = self._sell_all("11", datetime(2026, 9, 3, 11, tzinfo=TZ))
        self.application.record_daily_valuation(
            RecordDailyValuationCommand(
                project_key=self.project.project_key,
                actor="test-user",
                valuation_time=datetime(2026, 9, 3, 15, tzinfo=TZ),
            )
        )
        row = self.application.monthly_statistics(
            GetMonthlyStatisticsQuery(self.project.project_key, "2026-09")
        ).rows[0]
        self.assertEqual(
            row.return_rate,
            (sell.net_cash_amount + buy.net_cash_amount) / -buy.net_cash_amount,
        )

    def test_daily_valuation_and_monthly_statistics(self) -> None:
        self._buy()
        self._sell()
        self.application.record_daily_valuation(
            RecordDailyValuationCommand(
                project_key=self.project.project_key,
                actor="test-user",
                valuation_time=datetime(2026, 9, 3, 15, 0, tzinfo=TZ),
            )
        )
        months = self.application.list_statistics_months(
            ListStatisticsMonthsQuery(self.project.project_key)
        )
        report = self.application.monthly_statistics(
            GetMonthlyStatisticsQuery(self.project.project_key, "2026-09")
        )
        self.assertIn("2026-09", months)
        self.assertEqual(report.buy_count, 1)
        self.assertEqual(report.sell_count, 1)
        self.assertEqual(report.instrument_count, 1)
        self.assertFalse(report.is_partial)
        self.assertEqual(len(report.valuation_points), 1)
        self.assertEqual(report.valuation_points[0].valuation_date, "2026-09-03")

    def test_reading_monthly_statistics_does_not_create_valuation(self) -> None:
        self.application.monthly_statistics(
            GetMonthlyStatisticsQuery(self.project.project_key, "2026-09")
        )
        with self.application.database.read() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM daily_account_valuations"
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_multiple_valuations_produce_drawdown_and_volatility(self) -> None:
        self._buy()
        self.application.record_daily_valuation(
            RecordDailyValuationCommand(
                project_key=self.project.project_key,
                actor="test-user",
                valuation_time=datetime(2026, 9, 2, 15, 0, tzinfo=TZ),
            )
        )
        for day, price in ((3, "12.00"), (4, "8.00")):
            self.application.record_reference_price(
                RecordReferencePriceCommand(
                    market="CN_STOCK",
                    venue="SSE",
                    symbol="600000.SH",
                    price=Decimal(price),
                    price_time=datetime(2026, 9, day, 15, 0, tzinfo=TZ),
                    source_name="测试行情",
                    request_id=f"price-{day}",
                    actor="test-user",
                )
            )
            self.application.record_daily_valuation(
                RecordDailyValuationCommand(
                    project_key=self.project.project_key,
                    actor="test-user",
                    valuation_time=datetime(2026, 9, day, 15, 5, tzinfo=TZ),
                )
            )
        report = self.application.monthly_statistics(
            GetMonthlyStatisticsQuery(self.project.project_key, "2026-09")
        )
        self.assertIsNotNone(report.max_drawdown)
        self.assertLess(report.max_drawdown, 0)
        self.assertIsNotNone(report.annualized_volatility)
        self.assertEqual(len(report.valuation_points), 3)
        self.assertEqual(
            [point.valuation_date for point in report.valuation_points],
            ["2026-09-02", "2026-09-03", "2026-09-04"],
        )

    def test_non_ledger_sqlite_file_is_rejected(self) -> None:
        path = Path(self.temp_dir.name) / "other.sqlite3"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE unrelated(id INTEGER PRIMARY KEY)")
        connection.commit()
        connection.close()
        with self.assertRaises(DatabaseIdentityError):
            LedgerDatabase(path).initialize()

    def test_version_one_database_is_upgraded_with_project_colors(self) -> None:
        path = Path(self.temp_dir.name) / "version-one.sqlite3"
        connection = sqlite3.connect(path)
        connection.executescript(MIGRATION_PATHS[1].read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO ledger_metadata(key, value) VALUES (?, ?), (?, ?)",
            ("database_identity", DATABASE_IDENTITY, "schema_version", "1"),
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)",
            ("2026-09-04T00:00:00+00:00",),
        )
        connection.execute(
            """
            INSERT INTO projects (
                project_key, project_name, project_name_normalized,
                description, status, created_at
            ) VALUES ('old-project', '旧项目', '旧项目', '', 'ACTIVE', ?)
            """,
            ("2026-09-04T00:00:00+00:00",),
        )
        connection.commit()
        connection.close()
        database = LedgerDatabase(path)
        database.initialize()
        with database.read() as upgraded:
            metadata = dict(
                upgraded.execute("SELECT key, value FROM ledger_metadata")
            )
            project = upgraded.execute(
                "SELECT color_key FROM projects WHERE project_key = 'old-project'"
            ).fetchone()
        self.assertEqual(metadata["schema_version"], "3")
        self.assertEqual(project["color_key"], "blue")


if __name__ == "__main__":
    unittest.main()
