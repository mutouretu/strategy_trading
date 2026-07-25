from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from market_protocol import MarketFrame


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderStatus(StrEnum):
    ACTIVE = "ACTIVE"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class SimOrder:
    """A domain-neutral logical market or limit order."""

    order_key: str
    instrument: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None = None
    tags: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.order_key.strip():
            raise ValueError("order_key must not be empty")
        if not self.instrument.strip():
            raise ValueError("instrument must not be empty")
        if self.quantity <= 0:
            raise ValueError("quantity must be > 0")
        if self.order_type == OrderType.LIMIT:
            if self.limit_price is None or self.limit_price <= 0:
                raise ValueError("LIMIT order requires limit_price > 0")
        elif self.limit_price is not None:
            raise ValueError("MARKET order must not define limit_price")
        object.__setattr__(self, "tags", MappingProxyType(dict(self.tags)))


@dataclass(frozen=True, slots=True)
class ActiveOrder:
    order: SimOrder
    activated_at_sequence: int


@dataclass(frozen=True, slots=True)
class SimFill:
    fill_id: str
    order_key: str
    instrument: str
    side: OrderSide
    price: Decimal
    quantity: Decimal
    sequence: int
    timestamp: int
    tags: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tags", MappingProxyType(dict(self.tags)))


@dataclass(frozen=True, slots=True)
class SimulationDecision:
    """A decision provider's complete desired-order set after one callback."""

    desired_orders: tuple[SimOrder, ...] = ()


@dataclass(frozen=True, slots=True)
class OrderRecord:
    """One logical order's complete lifecycle.

    ``active_to_sequence`` is exclusive. A record active from 3 to 5 can
    participate in bars 4 and 5, but it is no longer present in the
    end-of-bar state for sequence 5.
    """

    order: SimOrder
    active_from_sequence: int
    active_to_sequence: int | None = None
    status: OrderStatus = OrderStatus.ACTIVE

    def __post_init__(self) -> None:
        if self.active_from_sequence < 0:
            raise ValueError("active_from_sequence must be >= 0")
        if (
            self.active_to_sequence is not None
            and self.active_to_sequence < self.active_from_sequence
        ):
            raise ValueError(
                "active_to_sequence must be >= active_from_sequence"
            )
        if self.status == OrderStatus.ACTIVE and self.active_to_sequence is not None:
            raise ValueError("ACTIVE order must not have active_to_sequence")
        if self.status != OrderStatus.ACTIVE and self.active_to_sequence is None:
            raise ValueError("closed order requires active_to_sequence")


@dataclass(frozen=True, slots=True)
class EquitySnapshot:
    """End-of-bar account state after fills and decision callbacks."""

    sequence: int
    timestamp: int
    cash: Decimal
    positions: Mapping[str, Decimal]
    average_costs: Mapping[str, Decimal]
    marks: Mapping[str, Decimal]
    realized_pnl: Decimal
    equity: Decimal
    equity_asset: str = "USDT"
    account_metrics: Mapping[str, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("sequence must be >= 0")
        if not self.equity_asset.strip():
            raise ValueError("equity_asset must not be empty")
        object.__setattr__(self, "positions", MappingProxyType(dict(self.positions)))
        object.__setattr__(
            self,
            "average_costs",
            MappingProxyType(dict(self.average_costs)),
        )
        object.__setattr__(self, "marks", MappingProxyType(dict(self.marks)))
        object.__setattr__(
            self,
            "account_metrics",
            MappingProxyType(dict(self.account_metrics)),
        )


@dataclass(frozen=True, slots=True)
class SimulationResult:
    frames: tuple[MarketFrame, ...]
    orders: tuple[OrderRecord, ...]
    fills: tuple[SimFill, ...]
    equity_curve: tuple[EquitySnapshot, ...]
    initial_equity: Decimal
    final_cash: Decimal
    final_positions: Mapping[str, Decimal] = field(default_factory=dict)
    final_average_costs: Mapping[str, Decimal] = field(default_factory=dict)
    realized_pnl: Decimal = Decimal("0")
    final_equity: Decimal = Decimal("0")
    equity_asset: str = "USDT"
    final_account_metrics: Mapping[str, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.equity_asset.strip():
            raise ValueError("equity_asset must not be empty")
        object.__setattr__(
            self,
            "final_positions",
            MappingProxyType(dict(self.final_positions)),
        )
        object.__setattr__(
            self,
            "final_average_costs",
            MappingProxyType(dict(self.final_average_costs)),
        )
        object.__setattr__(
            self,
            "final_account_metrics",
            MappingProxyType(dict(self.final_account_metrics)),
        )
