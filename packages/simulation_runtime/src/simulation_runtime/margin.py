"""Product-neutral margin calculation contracts.

The generic runtime owns the types and ports in this module. Product-specific
formulas, such as COIN-M inverse-contract PnL and collateral conversion, live
in adapters outside ``simulation_runtime``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, DecimalException, localcontext
from enum import StrEnum
from typing import Protocol

from market_protocol import MarketFrame

from .ledger import SimulationLedger
from .models import SimFill


CALCULATION_PRECISION = 50


def _decimal(name: str, value: object) -> Decimal:
    try:
        converted = Decimal(str(value))
    except (DecimalException, ValueError) as exc:
        raise ValueError(f"{name} must be a decimal number") from exc
    if not converted.is_finite():
        raise ValueError(f"{name} must be finite")
    return converted


class MaintenanceMarginSchedule(Protocol):
    """Calculate maintenance requirement in the notional asset.

    Returning a requirement in the notional asset keeps the schedule
    independent of settlement mechanics. A COIN-M model converts the returned
    USD amount to BTC at the mark, while a linear contract model can keep a
    quote-currency requirement unchanged.
    """

    def requirement(
        self,
        *,
        position_notional: Decimal,
    ) -> Decimal: ...


class MarkPriceSampling(StrEnum):
    CLOSE_ONLY = "CLOSE_ONLY"
    ADVERSE_EXTREME = "ADVERSE_EXTREME"


@dataclass(frozen=True, slots=True)
class FlatMaintenanceMarginSchedule:
    """One fixed maintenance-margin rate for every position size."""

    maintenance_margin_rate: Decimal

    def __post_init__(self) -> None:
        rate = _decimal(
            "maintenance_margin_rate",
            self.maintenance_margin_rate,
        )
        if rate < 0 or rate >= 1:
            raise ValueError(
                "maintenance_margin_rate must be >= 0 and < 1"
            )
        object.__setattr__(self, "maintenance_margin_rate", rate)

    def requirement(
        self,
        *,
        position_notional: Decimal,
    ) -> Decimal:
        notional = _decimal("position_notional", position_notional)
        if notional < 0:
            raise ValueError("position_notional must be >= 0")
        with localcontext() as context:
            context.prec = CALCULATION_PRECISION
            return notional * self.maintenance_margin_rate


@dataclass(frozen=True, slots=True)
class MaintenanceMarginTier:
    """One contiguous maintenance bracket in the notional asset.

    Positive notional uses ``(notional_floor, notional_cap]``. The first
    floor must be zero, adjacent tiers share a boundary, and ``None`` as the
    final cap means that the schedule is unbounded.
    """

    notional_floor: Decimal
    notional_cap: Decimal | None
    maintenance_margin_rate: Decimal
    maintenance_amount_deduction: Decimal

    def __post_init__(self) -> None:
        floor = _decimal("notional_floor", self.notional_floor)
        cap = (
            None
            if self.notional_cap is None
            else _decimal("notional_cap", self.notional_cap)
        )
        rate = _decimal(
            "maintenance_margin_rate",
            self.maintenance_margin_rate,
        )
        deduction = _decimal(
            "maintenance_amount_deduction",
            self.maintenance_amount_deduction,
        )
        if floor < 0:
            raise ValueError("notional_floor must be >= 0")
        if cap is not None and cap <= floor:
            raise ValueError("notional_cap must be > notional_floor")
        if rate < 0 or rate >= 1:
            raise ValueError(
                "maintenance_margin_rate must be >= 0 and < 1"
            )
        if deduction < 0:
            raise ValueError(
                "maintenance_amount_deduction must be >= 0"
            )
        object.__setattr__(self, "notional_floor", floor)
        object.__setattr__(self, "notional_cap", cap)
        object.__setattr__(self, "maintenance_margin_rate", rate)
        object.__setattr__(
            self,
            "maintenance_amount_deduction",
            deduction,
        )

    def requirement(self, position_notional: Decimal) -> Decimal:
        """Return this tier's unconverted maintenance requirement."""

        with localcontext() as context:
            context.prec = CALCULATION_PRECISION
            return (
                position_notional * self.maintenance_margin_rate
                - self.maintenance_amount_deduction
            )


@dataclass(frozen=True, slots=True)
class TieredMaintenanceMarginSchedule:
    """A validated, versioned maintenance-margin bracket snapshot."""

    product: str
    instrument: str
    source: str
    effective_at: str
    version: str
    content_hash: str
    tiers: tuple[MaintenanceMarginTier, ...]

    def __post_init__(self) -> None:
        for name in (
            "product",
            "instrument",
            "source",
            "effective_at",
            "version",
            "content_hash",
        ):
            normalized = str(getattr(self, name)).strip()
            if not normalized:
                raise ValueError(f"{name} must not be empty")
            object.__setattr__(self, name, normalized)

        tiers = tuple(self.tiers)
        if not tiers:
            raise ValueError("tiers must not be empty")
        if any(
            not isinstance(tier, MaintenanceMarginTier)
            for tier in tiers
        ):
            raise TypeError(
                "tiers must contain MaintenanceMarginTier values"
            )
        if tiers[0].notional_floor != 0:
            raise ValueError("the first tier must start at zero")

        for index, tier in enumerate(tiers):
            if index == 0:
                if tier.maintenance_amount_deduction != 0:
                    raise ValueError(
                        "the first tier deduction must be zero"
                    )
                continue

            previous = tiers[index - 1]
            if previous.notional_cap is None:
                raise ValueError(
                    "only the final tier may have an unbounded cap"
                )
            if tier.notional_floor != previous.notional_cap:
                raise ValueError(
                    "maintenance tiers must be contiguous"
                )
            if (
                tier.maintenance_margin_rate
                < previous.maintenance_margin_rate
            ):
                raise ValueError(
                    "maintenance margin rates must not decrease"
                )
            boundary = tier.notional_floor
            if (
                previous.requirement(boundary)
                != tier.requirement(boundary)
            ):
                raise ValueError(
                    "maintenance requirement must be continuous "
                    "between tiers"
                )

        object.__setattr__(self, "tiers", tiers)

    def tier_for(
        self,
        position_notional: Decimal,
    ) -> MaintenanceMarginTier | None:
        """Select the bracket for a nonnegative notional amount."""

        notional = _decimal("position_notional", position_notional)
        if notional < 0:
            raise ValueError("position_notional must be >= 0")
        if notional == 0:
            return None
        for tier in self.tiers:
            if (
                tier.notional_cap is None
                or notional <= tier.notional_cap
            ):
                return tier
        raise ValueError(
            "position_notional exceeds the schedule's maximum cap"
        )

    def requirement(
        self,
        *,
        position_notional: Decimal,
    ) -> Decimal:
        notional = _decimal("position_notional", position_notional)
        tier = self.tier_for(notional)
        if tier is None:
            return Decimal("0")
        requirement = tier.requirement(notional)
        if requirement < 0:
            raise ValueError(
                "maintenance requirement must be >= 0"
            )
        return requirement


@dataclass(frozen=True, slots=True)
class MarginConfig:
    """Static account inputs needed by a product margin model."""

    leverage: Decimal
    maintenance_schedule: MaintenanceMarginSchedule

    def __post_init__(self) -> None:
        leverage = _decimal("leverage", self.leverage)
        if leverage <= 0:
            raise ValueError("leverage must be > 0")
        if not callable(
            getattr(self.maintenance_schedule, "requirement", None)
        ):
            raise TypeError(
                "maintenance_schedule must provide requirement()"
            )
        object.__setattr__(self, "leverage", leverage)

    @property
    def initial_margin_rate(self) -> Decimal:
        return Decimal("1") / self.leverage


@dataclass(frozen=True, slots=True)
class MarginSnapshot:
    """One immutable, product-calculated margin account state.

    Monetary fields use ``settlement_asset`` except ``position_notional``,
    which uses ``notional_asset``. Prices are expressed as notional asset per
    settlement asset.
    """

    sequence: int
    timestamp: int
    instrument: str
    settlement_asset: str
    notional_asset: str
    mark_price: Decimal
    mark_price_source: str
    leverage: Decimal
    position_quantity: Decimal
    position_unit: str
    average_entry_price: Decimal
    position_notional: Decimal
    wallet_balance: Decimal
    unrealized_pnl: Decimal
    margin_balance: Decimal
    position_initial_margin: Decimal
    maintenance_margin: Decimal
    available_balance: Decimal
    margin_buffer: Decimal
    initial_margin_utilization: Decimal | None
    maintenance_margin_utilization: Decimal | None
    effective_leverage: Decimal | None
    estimated_liquidation_price: Decimal | None
    liquidation_triggered: bool
    bankrupt: bool

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("sequence must be >= 0")
        for name in (
            "instrument",
            "settlement_asset",
            "notional_asset",
            "mark_price_source",
            "position_unit",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must not be empty")
        if self.mark_price <= 0:
            raise ValueError("mark_price must be > 0")
        if self.leverage <= 0:
            raise ValueError("leverage must be > 0")
        if self.position_notional < 0:
            raise ValueError("position_notional must be >= 0")
        if self.position_initial_margin < 0:
            raise ValueError("position_initial_margin must be >= 0")
        if self.maintenance_margin < 0:
            raise ValueError("maintenance_margin must be >= 0")
        if self.position_quantity == 0:
            if self.average_entry_price != 0:
                raise ValueError(
                    "average_entry_price must be zero without a position"
                )
        elif self.average_entry_price <= 0:
            raise ValueError(
                "average_entry_price must be > 0 with a position"
            )
        for name in (
            "initial_margin_utilization",
            "maintenance_margin_utilization",
            "effective_leverage",
            "estimated_liquidation_price",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be >= 0 when defined")
        if not isinstance(self.liquidation_triggered, bool):
            raise TypeError("liquidation_triggered must be a bool")
        if not isinstance(self.bankrupt, bool):
            raise TypeError("bankrupt must be a bool")


@dataclass(frozen=True, slots=True)
class LiquidationEvent:
    """One terminal platform-liquidation trigger."""

    snapshot: MarginSnapshot
    mark_price_sampling: MarkPriceSampling
    maintenance_schedule_version: str
    intrabar_ordering_ambiguous: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, MarginSnapshot):
            raise TypeError("snapshot must be a MarginSnapshot")
        if not self.snapshot.liquidation_triggered:
            raise ValueError(
                "liquidation event requires a triggered snapshot"
            )
        if not isinstance(
            self.mark_price_sampling,
            MarkPriceSampling,
        ):
            raise TypeError(
                "mark_price_sampling must be a MarkPriceSampling"
            )
        if not self.maintenance_schedule_version.strip():
            raise ValueError(
                "maintenance_schedule_version must not be empty"
            )
        if not isinstance(
            self.intrabar_ordering_ambiguous,
            bool,
        ):
            raise TypeError(
                "intrabar_ordering_ambiguous must be a bool"
            )

    @property
    def sequence(self) -> int:
        return self.snapshot.sequence

    @property
    def timestamp(self) -> int:
        return self.snapshot.timestamp

    @property
    def instrument(self) -> str:
        return self.snapshot.instrument

    @property
    def bankrupt(self) -> bool:
        return self.snapshot.bankrupt


class MarginModel(Protocol):
    """Derive margin facts from an accounting ledger and one mark."""

    @property
    def maintenance_schedule_version(self) -> str: ...

    def snapshot(
        self,
        ledger: SimulationLedger,
        *,
        mark_price: Decimal,
        frame: MarketFrame,
        mark_price_source: str = "explicit",
    ) -> MarginSnapshot | None: ...

    def projected_snapshot(
        self,
        ledger: SimulationLedger,
        *,
        fill: SimFill,
        mark_price: Decimal,
        frame: MarketFrame,
        mark_price_source: str = "fill_price_proxy",
    ) -> MarginSnapshot | None: ...


class NoMarginModel:
    """Disable margin calculations for spot and mathematical probes."""

    @property
    def maintenance_schedule_version(self) -> str:
        return "none"

    def snapshot(
        self,
        ledger: SimulationLedger,
        *,
        mark_price: Decimal,
        frame: MarketFrame,
        mark_price_source: str = "explicit",
    ) -> None:
        return None

    def projected_snapshot(
        self,
        ledger: SimulationLedger,
        *,
        fill: SimFill,
        mark_price: Decimal,
        frame: MarketFrame,
        mark_price_source: str = "fill_price_proxy",
    ) -> None:
        return None
