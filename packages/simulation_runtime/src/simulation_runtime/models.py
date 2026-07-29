from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Mapping

from market_protocol import MarketFrame


if TYPE_CHECKING:
    from .funding import FundingSettlement
    from .margin import LiquidationEvent, MarginSnapshot


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class TradeIntentMode(StrEnum):
    PASSIVE = "PASSIVE"
    ACTIVE = "ACTIVE"


class LiquidityRole(StrEnum):
    MAKER = "MAKER"
    TAKER = "TAKER"


class IntentStatus(StrEnum):
    WAITING = "WAITING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"


class SimulationTerminationReason(StrEnum):
    LIQUIDATION = "LIQUIDATION"


@dataclass(frozen=True, slots=True)
class TradeInstruction:
    """One explicit trade to apply against a specific market frame."""

    instruction_key: str
    source_intent_key: str
    instrument: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    frame_sequence: int
    intent_mode: TradeIntentMode
    reduce_only: bool = False
    tags: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.instruction_key.strip():
            raise ValueError("instruction_key must not be empty")
        if not self.source_intent_key.strip():
            raise ValueError("source_intent_key must not be empty")
        if not self.instrument.strip():
            raise ValueError("instrument must not be empty")
        if self.quantity <= 0:
            raise ValueError("quantity must be > 0")
        if self.price <= 0:
            raise ValueError("price must be > 0")
        if self.frame_sequence < 0:
            raise ValueError("frame_sequence must be >= 0")
        if not isinstance(self.intent_mode, TradeIntentMode):
            raise TypeError("intent_mode must be a TradeIntentMode")
        if not isinstance(self.reduce_only, bool):
            raise TypeError("reduce_only must be a bool")
        object.__setattr__(self, "tags", MappingProxyType(dict(self.tags)))


@dataclass(frozen=True, slots=True)
class IntentSnapshot:
    """One strategy-owned intent currently visible to reporting."""

    intent_key: str
    instrument: str
    side: OrderSide
    quantity: Decimal
    intent_mode: TradeIntentMode
    target_price: Decimal | None = None
    reduce_only: bool = False
    tags: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.intent_key.strip():
            raise ValueError("intent_key must not be empty")
        if not self.instrument.strip():
            raise ValueError("instrument must not be empty")
        if self.quantity <= 0:
            raise ValueError("quantity must be > 0")
        if not isinstance(self.intent_mode, TradeIntentMode):
            raise TypeError("intent_mode must be a TradeIntentMode")
        if (
            self.intent_mode == TradeIntentMode.PASSIVE
            and (
                self.target_price is None
                or self.target_price <= 0
            )
        ):
            raise ValueError(
                "PASSIVE intent requires target_price > 0"
            )
        if (
            self.intent_mode == TradeIntentMode.ACTIVE
            and self.target_price is not None
        ):
            raise ValueError(
                "ACTIVE intent must not define target_price"
            )
        if not isinstance(self.reduce_only, bool):
            raise TypeError("reduce_only must be a bool")
        object.__setattr__(
            self,
            "tags",
            MappingProxyType(dict(self.tags)),
        )


@dataclass(frozen=True, slots=True)
class IntentRecord:
    """One strategy intent's complete reporting lifecycle."""

    intent: IntentSnapshot
    active_from_sequence: int
    active_to_sequence: int | None = None
    status: IntentStatus = IntentStatus.WAITING

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
        if (
            self.status == IntentStatus.WAITING
            and self.active_to_sequence is not None
        ):
            raise ValueError(
                "WAITING intent must not have active_to_sequence"
            )
        if (
            self.status != IntentStatus.WAITING
            and self.active_to_sequence is None
        ):
            raise ValueError(
                "closed intent requires active_to_sequence"
            )


@dataclass(frozen=True, slots=True)
class SimFill:
    fill_id: str
    instruction_key: str
    source_intent_key: str
    intent_mode: TradeIntentMode
    instrument: str
    side: OrderSide
    price: Decimal
    quantity: Decimal
    sequence: int
    timestamp: int
    liquidity_role: LiquidityRole
    fee_rate: Decimal
    fee_amount: Decimal
    fee_asset: str
    reduce_only: bool
    reference_price: Decimal | None = None
    slippage_amount: Decimal = Decimal("0")
    slippage_bps: Decimal = Decimal("0")
    tags: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.fill_id.strip():
            raise ValueError("fill_id must not be empty")
        if not self.instruction_key.strip():
            raise ValueError("instruction_key must not be empty")
        if not self.source_intent_key.strip():
            raise ValueError("source_intent_key must not be empty")
        if not isinstance(self.intent_mode, TradeIntentMode):
            raise TypeError("intent_mode must be a TradeIntentMode")
        if not self.instrument.strip():
            raise ValueError("instrument must not be empty")
        if self.price <= 0:
            raise ValueError("price must be > 0")
        if self.reference_price is None:
            object.__setattr__(self, "reference_price", self.price)
        assert self.reference_price is not None
        if not self.reference_price.is_finite():
            raise ValueError("reference_price must be finite")
        if self.reference_price <= 0:
            raise ValueError("reference_price must be > 0")
        if not self.slippage_amount.is_finite():
            raise ValueError("slippage_amount must be finite")
        if not self.slippage_bps.is_finite():
            raise ValueError("slippage_bps must be finite")
        if self.slippage_amount != self.price - self.reference_price:
            raise ValueError(
                "slippage_amount must equal price - reference_price"
            )
        expected_slippage_bps = (
            self.slippage_amount
            / self.reference_price
            * Decimal("10000")
        )
        if self.slippage_bps != expected_slippage_bps:
            raise ValueError(
                "slippage_bps must match price and reference_price"
            )
        if self.quantity <= 0:
            raise ValueError("quantity must be > 0")
        if self.sequence < 0:
            raise ValueError("sequence must be >= 0")
        if not isinstance(self.liquidity_role, LiquidityRole):
            raise TypeError("liquidity_role must be a LiquidityRole")
        if self.fee_rate < 0:
            raise ValueError("fee_rate must be >= 0")
        if self.fee_amount < 0:
            raise ValueError("fee_amount must be >= 0")
        if not self.fee_asset.strip():
            raise ValueError("fee_asset must not be empty")
        if not isinstance(self.reduce_only, bool):
            raise TypeError("reduce_only must be a bool")
        object.__setattr__(self, "tags", MappingProxyType(dict(self.tags)))


@dataclass(frozen=True, slots=True)
class EquitySnapshot:
    """End-of-bar account state after fills and decision callbacks."""

    sequence: int
    timestamp: int
    cash: Decimal
    positions: Mapping[str, Decimal]
    average_costs: Mapping[str, Decimal]
    marks: Mapping[str, Decimal]
    gross_realized_pnl: Decimal
    total_fees: Decimal
    net_realized_pnl: Decimal
    total_funding: Decimal
    net_pnl_after_fees_and_funding: Decimal
    realized_pnl: Decimal
    equity: Decimal
    equity_asset: str = "USDT"
    account_metrics: Mapping[str, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("sequence must be >= 0")
        if not self.equity_asset.strip():
            raise ValueError("equity_asset must not be empty")
        if self.realized_pnl != self.net_realized_pnl:
            raise ValueError(
                "realized_pnl must equal net_realized_pnl"
            )
        if (
            self.net_pnl_after_fees_and_funding
            != self.net_realized_pnl + self.total_funding
        ):
            raise ValueError(
                "net_pnl_after_fees_and_funding must equal "
                "net_realized_pnl + total_funding"
            )
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
    fills: tuple[SimFill, ...]
    equity_curve: tuple[EquitySnapshot, ...]
    initial_equity: Decimal
    final_cash: Decimal
    gross_realized_pnl: Decimal
    total_fees: Decimal
    net_realized_pnl: Decimal
    total_funding: Decimal
    net_pnl_after_fees_and_funding: Decimal
    realized_pnl: Decimal
    final_equity: Decimal
    intents: tuple[IntentRecord, ...] = ()
    instructions: tuple[TradeInstruction, ...] = ()
    final_positions: Mapping[str, Decimal] = field(default_factory=dict)
    final_average_costs: Mapping[str, Decimal] = field(default_factory=dict)
    equity_asset: str = "USDT"
    final_account_metrics: Mapping[str, Decimal] = field(default_factory=dict)
    completed: bool = True
    liquidated: bool = False
    bankrupt: bool = False
    termination_reason: SimulationTerminationReason | None = None
    termination_sequence: int | None = None
    margin_snapshots: tuple["MarginSnapshot", ...] = ()
    account_events: tuple["LiquidationEvent", ...] = ()
    funding_enabled: bool = False
    funding_source: str = "ZERO"
    funding_market_conditioned: bool = False
    funding_events: tuple["FundingSettlement", ...] = ()
    slippage_enabled: bool = False
    slippage_source: str = "ZERO"

    def __post_init__(self) -> None:
        from .funding import FundingSettlement
        from .margin import LiquidationEvent, MarginSnapshot

        if not self.equity_asset.strip():
            raise ValueError("equity_asset must not be empty")
        if self.total_fees < 0:
            raise ValueError("total_fees must be >= 0")
        if self.realized_pnl != self.net_realized_pnl:
            raise ValueError(
                "realized_pnl must equal net_realized_pnl"
            )
        if (
            self.net_pnl_after_fees_and_funding
            != self.net_realized_pnl + self.total_funding
        ):
            raise ValueError(
                "net_pnl_after_fees_and_funding must equal "
                "net_realized_pnl + total_funding"
            )
        if not self.funding_source.strip():
            raise ValueError("funding_source must not be empty")
        if not self.slippage_source.strip():
            raise ValueError("slippage_source must not be empty")
        for name in (
            "funding_enabled",
            "funding_market_conditioned",
            "slippage_enabled",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a bool")
        if any(
            not isinstance(event, FundingSettlement)
            for event in self.funding_events
        ):
            raise TypeError(
                "funding_events must contain FundingSettlement values"
            )
        if (
            sum(
                (
                    event.wallet_delta
                    for event in self.funding_events
                ),
                Decimal("0"),
            )
            != self.total_funding
        ):
            raise ValueError(
                "total_funding must equal funding event wallet deltas"
            )
        if not self.funding_enabled and self.funding_events:
            raise ValueError(
                "disabled funding cannot contain funding events"
            )
        for name in ("completed", "liquidated", "bankrupt"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a bool")
        if self.liquidated:
            if self.completed:
                raise ValueError(
                    "a liquidated result cannot be completed"
                )
            if (
                self.termination_reason
                != SimulationTerminationReason.LIQUIDATION
            ):
                raise ValueError(
                    "a liquidated result requires LIQUIDATION reason"
                )
            if self.termination_sequence is None:
                raise ValueError(
                    "a liquidated result requires termination_sequence"
                )
            if not self.account_events:
                raise ValueError(
                    "a liquidated result requires an account event"
                )
        else:
            if not self.completed:
                raise ValueError(
                    "a non-liquidated result must be completed"
                )
            if self.bankrupt:
                raise ValueError(
                    "bankrupt is only valid for liquidation results"
                )
            if self.termination_reason is not None:
                raise ValueError(
                    "non-liquidated result cannot have "
                    "termination_reason"
                )
            if self.termination_sequence is not None:
                raise ValueError(
                    "non-liquidated result cannot have "
                    "termination_sequence"
                )
            if self.account_events:
                raise ValueError(
                    "non-liquidated result cannot have account_events"
                )
        if self.termination_sequence is not None:
            if self.termination_sequence < 0:
                raise ValueError(
                    "termination_sequence must be >= 0"
                )
            if (
                not self.frames
                or self.frames[-1].sequence
                != self.termination_sequence
            ):
                raise ValueError(
                    "termination_sequence must match the final frame"
                )
        if any(
            not isinstance(snapshot, MarginSnapshot)
            for snapshot in self.margin_snapshots
        ):
            raise TypeError(
                "margin_snapshots must contain MarginSnapshot values"
            )
        if any(
            not isinstance(event, LiquidationEvent)
            for event in self.account_events
        ):
            raise TypeError(
                "account_events must contain LiquidationEvent values"
            )
        if self.liquidated:
            final_event = self.account_events[-1]
            if (
                final_event.sequence != self.termination_sequence
                or final_event.bankrupt != self.bankrupt
            ):
                raise ValueError(
                    "final account event must match termination state"
                )
            if (
                not self.margin_snapshots
                or self.margin_snapshots[-1]
                != final_event.snapshot
            ):
                raise ValueError(
                    "final margin snapshot must match "
                    "the liquidation event"
                )
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
