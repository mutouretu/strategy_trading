"""Stable application-layer input and output contracts.

These types describe the confirmed UI and future API without selecting a database,
web framework, or transaction implementation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Mapping

from trading_ledger.domain import (
    AccountStatus,
    CashEntryType,
    ExecutionSource,
    OperationKind,
    ProjectStatus,
    RatioBasis,
    TradeRecordStatus,
    TradeSide,
    TrackingStatus,
)


PRIMARY_ACCOUNT_CODE = "primary-cny"
PROJECT_COLOR_KEYS = ("blue", "green", "yellow", "purple", "orange", "rose")
_PROJECT_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} cannot be empty.")


def _require_project_key(value: str) -> None:
    if not _PROJECT_KEY_PATTERN.fullmatch(value):
        raise ValueError(
            "project_key must contain 1-64 lowercase letters, digits, or hyphens."
        )


def _require_positive(value: Decimal, field_name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{field_name} must be a positive Decimal.")


def _require_non_negative(value: Decimal | None, field_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{field_name} must be a non-negative Decimal.")


def _require_ratio(value: Decimal) -> None:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value <= 0
        or value > 1
    ):
        raise ValueError("allocation_ratio must be a Decimal greater than 0 and at most 1.")


def _require_aware_datetime(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone offset.")


class ErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
    PROJECT_ARCHIVED = "PROJECT_ARCHIVED"
    DUPLICATE_PROJECT_NAME = "DUPLICATE_PROJECT_NAME"
    ACCOUNT_NOT_FOUND = "ACCOUNT_NOT_FOUND"
    ACCOUNT_UNAVAILABLE = "ACCOUNT_UNAVAILABLE"
    INSTRUMENT_NOT_FOUND = "INSTRUMENT_NOT_FOUND"
    TRACKING_NOT_FOUND = "TRACKING_NOT_FOUND"
    INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
    INSUFFICIENT_SELLABLE_POSITION = "INSUFFICIENT_SELLABLE_POSITION"
    TRADE_NOT_FOUND = "TRADE_NOT_FOUND"
    TRADE_ALREADY_REVERSED = "TRADE_ALREADY_REVERSED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    EXTERNAL_TRADE_CONFLICT = "EXTERNAL_TRADE_CONFLICT"
    CONCURRENCY_CONFLICT = "CONCURRENCY_CONFLICT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True, slots=True)
class ErrorDetail:
    code: ErrorCode
    message: str
    field: str | None = None
    retryable: bool = False


class ApplicationError(Exception):
    """Framework-neutral application failure exposed to UI and API adapters."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        field: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.detail = ErrorDetail(code, message, field, retryable)


# Commands used by the confirmed Streamlit flows.


@dataclass(frozen=True, slots=True)
class CreateProjectCommand:
    project_name: str
    initial_capital: Decimal
    actor: str
    description: str = ""

    def __post_init__(self) -> None:
        _require_text(self.project_name, "project_name")
        _require_positive(self.initial_capital, "initial_capital")
        _require_text(self.actor, "actor")


@dataclass(frozen=True, slots=True)
class UpdateProjectCommand:
    project_key: str
    project_name: str
    actor: str
    description: str = ""
    color_key: str | None = None

    def __post_init__(self) -> None:
        _require_project_key(self.project_key)
        _require_text(self.project_name, "project_name")
        _require_text(self.actor, "actor")
        if self.color_key is not None and self.color_key not in PROJECT_COLOR_KEYS:
            raise ValueError("color_key is not a supported project color.")


@dataclass(frozen=True, slots=True)
class ArchiveProjectCommand:
    project_key: str
    actor: str

    def __post_init__(self) -> None:
        _require_project_key(self.project_key)
        _require_text(self.actor, "actor")


@dataclass(frozen=True, slots=True)
class RestoreProjectCommand:
    project_key: str
    actor: str

    def __post_init__(self) -> None:
        _require_project_key(self.project_key)
        _require_text(self.actor, "actor")


@dataclass(frozen=True, slots=True)
class AddTrackedInstrumentCommand:
    project_key: str
    symbol: str
    name: str
    source_text: str
    actor: str
    added_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_project_key(self.project_key)
        _require_text(self.symbol, "symbol")
        _require_text(self.name, "name")
        _require_text(self.source_text, "source_text")
        _require_text(self.actor, "actor")


@dataclass(frozen=True, slots=True)
class UpdateTrackedInstrumentCommand:
    project_key: str
    tracking_id: int
    symbol: str
    name: str
    source_text: str
    actor: str

    def __post_init__(self) -> None:
        _require_project_key(self.project_key)
        if self.tracking_id <= 0:
            raise ValueError("tracking_id must be positive.")
        _require_text(self.symbol, "symbol")
        _require_text(self.name, "name")
        _require_text(self.source_text, "source_text")
        _require_text(self.actor, "actor")


@dataclass(frozen=True, slots=True)
class CloseTrackedInstrumentCommand:
    project_key: str
    tracking_id: int
    actor: str

    def __post_init__(self) -> None:
        _require_project_key(self.project_key)
        if self.tracking_id <= 0:
            raise ValueError("tracking_id must be positive.")
        _require_text(self.actor, "actor")


@dataclass(frozen=True, slots=True)
class ArchiveTrackedInstrumentCommand:
    project_key: str
    tracking_id: int
    actor: str

    def __post_init__(self) -> None:
        _require_project_key(self.project_key)
        if self.tracking_id <= 0:
            raise ValueError("tracking_id must be positive.")
        _require_text(self.actor, "actor")


@dataclass(frozen=True, slots=True)
class RefreshTrackingPricesCommand:
    project_key: str
    actor: str

    def __post_init__(self) -> None:
        _require_project_key(self.project_key)
        _require_text(self.actor, "actor")


@dataclass(frozen=True, slots=True)
class ExpireTrackingCommand:
    project_key: str
    actor: str
    as_of: datetime | None = None

    def __post_init__(self) -> None:
        _require_project_key(self.project_key)
        _require_text(self.actor, "actor")


@dataclass(frozen=True, slots=True)
class PreviewManualTradeCommand:
    project_key: str
    symbol: str
    side: TradeSide
    allocation_ratio: Decimal
    price: Decimal
    signal_text: str
    account_code: str = PRIMARY_ACCOUNT_CODE

    def __post_init__(self) -> None:
        _require_project_key(self.project_key)
        _require_text(self.symbol, "symbol")
        _require_ratio(self.allocation_ratio)
        _require_positive(self.price, "price")
        _require_text(self.signal_text, "signal_text")
        _require_text(self.account_code, "account_code")


@dataclass(frozen=True, slots=True)
class ConfirmManualTradeCommand:
    project_key: str
    symbol: str
    side: TradeSide
    allocation_ratio: Decimal
    price: Decimal
    signal_text: str
    actor: str
    account_code: str = PRIMARY_ACCOUNT_CODE
    trade_time: datetime | None = None

    def __post_init__(self) -> None:
        _require_project_key(self.project_key)
        _require_text(self.symbol, "symbol")
        _require_ratio(self.allocation_ratio)
        _require_positive(self.price, "price")
        _require_text(self.signal_text, "signal_text")
        _require_text(self.actor, "actor")
        _require_text(self.account_code, "account_code")


# Commands reserved for the versioned inbound API. They share the same service port.


@dataclass(frozen=True, slots=True)
class RecordConfirmedTradeCommand:
    project_key: str
    request_id: str
    external_trade_id: str
    account_code: str
    market: str
    venue: str
    symbol: str
    side: TradeSide
    trade_time: datetime
    quantity: Decimal
    price: Decimal
    signal_text: str
    actor: str
    execution_source: ExecutionSource = ExecutionSource.AUTO_TRADING
    commission_amount: Decimal | None = None
    stamp_tax_amount: Decimal | None = None
    transfer_fee_amount: Decimal | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for value, name in (
            (self.project_key, "project_key"),
            (self.request_id, "request_id"),
            (self.external_trade_id, "external_trade_id"),
            (self.account_code, "account_code"),
            (self.market, "market"),
            (self.venue, "venue"),
            (self.symbol, "symbol"),
            (self.signal_text, "signal_text"),
            (self.actor, "actor"),
        ):
            if name == "project_key":
                _require_project_key(value)
            else:
                _require_text(value, name)
        _require_positive(self.quantity, "quantity")
        _require_positive(self.price, "price")
        _require_aware_datetime(self.trade_time, "trade_time")
        _require_non_negative(self.commission_amount, "commission_amount")
        _require_non_negative(self.stamp_tax_amount, "stamp_tax_amount")
        _require_non_negative(self.transfer_fee_amount, "transfer_fee_amount")
        provided_fees = (
            self.commission_amount,
            self.stamp_tax_amount,
            self.transfer_fee_amount,
        )
        if any(value is None for value in provided_fees) and any(
            value is not None for value in provided_fees
        ):
            raise ValueError("fee fields must be provided together or all omitted.")
        if self.side is TradeSide.BUY and any(
            value not in (None, Decimal("0"))
            for value in (self.stamp_tax_amount, self.transfer_fee_amount)
        ):
            raise ValueError("BUY trades cannot include stamp tax or transfer fees.")
        if self.execution_source is ExecutionSource.MANUAL:
            raise ValueError("The inbound API does not accept MANUAL execution_source.")


@dataclass(frozen=True, slots=True)
class ReverseTradeCommand:
    project_key: str
    trade_id: str
    request_id: str
    reason: str
    actor: str

    def __post_init__(self) -> None:
        _require_project_key(self.project_key)
        _require_text(self.trade_id, "trade_id")
        _require_text(self.request_id, "request_id")
        _require_text(self.reason, "reason")
        _require_text(self.actor, "actor")


@dataclass(frozen=True, slots=True)
class RecordCashEntryCommand:
    project_key: str
    account_code: str
    entry_type: CashEntryType
    amount: Decimal
    occurred_at: datetime
    request_id: str
    note: str
    actor: str
    currency: str = "CNY"

    def __post_init__(self) -> None:
        _require_project_key(self.project_key)
        _require_text(self.account_code, "account_code")
        _require_positive(self.amount, "amount")
        _require_aware_datetime(self.occurred_at, "occurred_at")
        _require_text(self.request_id, "request_id")
        _require_text(self.note, "note")
        _require_text(self.actor, "actor")
        _require_text(self.currency, "currency")


@dataclass(frozen=True, slots=True)
class RecordReferencePriceCommand:
    market: str
    venue: str
    symbol: str
    price: Decimal
    price_time: datetime
    source_name: str
    request_id: str
    actor: str

    def __post_init__(self) -> None:
        _require_text(self.market, "market")
        _require_text(self.venue, "venue")
        _require_text(self.symbol, "symbol")
        _require_positive(self.price, "price")
        _require_aware_datetime(self.price_time, "price_time")
        _require_text(self.source_name, "source_name")
        _require_text(self.request_id, "request_id")
        _require_text(self.actor, "actor")


@dataclass(frozen=True, slots=True)
class RecordDailyValuationCommand:
    project_key: str
    actor: str
    valuation_time: datetime | None = None
    require_fresh_prices: bool = False

    def __post_init__(self) -> None:
        _require_project_key(self.project_key)
        _require_text(self.actor, "actor")
        if self.valuation_time is not None:
            _require_aware_datetime(self.valuation_time, "valuation_time")


# Read-only queries.


@dataclass(frozen=True, slots=True)
class ListProjectsQuery:
    include_archived: bool = True


@dataclass(frozen=True, slots=True)
class GetProjectQuery:
    project_key: str

    def __post_init__(self) -> None:
        _require_project_key(self.project_key)


@dataclass(frozen=True, slots=True)
class GetAccountSummaryQuery:
    project_key: str
    account_code: str = PRIMARY_ACCOUNT_CODE

    def __post_init__(self) -> None:
        _require_project_key(self.project_key)
        _require_text(self.account_code, "account_code")


@dataclass(frozen=True, slots=True)
class ListTrackingQuery:
    project_key: str
    keyword: str = ""
    statuses: tuple[TrackingStatus, ...] = (
        TrackingStatus.WATCHING,
        TrackingStatus.HOLDING,
    )

    def __post_init__(self) -> None:
        _require_project_key(self.project_key)


@dataclass(frozen=True, slots=True)
class ListOperationHistoryQuery:
    project_key: str
    keyword: str = ""
    page: int = 1
    page_size: int = 50

    def __post_init__(self) -> None:
        _require_project_key(self.project_key)
        if self.page <= 0:
            raise ValueError("page must be positive.")
        if not 1 <= self.page_size <= 200:
            raise ValueError("page_size must be between 1 and 200.")


@dataclass(frozen=True, slots=True)
class ListStatisticsMonthsQuery:
    project_key: str

    def __post_init__(self) -> None:
        _require_project_key(self.project_key)


@dataclass(frozen=True, slots=True)
class GetMonthlyStatisticsQuery:
    project_key: str
    month: str

    def __post_init__(self) -> None:
        _require_project_key(self.project_key)
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", self.month):
            raise ValueError("month must use YYYY-MM format.")


@dataclass(frozen=True, slots=True)
class GetTradeQuery:
    project_key: str
    trade_id: str

    def __post_init__(self) -> None:
        _require_project_key(self.project_key)
        _require_text(self.trade_id, "trade_id")


@dataclass(frozen=True, slots=True)
class ListPositionsQuery:
    project_key: str
    account_code: str = PRIMARY_ACCOUNT_CODE

    def __post_init__(self) -> None:
        _require_project_key(self.project_key)
        _require_text(self.account_code, "account_code")


# View models returned to Streamlit and, after serialization, to the API adapter.


@dataclass(frozen=True, slots=True)
class ProjectView:
    project_id: int
    project_key: str
    project_name: str
    description: str
    color_key: str
    status: ProjectStatus
    initial_capital: Decimal
    created_at: datetime
    archived_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AccountSummaryView:
    project_key: str
    account_code: str
    account_status: AccountStatus
    currency: str
    initial_capital: Decimal
    cash_balance: Decimal
    market_value: Decimal
    equity: Decimal
    position_ratio: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class TrackingSummaryView:
    watching_count: int
    holding_count: int
    today_added_count: int
    expired_count: int


@dataclass(frozen=True, slots=True)
class TrackingRowView:
    tracking_id: int
    project_key: str
    symbol: str
    name: str
    source_text: str
    tracking_status: TrackingStatus
    added_at: datetime
    expires_at: datetime | None
    reference_price: Decimal | None
    quantity: Decimal
    sellable_quantity: Decimal
    average_cost: Decimal | None
    position_ratio: Decimal
    pnl_ratio: Decimal | None


@dataclass(frozen=True, slots=True)
class TrackingPageView:
    summary: TrackingSummaryView
    account: AccountSummaryView
    rows: tuple[TrackingRowView, ...]


@dataclass(frozen=True, slots=True)
class TradePreviewView:
    project_key: str
    account_code: str
    symbol: str
    side: TradeSide
    ratio_basis: RatioBasis
    allocation_ratio: Decimal
    available_cash: Decimal
    sellable_quantity: Decimal
    quantity: Decimal
    price: Decimal
    gross_amount: Decimal
    commission_amount: Decimal
    stamp_tax_amount: Decimal
    transfer_fee_amount: Decimal
    net_cash_amount: Decimal


@dataclass(frozen=True, slots=True)
class TradeView:
    trade_id: str
    project_key: str
    account_code: str
    market: str
    venue: str
    symbol: str
    side: TradeSide
    trade_time: datetime
    quantity: Decimal
    price: Decimal
    gross_amount: Decimal
    commission_amount: Decimal
    stamp_tax_amount: Decimal
    transfer_fee_amount: Decimal
    net_cash_amount: Decimal
    realized_pnl: Decimal
    signal_text: str
    execution_source: ExecutionSource
    record_status: TradeRecordStatus
    external_trade_id: str | None = None
    request_id: str | None = None
    reversal_of_trade_id: str | None = None


@dataclass(frozen=True, slots=True)
class OperationHistoryRowView:
    operation_kind: OperationKind
    recorded_at: datetime
    symbol: str
    name: str
    side: TradeSide | None
    source_or_signal: str
    position_ratio: Decimal | None
    price: Decimal | None
    quantity: Decimal | None
    gross_amount: Decimal | None
    cash_change: Decimal | None


@dataclass(frozen=True, slots=True)
class OperationHistoryPageView:
    rows: tuple[OperationHistoryRowView, ...]
    page: int
    page_size: int
    page_count: int
    total_count: int
    matched_symbol_count: int


@dataclass(frozen=True, slots=True)
class MonthlyInstrumentStatisticsView:
    symbol: str
    name: str
    buy_count: int
    sell_count: int
    closing_position_ratio: Decimal
    sell_amount: Decimal
    realized_pnl: Decimal
    unrealized_pnl_change: Decimal
    pnl: Decimal
    return_rate: Decimal | None
    closing_status: str


@dataclass(frozen=True, slots=True)
class DailyValuationPointView:
    valuation_date: str
    cash_balance: Decimal
    market_value: Decimal
    equity: Decimal


@dataclass(frozen=True, slots=True)
class MonthlyStatisticsView:
    month: str
    instrument_count: int
    buy_count: int
    sell_count: int
    opening_capital: Decimal
    closing_capital: Decimal
    external_net_flow: Decimal
    pnl: Decimal
    return_rate: Decimal | None
    max_drawdown: Decimal | None
    annualized_volatility: Decimal | None
    is_partial: bool
    missing_symbols: tuple[str, ...]
    valuation_points: tuple[DailyValuationPointView, ...]
    rows: tuple[MonthlyInstrumentStatisticsView, ...]


@dataclass(frozen=True, slots=True)
class PositionView:
    project_key: str
    account_code: str
    symbol: str
    name: str
    quantity: Decimal
    sellable_quantity: Decimal
    average_cost: Decimal
    last_price: Decimal | None
    market_value: Decimal | None
    realized_pnl: Decimal
    unrealized_pnl: Decimal | None


@dataclass(frozen=True, slots=True)
class CashEntryView:
    cash_entry_id: str
    project_key: str
    account_code: str
    entry_type: CashEntryType
    amount: Decimal
    balance_after: Decimal
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ReferencePriceView:
    market: str
    venue: str
    symbol: str
    price: Decimal
    price_time: datetime
    source_name: str


@dataclass(frozen=True, slots=True)
class PriceRefreshView:
    requested_count: int
    updated_count: int
    failed_symbols: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DailyValuationView:
    valuation_date: str
    project_key: str
    account_code: str
    cash_balance: Decimal
    market_value: Decimal
    equity: Decimal
    external_net_flow: Decimal
    pnl: Decimal
    return_rate: Decimal | None
    peak_equity: Decimal
    drawdown: Decimal
    is_partial: bool
    missing_symbols: tuple[str, ...]
