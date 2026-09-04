"""Stable status values used by the application contracts."""

from enum import StrEnum


class ProjectStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class AccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    ARCHIVED = "ARCHIVED"


class TrackingStatus(StrEnum):
    WATCHING = "WATCHING"
    HOLDING = "HOLDING"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"
    ARCHIVED = "ARCHIVED"


class TradeSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class ExecutionSource(StrEnum):
    MANUAL = "MANUAL"
    AUTO_TRADING = "AUTO_TRADING"
    IMPORT = "IMPORT"


class TradeRecordStatus(StrEnum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    REVERSED = "REVERSED"


class RatioBasis(StrEnum):
    AVAILABLE_CASH = "AVAILABLE_CASH"
    SELLABLE_POSITION = "SELLABLE_POSITION"


class OperationKind(StrEnum):
    TRACKING = "TRACKING"
    TRADE = "TRADE"
    REVERSAL = "REVERSAL"


class CashEntryType(StrEnum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
