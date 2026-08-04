"""Margin calculations for one COIN-M inverse-contract account."""

from __future__ import annotations

from decimal import Decimal, localcontext

from market_protocol import MarketFrame
from simulation_runtime import (
    MarginConfig,
    MarginSnapshot,
    SimFill,
    SimulationLedger,
)

from .inverse_ledger import InverseContractLedger


CALCULATION_PRECISION = 50


class InverseContractMarginModel:
    """Derive COIN-M margin facts without mutating the ledger."""

    def __init__(self, config: MarginConfig) -> None:
        if not isinstance(config, MarginConfig):
            raise TypeError("config must be a MarginConfig")
        self.config = config

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
        if not isinstance(ledger, InverseContractLedger):
            raise TypeError(
                "InverseContractMarginModel requires "
                "InverseContractLedger"
            )
        mark = Decimal(str(mark_price))
        if mark <= 0:
            raise ValueError("mark_price must be > 0")
        if frame.instrument != ledger.instrument:
            raise ValueError(
                "frame instrument does not match ledger: "
                f"{frame.instrument} != {ledger.instrument}"
            )
        if not mark_price_source.strip():
            raise ValueError("mark_price_source must not be empty")

        with localcontext() as context:
            context.prec = CALCULATION_PRECISION
            position = ledger.position_quantity
            position_notional = abs(position) * ledger.contract_size
            average_entry = ledger.average_entry_price
            unrealized_pnl = ledger.unrealized_pnl(
                {ledger.instrument: mark}
            )
            wallet_balance = ledger.futures_wallet_balance
            margin_balance = wallet_balance + unrealized_pnl

            if position_notional == 0:
                position_initial_margin = Decimal("0")
                maintenance_margin = Decimal("0")
                maintenance_notional = Decimal("0")
            else:
                position_initial_margin = (
                    position_notional
                    / mark
                    / self.config.leverage
                )
                maintenance_notional = (
                    self.config.maintenance_schedule.requirement(
                        position_notional=position_notional,
                    )
                )
                if maintenance_notional < 0:
                    raise ValueError(
                        "maintenance requirement must be >= 0"
                    )
                maintenance_margin = maintenance_notional / mark

            available_balance = (
                margin_balance - position_initial_margin
            )
            margin_buffer = margin_balance - maintenance_margin
            if position_notional > 0 and margin_balance > 0:
                initial_margin_utilization = (
                    position_initial_margin / margin_balance
                )
                maintenance_margin_utilization = (
                    maintenance_margin / margin_balance
                )
                effective_leverage = (
                    position_notional / (margin_balance * mark)
                )
            else:
                initial_margin_utilization = None
                maintenance_margin_utilization = None
                effective_leverage = None

            estimated_liquidation_price = (
                self._estimated_liquidation_price(
                    position=position,
                    position_notional=position_notional,
                    average_entry=average_entry,
                    wallet_balance=wallet_balance,
                    maintenance_notional=maintenance_notional,
                )
            )
            liquidation_triggered = (
                position_notional > 0
                and margin_balance <= maintenance_margin
            )
            bankrupt = margin_balance <= 0

            return MarginSnapshot(
                sequence=frame.sequence,
                timestamp=frame.timestamp,
                instrument=ledger.instrument,
                settlement_asset=ledger.base_asset,
                notional_asset=ledger.notional_asset,
                mark_price=mark,
                mark_price_source=mark_price_source,
                leverage=self.config.leverage,
                position_quantity=position,
                position_unit="CONTRACT",
                average_entry_price=average_entry,
                position_notional=position_notional,
                wallet_balance=wallet_balance,
                unrealized_pnl=unrealized_pnl,
                margin_balance=margin_balance,
                position_initial_margin=position_initial_margin,
                maintenance_margin=maintenance_margin,
                available_balance=available_balance,
                margin_buffer=margin_buffer,
                initial_margin_utilization=(
                    initial_margin_utilization
                ),
                maintenance_margin_utilization=(
                    maintenance_margin_utilization
                ),
                effective_leverage=effective_leverage,
                estimated_liquidation_price=(
                    estimated_liquidation_price
                ),
                liquidation_triggered=liquidation_triggered,
                bankrupt=bankrupt,
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
        """Apply one fill to a ledger copy and value the projected account."""

        if not isinstance(ledger, InverseContractLedger):
            raise TypeError(
                "InverseContractMarginModel requires "
                "InverseContractLedger"
            )
        projected_ledger = ledger.clone()
        projected_ledger.apply(fill)
        return self.snapshot(
            projected_ledger,
            mark_price=mark_price,
            frame=frame,
            mark_price_source=mark_price_source,
        )

    @staticmethod
    def _estimated_liquidation_price(
        *,
        position: Decimal,
        position_notional: Decimal,
        average_entry: Decimal,
        wallet_balance: Decimal,
        maintenance_notional: Decimal,
    ) -> Decimal | None:
        """Solve margin balance(P) == maintenance margin(P).

        COIN-M contract notional is fixed in USD, so its maintenance bracket
        does not change while solving for the candidate mark price.
        """

        if position == 0:
            return None
        direction = Decimal("1") if position > 0 else Decimal("-1")
        denominator = (
            wallet_balance
            + direction * position_notional / average_entry
        )
        if denominator == 0:
            return None
        numerator = (
            direction * position_notional
            + maintenance_notional
        )
        price = numerator / denominator
        if not price.is_finite() or price <= 0:
            return None
        return price
