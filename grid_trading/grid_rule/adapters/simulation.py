from __future__ import annotations

from typing import Sequence

from grid_rule import (
    GridOrderIntent,
    GridRuleConfig,
    GridRuleEngine,
)
from grid_rule.adapters.passive_execution import (
    PassiveGridIntentBook,
    simulation_fills_to_grid_fills,
)
from market_protocol import MarketFrame
from simulation_runtime import (
    IntentSnapshot,
    SimFill,
    TradeInstruction,
)


class GridRuleSimulationAdapter:
    """Resolve one grid rule's passive intents for the simulation runtime.

    The adapter owns passive intent timing and exposes explicit trades only.
    """

    def __init__(self, config: GridRuleConfig) -> None:
        self.engine = GridRuleEngine(config)
        self._intent_book = PassiveGridIntentBook()

    def initialize(self, frame: MarketFrame) -> None:
        self._check_instrument(frame.instrument)
        intents = self.engine.initialize(frame.close)
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
        intents = self.engine.on_market(frame.close)
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
        intents = self.engine.on_fills(grid_fills)
        self._intent_book.synchronize(
            intents,
            current_sequence=current_sequence,
        )

    def _tags(self, intent: GridOrderIntent) -> dict[str, str]:
        return {
            "rule_engine": "grid_rule",
            "market_type": self.engine.config.market_type.value,
            "quantity_unit": (
                "contracts"
                if self.engine.config.market_type.value == "coinm"
                else "base_asset"
            ),
            "contract_size": str(self.engine.config.contract_size),
            "cell_id": intent.cell_id,
            "role": intent.role.value,
            "cycle": str(intent.cycle),
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
        if instrument != self.engine.config.instrument:
            raise ValueError(
                f"unexpected instrument {instrument}; "
                f"expected {self.engine.config.instrument}"
            )
