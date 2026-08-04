from __future__ import annotations

from typing import Sequence

from market_protocol import MarketFrame
from simulation_runtime import IntentSnapshot, SimFill, TradeInstruction

from trading_strategies.baselines import HoldBtcStrategy


class HoldBtcSimulationAdapter:
    def __init__(self, strategy: HoldBtcStrategy) -> None:
        self.strategy = strategy

    def initialize(self, frame: MarketFrame) -> None:
        self._validate(frame)
        self.strategy.initialize()

    def instructions_for(self, frame: MarketFrame) -> tuple[TradeInstruction, ...]:
        self._validate(frame)
        return ()

    def on_fills(self, fills: Sequence[SimFill]) -> None:
        if fills:
            raise RuntimeError("hold-btc baseline must not receive fills")

    def on_market(self, frame: MarketFrame) -> None:
        self._validate(frame)
        self.strategy.on_market()

    def visible_intents(self) -> tuple[IntentSnapshot, ...]:
        return ()

    def _validate(self, frame: MarketFrame) -> None:
        if frame.instrument != self.strategy.config.instrument:
            raise ValueError("market and strategy instruments must match")
