"""Simulation adapter for a single fixed-range grid strategy."""

from __future__ import annotations

from typing import Sequence

from grid_rule import GridOrderIntent
from grid_rule.adapters.passive_execution import (
    PassiveGridIntentBook,
    simulation_fills_to_grid_fills,
)
from market_protocol import MarketFrame
from simulation_runtime import IntentSnapshot, SimFill, TradeInstruction
from trading_strategies.grid_following import (
    FixedGridStrategy,
    FixedGridStrategyConfig,
)

from .grid_rule_engine import GridRuleEngineFactory


class FixedGridSimulationAdapter:
    """Translate one fixed grid's passive intents to runtime instructions."""

    def __init__(self, config: FixedGridStrategyConfig) -> None:
        self.strategy = FixedGridStrategy(config, GridRuleEngineFactory())
        self._intent_book = PassiveGridIntentBook()

    def initialize(self, frame: MarketFrame) -> None:
        self._check_instrument(frame.instrument)
        self._intent_book.synchronize(
            self.strategy.initialize(frame.close),
            current_sequence=frame.sequence,
        )

    def instructions_for(
        self, frame: MarketFrame
    ) -> tuple[TradeInstruction, ...]:
        self._check_instrument(frame.instrument)
        return self._intent_book.instructions_for(frame, tags_for=self._tags)

    def visible_intents(self) -> tuple[IntentSnapshot, ...]:
        return self._intent_book.visible_intents(tags_for=self._tags)

    def on_market(self, frame: MarketFrame) -> None:
        self._check_instrument(frame.instrument)
        self._intent_book.synchronize(
            self.strategy.on_market(frame.close),
            current_sequence=frame.sequence,
        )

    def on_fills(self, fills: Sequence[SimFill]) -> None:
        if not fills:
            raise ValueError("fills must not be empty")
        sequences = {fill.sequence for fill in fills}
        if len(sequences) != 1:
            raise ValueError("all fills must belong to the same frame")
        self._intent_book.synchronize(
            self.strategy.on_fills(
                simulation_fills_to_grid_fills(fills)
            ),
            current_sequence=next(iter(sequences)),
        )

    def _tags(self, intent: GridOrderIntent) -> dict[str, str]:
        rule = self.strategy.config.rule
        return {
            "strategy": "fixed_grid",
            "strategy_id": self.strategy.config.strategy_id,
            "rule_engine": "grid_rule",
            "market_type": rule.market_type.value,
            "quantity_unit": (
                "contracts"
                if rule.market_type.value == "coinm"
                else "base_asset"
            ),
            "contract_size": str(rule.contract_size),
            "cell_id": intent.cell_id,
            "role": intent.role.value,
            "cycle": str(intent.cycle),
        }

    def _check_instrument(self, instrument: str) -> None:
        expected = self.strategy.config.rule.instrument
        if instrument != expected:
            raise ValueError(
                f"unexpected instrument {instrument}; expected {expected}"
            )
