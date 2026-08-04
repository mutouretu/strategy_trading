"""Funding calculation for coin-margined inverse contracts."""

from __future__ import annotations

from decimal import Decimal
from typing import Mapping

from market_protocol import MarketFrame
from simulation_runtime import (
    FixedFundingSchedule,
    FundingSettlement,
    SimulationLedger,
)

from .inverse_ledger import InverseContractLedger


class FixedRateInverseContractFundingModel:
    """Apply a fixed rate to inverse-contract value in the base asset."""

    enabled = True
    source = "FIXED"
    market_conditioned = False

    def __init__(
        self,
        *,
        funding_rate: Decimal,
        funding_interval_seconds: int,
        settlement_offset_seconds: int = 0,
    ) -> None:
        self.funding_rate = Decimal(funding_rate)
        self.schedule = FixedFundingSchedule(
            interval_seconds=funding_interval_seconds,
            offset_seconds=settlement_offset_seconds,
        )
        if not self.funding_rate.is_finite():
            raise ValueError("funding_rate must be finite")

    def settle(
        self,
        frame: MarketFrame,
        ledger: SimulationLedger,
        marks: Mapping[str, Decimal],
    ) -> FundingSettlement | None:
        if not isinstance(ledger, InverseContractLedger):
            raise TypeError(
                "FixedRateInverseContractFundingModel requires "
                "InverseContractLedger"
            )
        if frame.instrument != ledger.instrument:
            raise ValueError(
                "frame instrument does not match ledger: "
                f"{frame.instrument} != {ledger.instrument}"
            )
        if (
            self.funding_rate == 0
            or not self.schedule.includes(frame.timestamp)
        ):
            return None

        position = ledger.position_quantity
        if position == 0:
            return None
        try:
            mark_price = Decimal(marks[ledger.instrument])
        except KeyError as exc:
            raise KeyError(
                f"missing funding mark for: {ledger.instrument}"
            ) from exc
        if mark_price <= 0:
            raise ValueError("funding mark price must be > 0")

        direction = Decimal("1") if position > 0 else Decimal("-1")
        position_notional = abs(position) * ledger.contract_size
        position_value = position_notional / mark_price
        wallet_delta = (
            -direction * position_value * self.funding_rate
        )
        return FundingSettlement(
            settlement_id=(
                f"funding:{self.source.lower()}:{ledger.instrument}:"
                f"{frame.timestamp}"
            ),
            sequence=frame.sequence,
            timestamp=frame.timestamp,
            instrument=ledger.instrument,
            source=self.source,
            funding_rate=self.funding_rate,
            position_quantity=position,
            mark_price=mark_price,
            mark_price_source="market_frame_close",
            position_notional=position_notional,
            notional_asset=ledger.notional_asset,
            position_value=position_value,
            settlement_asset=ledger.base_asset,
            wallet_delta=wallet_delta,
        )
