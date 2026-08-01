"""No-trade BTC holding baseline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HoldBtcConfig:
    strategy_id: str
    instrument: str

    def __post_init__(self) -> None:
        if not self.strategy_id.strip():
            raise ValueError("strategy_id must not be empty")
        if not self.instrument.strip():
            raise ValueError("instrument must not be empty")


class HoldBtcStrategy:
    """Record baseline lifecycle without producing trade decisions."""

    def __init__(self, config: HoldBtcConfig) -> None:
        self.config = config
        self.initialized = False
        self.market_observation_count = 0

    def initialize(self) -> None:
        if self.initialized:
            raise RuntimeError("strategy is already initialized")
        self.initialized = True

    def on_market(self) -> None:
        if not self.initialized:
            raise RuntimeError("strategy must be initialized first")
        self.market_observation_count += 1
