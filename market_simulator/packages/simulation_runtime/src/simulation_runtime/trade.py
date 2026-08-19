from __future__ import annotations

from typing import Protocol, Sequence

from market_protocol import MarketFrame

from .funding import FundingSettlement
from .models import SimFill, TradeInstruction


class SimulationTradePort(Protocol):
    """Provide explicit trades for each frame without runtime order matching."""

    def initialize(self, frame: MarketFrame) -> None: ...

    def instructions_for(
        self,
        frame: MarketFrame,
    ) -> tuple[TradeInstruction, ...]: ...

    def on_fills(
        self,
        fills: Sequence[SimFill],
    ) -> None: ...

    def on_market(self, frame: MarketFrame) -> None: ...


class SimulationFundingPort(Protocol):
    """Optional observer for funding already booked by the account ledger."""

    def on_funding(self, settlement: FundingSettlement) -> None: ...


class SimulationAccountingPort(Protocol):
    """Optional ordered account facts, including terminal post-fill facts."""

    def on_accounting(
        self,
        fills: Sequence[SimFill],
        funding: FundingSettlement | None,
    ) -> None: ...
