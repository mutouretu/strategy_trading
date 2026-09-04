"""Application service port shared by Streamlit and the future HTTP adapter."""

from __future__ import annotations

from typing import Protocol, Sequence

from .contracts import (
    AccountSummaryView,
    AddTrackedInstrumentCommand,
    ArchiveProjectCommand,
    ArchiveTrackedInstrumentCommand,
    CashEntryView,
    CloseTrackedInstrumentCommand,
    ConfirmManualTradeCommand,
    CreateProjectCommand,
    DailyValuationView,
    ExpireTrackingCommand,
    GetAccountSummaryQuery,
    GetMonthlyStatisticsQuery,
    GetProjectQuery,
    GetTradeQuery,
    ListOperationHistoryQuery,
    ListPositionsQuery,
    ListProjectsQuery,
    ListStatisticsMonthsQuery,
    ListTrackingQuery,
    MonthlyStatisticsView,
    OperationHistoryPageView,
    PositionView,
    PreviewManualTradeCommand,
    PriceRefreshView,
    ProjectView,
    RecordCashEntryCommand,
    RecordConfirmedTradeCommand,
    RecordDailyValuationCommand,
    RecordReferencePriceCommand,
    ReferencePriceView,
    RefreshTrackingPricesCommand,
    RestoreProjectCommand,
    ReverseTradeCommand,
    TrackingPageView,
    TrackingRowView,
    TradePreviewView,
    TradeView,
    UpdateProjectCommand,
    UpdateTrackedInstrumentCommand,
)


class TradingLedgerApplication(Protocol):
    """Stable use-case boundary shared by the current UI and future API."""

    def create_project(self, command: CreateProjectCommand) -> ProjectView: ...

    def update_project(self, command: UpdateProjectCommand) -> ProjectView: ...

    def archive_project(self, command: ArchiveProjectCommand) -> ProjectView: ...

    def restore_project(self, command: RestoreProjectCommand) -> ProjectView: ...

    def list_projects(self, query: ListProjectsQuery) -> Sequence[ProjectView]: ...

    def get_project(self, query: GetProjectQuery) -> ProjectView: ...

    def add_tracking(self, command: AddTrackedInstrumentCommand) -> TrackingRowView: ...

    def update_tracking(
        self, command: UpdateTrackedInstrumentCommand
    ) -> TrackingRowView: ...

    def close_tracking(
        self, command: CloseTrackedInstrumentCommand
    ) -> TrackingRowView: ...

    def archive_tracking(
        self, command: ArchiveTrackedInstrumentCommand
    ) -> TrackingRowView: ...

    def refresh_tracking_prices(
        self, command: RefreshTrackingPricesCommand
    ) -> PriceRefreshView: ...

    def expire_tracking(self, command: ExpireTrackingCommand) -> int: ...

    def list_tracking(self, query: ListTrackingQuery) -> TrackingPageView: ...

    def preview_manual_trade(
        self, command: PreviewManualTradeCommand
    ) -> TradePreviewView: ...

    def confirm_manual_trade(self, command: ConfirmManualTradeCommand) -> TradeView: ...

    def record_confirmed_trade(
        self, command: RecordConfirmedTradeCommand
    ) -> TradeView: ...

    def get_trade(self, query: GetTradeQuery) -> TradeView: ...

    def reverse_trade(self, command: ReverseTradeCommand) -> TradeView: ...

    def record_cash_entry(self, command: RecordCashEntryCommand) -> CashEntryView: ...

    def account_summary(self, query: GetAccountSummaryQuery) -> AccountSummaryView: ...

    def list_positions(self, query: ListPositionsQuery) -> Sequence[PositionView]: ...

    def operation_history(
        self, query: ListOperationHistoryQuery
    ) -> OperationHistoryPageView: ...

    def list_statistics_months(
        self, query: ListStatisticsMonthsQuery
    ) -> Sequence[str]: ...

    def monthly_statistics(
        self, query: GetMonthlyStatisticsQuery
    ) -> MonthlyStatisticsView: ...

    def record_reference_price(
        self, command: RecordReferencePriceCommand
    ) -> ReferencePriceView: ...

    def record_daily_valuation(
        self, command: RecordDailyValuationCommand
    ) -> DailyValuationView: ...
