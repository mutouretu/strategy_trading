from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Protocol

from market_protocol import MarketFrame

from .ledger import SimulationLedger


@dataclass(frozen=True, slots=True)
class FixedFundingSchedule:
    """A deterministic schedule evaluated only at visible frame times."""

    interval_seconds: int
    offset_seconds: int = 0

    def __post_init__(self) -> None:
        if (
            isinstance(self.interval_seconds, bool)
            or not isinstance(self.interval_seconds, int)
            or self.interval_seconds <= 0
        ):
            raise ValueError("interval_seconds must be a positive integer")
        if (
            isinstance(self.offset_seconds, bool)
            or not isinstance(self.offset_seconds, int)
            or self.offset_seconds < 0
            or self.offset_seconds >= self.interval_seconds
        ):
            raise ValueError(
                "offset_seconds must be an integer in "
                "[0, interval_seconds)"
            )

    def includes(self, timestamp: int) -> bool:
        interval_milliseconds = self.interval_seconds * 1_000
        offset_milliseconds = self.offset_seconds * 1_000
        return (
            timestamp - offset_milliseconds
        ) % interval_milliseconds == 0


@dataclass(frozen=True, slots=True)
class FundingSettlement:
    """One signed funding cash flow applied to a settlement wallet.

    ``wallet_delta`` is positive when the account receives funding and
    negative when it pays funding.
    """

    settlement_id: str
    sequence: int
    timestamp: int
    instrument: str
    source: str
    funding_rate: Decimal
    position_quantity: Decimal
    mark_price: Decimal
    mark_price_source: str
    position_notional: Decimal
    notional_asset: str
    position_value: Decimal
    settlement_asset: str
    wallet_delta: Decimal

    def __post_init__(self) -> None:
        for name in (
            "settlement_id",
            "instrument",
            "source",
            "mark_price_source",
            "notional_asset",
            "settlement_asset",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must not be empty")
        if self.sequence < 0:
            raise ValueError("sequence must be >= 0")
        for name in (
            "funding_rate",
            "position_quantity",
            "mark_price",
            "position_notional",
            "position_value",
            "wallet_delta",
        ):
            if not getattr(self, name).is_finite():
                raise ValueError(f"{name} must be finite")
        if self.funding_rate == 0:
            raise ValueError("funding_rate must not be zero")
        if self.position_quantity == 0:
            raise ValueError("position_quantity must not be zero")
        if self.mark_price <= 0:
            raise ValueError("mark_price must be > 0")
        if self.position_notional <= 0:
            raise ValueError("position_notional must be > 0")
        if self.position_value <= 0:
            raise ValueError("position_value must be > 0")
        if self.wallet_delta == 0:
            raise ValueError("wallet_delta must not be zero")


class FundingModel(Protocol):
    """Calculate a funding settlement without mutating the ledger."""

    @property
    def enabled(self) -> bool: ...

    @property
    def source(self) -> str: ...

    @property
    def market_conditioned(self) -> bool: ...

    def settle(
        self,
        frame: MarketFrame,
        ledger: SimulationLedger,
        marks: Mapping[str, Decimal],
    ) -> FundingSettlement | None: ...


class ZeroFundingModel:
    """Default funding model preserving previous simulation results."""

    enabled = False
    source = "ZERO"
    market_conditioned = False

    def settle(
        self,
        frame: MarketFrame,
        ledger: SimulationLedger,
        marks: Mapping[str, Decimal],
    ) -> FundingSettlement | None:
        del frame, ledger, marks
        return None


class FixedRateFundingModel:
    """Fixed funding rate for linear quote-settled positions."""

    enabled = True
    source = "FIXED"
    market_conditioned = False

    def __init__(
        self,
        *,
        funding_rate: Decimal,
        funding_interval_seconds: int,
        settlement_offset_seconds: int = 0,
        funding_asset: str | None = None,
    ) -> None:
        self.funding_rate = Decimal(funding_rate)
        self.schedule = FixedFundingSchedule(
            interval_seconds=funding_interval_seconds,
            offset_seconds=settlement_offset_seconds,
        )
        self.funding_asset = (
            None
            if funding_asset is None
            else funding_asset.strip().upper()
        )
        if not self.funding_rate.is_finite():
            raise ValueError("funding_rate must be finite")
        if self.funding_asset is not None and not self.funding_asset:
            raise ValueError("funding_asset must not be empty")

    def settle(
        self,
        frame: MarketFrame,
        ledger: SimulationLedger,
        marks: Mapping[str, Decimal],
    ) -> FundingSettlement | None:
        if (
            self.funding_rate == 0
            or not self.schedule.includes(frame.timestamp)
        ):
            return None
        position = Decimal(
            ledger.positions.get(frame.instrument, Decimal("0"))
        )
        if position == 0:
            return None
        try:
            mark_price = Decimal(marks[frame.instrument])
        except KeyError as exc:
            raise KeyError(
                f"missing funding mark for: {frame.instrument}"
            ) from exc
        if mark_price <= 0:
            raise ValueError("funding mark price must be > 0")

        direction = Decimal("1") if position > 0 else Decimal("-1")
        position_notional = abs(position) * mark_price
        wallet_delta = (
            -direction * position_notional * self.funding_rate
        )
        settlement_asset = (
            self.funding_asset or ledger.equity_asset.upper()
        )
        return FundingSettlement(
            settlement_id=(
                f"funding:{self.source.lower()}:{frame.instrument}:"
                f"{frame.timestamp}"
            ),
            sequence=frame.sequence,
            timestamp=frame.timestamp,
            instrument=frame.instrument,
            source=self.source,
            funding_rate=self.funding_rate,
            position_quantity=position,
            mark_price=mark_price,
            mark_price_source="market_frame_close",
            position_notional=position_notional,
            notional_asset=settlement_asset,
            position_value=position_notional,
            settlement_asset=settlement_asset,
            wallet_delta=wallet_delta,
        )
