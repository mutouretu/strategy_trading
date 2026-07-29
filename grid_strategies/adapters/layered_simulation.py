"""Simulation adapter for the layered following-grid strategy."""

from __future__ import annotations

from typing import Sequence

from grid_rule import GridOrderIntent
from grid_rule.adapters.passive_execution import (
    PassiveGridIntentBook,
    simulation_fills_to_grid_fills,
)
from grid_strategies.layered_following_grid import (
    LayeredFollowingGridStrategy,
    LayeredFollowingGridStrategyConfig,
)
from market_protocol import MarketFrame
from simulation_runtime import (
    IntentSnapshot,
    SimFill,
    TradeInstruction,
)


class LayeredFollowingGridSimulationAdapter:
    """Resolve layered following-grid passive intents for simulation.

    The adapter owns passive intent timing and exposes explicit trades only.
    """

    def __init__(
        self,
        config: LayeredFollowingGridStrategyConfig,
    ) -> None:
        self.strategy = LayeredFollowingGridStrategy(config)
        self._intent_book = PassiveGridIntentBook()

    def initialize(self, frame: MarketFrame) -> None:
        self._check_instrument(frame.instrument)
        intents = self.strategy.initialize(frame.close)
        self._intent_book.synchronize(
            intents,
            current_sequence=frame.sequence,
        )

    def instructions_for(
        self,
        frame: MarketFrame,
    ) -> tuple[TradeInstruction, ...]:
        self._check_instrument(frame.instrument)
        return self._intent_book.instructions_for(
            frame,
            tags_for=self._tags,
        )

    def visible_intents(self) -> tuple[IntentSnapshot, ...]:
        return self._intent_book.visible_intents(tags_for=self._tags)

    def on_market(self, frame: MarketFrame) -> None:
        self._check_instrument(frame.instrument)
        intents = self.strategy.on_market(frame.close)
        self._intent_book.synchronize(
            intents,
            current_sequence=frame.sequence,
        )

    def on_fills(
        self,
        fills: Sequence[SimFill],
    ) -> None:
        current_sequence = self._fill_sequence(fills)
        grid_fills = simulation_fills_to_grid_fills(fills)
        intents = self.strategy.on_fills(grid_fills)
        self._intent_book.synchronize(
            intents,
            current_sequence=current_sequence,
        )

    def _tags(self, intent: GridOrderIntent) -> dict[str, str]:
        rule = self.strategy.config.rule_template
        return {
            "strategy": "layered_following_grid",
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
            **{
                key: value
                for key, value in self.strategy.order_context(
                    intent.order_key
                ).items()
                if key != "grid_state"
            },
        }

    @staticmethod
    def _fill_sequence(fills: Sequence[SimFill]) -> int:
        if not fills:
            raise ValueError("fills must not be empty")
        sequences = {fill.sequence for fill in fills}
        if len(sequences) != 1:
            raise ValueError("all fills must belong to the same frame")
        return next(iter(sequences))

    def _check_instrument(self, instrument: str) -> None:
        expected = self.strategy.config.rule_template.instrument
        if instrument != expected:
            raise ValueError(
                f"unexpected instrument {instrument}; expected {expected}"
            )
