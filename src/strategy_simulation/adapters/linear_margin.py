"""Margin calculations for one USD-M linear-contract account."""

from __future__ import annotations

from decimal import Decimal, localcontext

from market_protocol import MarketFrame
from simulation_runtime import (
    LinearLedger,
    MarginConfig,
    MarginSnapshot,
    SimFill,
    SimulationLedger,
)


CALCULATION_PRECISION = 50


class LinearContractMarginModel:
    """Derive isolated product arithmetic for a quote-settled position.

    The simulator remains a cold execution machine: this model enforces
    exchange-like initial/maintenance margin arithmetic, but contains no
    strategy risk policy.
    """

    def __init__(
        self,
        config: MarginConfig,
        *,
        instrument: str,
        settlement_asset: str = "USDT",
    ) -> None:
        if not isinstance(config, MarginConfig):
            raise TypeError("config must be a MarginConfig")
        if not instrument.strip():
            raise ValueError("instrument must not be empty")
        if not settlement_asset.strip():
            raise ValueError("settlement_asset must not be empty")
        self.config = config
        self.instrument = instrument
        self.settlement_asset = settlement_asset.upper()

    @property
    def maintenance_schedule_version(self) -> str:
        schedule = self.config.maintenance_schedule
        version = str(getattr(schedule, "version", "")).strip()
        if version:
            return version
        rate = getattr(schedule, "maintenance_margin_rate", None)
        if rate is not None:
            return f"flat-rate:{rate}"
        return type(schedule).__name__

    def snapshot(
        self,
        ledger: SimulationLedger,
        *,
        mark_price: Decimal,
        frame: MarketFrame,
        mark_price_source: str = "explicit",
    ) -> MarginSnapshot:
        if not isinstance(ledger, LinearLedger):
            raise TypeError(
                "LinearContractMarginModel requires LinearLedger"
            )
        if ledger.equity_asset.upper() != self.settlement_asset:
            raise ValueError("ledger and margin settlement assets differ")
        if frame.instrument != self.instrument:
            raise ValueError(
                "frame instrument does not match margin model: "
                f"{frame.instrument} != {self.instrument}"
            )
        if not mark_price_source.strip():
            raise ValueError("mark_price_source must not be empty")
        mark = Decimal(str(mark_price))
        if mark <= 0:
            raise ValueError("mark_price must be > 0")

        with localcontext() as context:
            context.prec = CALCULATION_PRECISION
            position = Decimal(
                ledger.positions.get(self.instrument, Decimal("0"))
            )
            average_entry = Decimal(
                ledger.average_costs.get(self.instrument, Decimal("0"))
            )
            wallet_balance = (
                ledger.initial_equity
                + ledger.net_pnl_after_fees_and_funding
            )
            unrealized_pnl = (
                position * (mark - average_entry)
                if position != 0
                else Decimal("0")
            )
            margin_balance = wallet_balance + unrealized_pnl
            position_notional = abs(position) * mark
            position_initial_margin = (
                position_notional / self.config.leverage
            )
            maintenance_margin = (
                self.config.maintenance_schedule.requirement(
                    position_notional=position_notional,
                )
            )
            if maintenance_margin < 0:
                raise ValueError(
                    "maintenance requirement must be >= 0"
                )
            available_balance = margin_balance - position_initial_margin
            margin_buffer = margin_balance - maintenance_margin
            if position_notional > 0 and margin_balance > 0:
                initial_utilization = (
                    position_initial_margin / margin_balance
                )
                maintenance_utilization = (
                    maintenance_margin / margin_balance
                )
                effective_leverage = position_notional / margin_balance
            else:
                initial_utilization = None
                maintenance_utilization = None
                effective_leverage = None

            return MarginSnapshot(
                sequence=frame.sequence,
                timestamp=frame.timestamp,
                instrument=self.instrument,
                settlement_asset=self.settlement_asset,
                notional_asset=self.settlement_asset,
                mark_price=mark,
                mark_price_source=mark_price_source,
                leverage=self.config.leverage,
                position_quantity=position,
                position_unit="BASE_ASSET",
                average_entry_price=average_entry,
                position_notional=position_notional,
                wallet_balance=wallet_balance,
                unrealized_pnl=unrealized_pnl,
                margin_balance=margin_balance,
                position_initial_margin=position_initial_margin,
                maintenance_margin=maintenance_margin,
                available_balance=available_balance,
                margin_buffer=margin_buffer,
                initial_margin_utilization=initial_utilization,
                maintenance_margin_utilization=maintenance_utilization,
                effective_leverage=effective_leverage,
                estimated_liquidation_price=(
                    self._estimated_liquidation_price(
                        position=position,
                        average_entry=average_entry,
                        wallet_balance=wallet_balance,
                    )
                ),
                liquidation_triggered=(
                    position_notional > 0
                    and margin_balance <= maintenance_margin
                ),
                bankrupt=margin_balance <= 0,
            )

    def projected_snapshot(
        self,
        ledger: SimulationLedger,
        *,
        fill: SimFill,
        mark_price: Decimal,
        frame: MarketFrame,
        mark_price_source: str = "fill_price_proxy",
    ) -> MarginSnapshot:
        if not isinstance(ledger, LinearLedger):
            raise TypeError(
                "LinearContractMarginModel requires LinearLedger"
            )
        projected = ledger.clone()
        projected.apply(fill)
        return self.snapshot(
            projected,
            mark_price=mark_price,
            frame=frame,
            mark_price_source=mark_price_source,
        )

    def _estimated_liquidation_price(
        self,
        *,
        position: Decimal,
        average_entry: Decimal,
        wallet_balance: Decimal,
    ) -> Decimal | None:
        """Solve margin_balance(price) == maintenance_margin(price)."""

        if position == 0:
            return None

        def buffer(price: Decimal) -> Decimal:
            notional = abs(position) * price
            maintenance = self.config.maintenance_schedule.requirement(
                position_notional=notional,
            )
            return (
                wallet_balance
                + position * (price - average_entry)
                - maintenance
            )

        with localcontext() as context:
            context.prec = CALCULATION_PRECISION
            low = average_entry
            high = average_entry
            if position > 0:
                for _ in range(256):
                    if buffer(low) <= 0:
                        break
                    low /= Decimal("2")
                else:
                    return None
                for _ in range(256):
                    if buffer(high) >= 0:
                        break
                    high *= Decimal("2")
                else:
                    return None
            else:
                for _ in range(256):
                    if buffer(low) >= 0:
                        break
                    low /= Decimal("2")
                else:
                    return None
                for _ in range(256):
                    if buffer(high) <= 0:
                        break
                    high *= Decimal("2")
                else:
                    return None

            for _ in range(180):
                midpoint = (low + high) / Decimal("2")
                midpoint_buffer = buffer(midpoint)
                if midpoint_buffer == 0:
                    return midpoint
                if position > 0:
                    if midpoint_buffer < 0:
                        low = midpoint
                    else:
                        high = midpoint
                elif midpoint_buffer > 0:
                    low = midpoint
                else:
                    high = midpoint
            candidate = (low + high) / Decimal("2")
            if not candidate.is_finite() or candidate <= 0:
                return None
            return candidate
