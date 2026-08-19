"""Typed trading facts that can be projected into concrete rule inputs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from ._values import freeze_mapping
from .intents import TradeSide


def _decimal(name: str, value: object) -> Decimal:
    converted = Decimal(str(value))
    if not converted.is_finite():
        raise ValueError(f"{name} must be finite")
    return converted


class TradingEventKind(StrEnum):
    START = "START"
    MARKET_UPDATED = "MARKET_UPDATED"
    SIGNAL_RECEIVED = "SIGNAL_RECEIVED"
    FILL_RECEIVED = "FILL_RECEIVED"
    POSITION_CHANGED = "POSITION_CHANGED"
    STOP_REQUESTED = "STOP_REQUESTED"


@dataclass(frozen=True, slots=True)
class TradingEvent(ABC):
    event_id: str
    sequence: int
    timestamp: int

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("event_id must be a non-empty string")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise TypeError("sequence must be an integer")
        if self.sequence < 0:
            raise ValueError("sequence must be >= 0")
        if isinstance(self.timestamp, bool) or not isinstance(self.timestamp, int):
            raise TypeError("timestamp must be an integer")
        if self.timestamp < 0:
            raise ValueError("timestamp must be >= 0")

    @property
    @abstractmethod
    def kind(self) -> TradingEventKind:
        ...


@dataclass(frozen=True, slots=True)
class TradingStartEvent(TradingEvent):
    @property
    def kind(self) -> TradingEventKind:
        return TradingEventKind.START


@dataclass(frozen=True, slots=True)
class TradingMarketEvent(TradingEvent):
    instrument: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    def __post_init__(self) -> None:
        super(TradingMarketEvent, self).__post_init__()
        if not isinstance(self.instrument, str) or not self.instrument.strip():
            raise ValueError("instrument must be a non-empty string")
        for field_name in ("open", "high", "low", "close"):
            value = _decimal(field_name, getattr(self, field_name))
            if value <= 0:
                raise ValueError(f"{field_name} must be > 0")
            object.__setattr__(self, field_name, value)
        if self.low > min(self.open, self.high, self.close):
            raise ValueError("low must not exceed open, high, or close")
        if self.high < max(self.open, self.low, self.close):
            raise ValueError("high must not be below open, low, or close")

    @property
    def kind(self) -> TradingEventKind:
        return TradingEventKind.MARKET_UPDATED


@dataclass(frozen=True, slots=True)
class TradingSignalEvent(TradingEvent):
    signal_type: str
    values: Mapping[str, object]

    def __post_init__(self) -> None:
        super(TradingSignalEvent, self).__post_init__()
        if not isinstance(self.signal_type, str) or not self.signal_type.strip():
            raise ValueError("signal_type must be a non-empty string")
        object.__setattr__(self, "values", freeze_mapping(self.values))

    @property
    def kind(self) -> TradingEventKind:
        return TradingEventKind.SIGNAL_RECEIVED


@dataclass(frozen=True, slots=True)
class TradingFillEvent(TradingEvent):
    fill_id: str
    source_intent_key: str
    side: TradeSide
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        super(TradingFillEvent, self).__post_init__()
        for field_name in ("fill_id", "source_intent_key"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if not isinstance(self.side, TradeSide):
            raise TypeError("side must be a TradeSide")
        for field_name in ("price", "quantity"):
            value = _decimal(field_name, getattr(self, field_name))
            if value <= 0:
                raise ValueError(f"{field_name} must be > 0")
            object.__setattr__(self, field_name, value)

    @property
    def kind(self) -> TradingEventKind:
        return TradingEventKind.FILL_RECEIVED


@dataclass(frozen=True, slots=True)
class TradingPositionChangedEvent(TradingEvent):
    position_owner_id: str
    quantity: Decimal
    average_entry_price: Decimal | None
    reason: str

    def __post_init__(self) -> None:
        super(TradingPositionChangedEvent, self).__post_init__()
        for field_name in ("position_owner_id", "reason"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        object.__setattr__(self, "quantity", _decimal("quantity", self.quantity))
        if self.average_entry_price is not None:
            average = _decimal("average_entry_price", self.average_entry_price)
            if average <= 0:
                raise ValueError("average_entry_price must be > 0")
            object.__setattr__(self, "average_entry_price", average)
        if self.quantity != 0 and self.average_entry_price is None:
            raise ValueError("a non-zero position requires average_entry_price")

    @property
    def kind(self) -> TradingEventKind:
        return TradingEventKind.POSITION_CHANGED


@dataclass(frozen=True, slots=True)
class TradingStopEvent(TradingEvent):
    reason: str

    def __post_init__(self) -> None:
        super(TradingStopEvent, self).__post_init__()
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")

    @property
    def kind(self) -> TradingEventKind:
        return TradingEventKind.STOP_REQUESTED
