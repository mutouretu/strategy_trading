from __future__ import annotations

from typing import Protocol, Sequence

from market_protocol import MarketFrame

from .models import SimFill, SimulationDecision


class SimulationDecisionPort(Protocol):
    """Callback port through which the runtime requests trading decisions.

    Implementations may keep decision state internally. Every callback
    returns the complete desired-order set, allowing the runtime to derive
    creations and cancellations without knowing the caller's domain concepts.
    """

    def initialize(self, frame: MarketFrame) -> SimulationDecision: ...

    def on_market(self, frame: MarketFrame) -> SimulationDecision: ...

    def on_fills(
        self,
        fills: Sequence[SimFill],
    ) -> SimulationDecision: ...
