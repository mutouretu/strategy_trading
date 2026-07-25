from __future__ import annotations

from typing import Sequence

from grid_rule import (
    GridFill,
    GridOrderIntent,
    GridOrderSide,
    GridRuleConfig,
    GridRuleEngine,
)
from market_protocol import MarketFrame
from simulation_runtime import (
    OrderSide,
    OrderType,
    SimFill,
    SimOrder,
    SimulationDecision,
)


class GridRuleSimulationAdapter:
    """Expose one grid rule engine through the generic simulation port."""

    def __init__(self, config: GridRuleConfig) -> None:
        self.engine = GridRuleEngine(config)

    def initialize(self, frame: MarketFrame) -> SimulationDecision:
        self._check_instrument(frame.instrument)
        return self._output(self.engine.initialize(frame.close))

    def on_market(self, frame: MarketFrame) -> SimulationDecision:
        self._check_instrument(frame.instrument)
        return self._output(self.engine.on_market(frame.close))

    def on_fills(
        self,
        fills: Sequence[SimFill],
    ) -> SimulationDecision:
        grid_fills = tuple(
            GridFill(
                order_key=fill.order_key,
                instrument=fill.instrument,
                side=GridOrderSide(fill.side.value),
                price=fill.price,
                quantity=fill.quantity,
                sequence=fill.sequence,
                timestamp=fill.timestamp,
            )
            for fill in fills
        )
        return self._output(self.engine.on_fills(grid_fills))

    def _output(
        self,
        intents: tuple[GridOrderIntent, ...],
    ) -> SimulationDecision:
        return SimulationDecision(
            tuple(
                SimOrder(
                    order_key=intent.order_key,
                    instrument=intent.instrument,
                    side=OrderSide(intent.side.value),
                    order_type=OrderType.LIMIT,
                    quantity=intent.quantity,
                    limit_price=intent.price,
                    tags={
                        "rule_engine": "grid_rule",
                        "market_type": self.engine.config.market_type.value,
                        "quantity_unit": (
                            "contracts"
                            if self.engine.config.market_type.value == "coinm"
                            else "base_asset"
                        ),
                        "contract_size": str(
                            self.engine.config.contract_size
                        ),
                        "cell_id": intent.cell_id,
                        "role": intent.role.value,
                        "cycle": str(intent.cycle),
                    },
                )
                for intent in intents
            )
        )

    def _check_instrument(self, instrument: str) -> None:
        if instrument != self.engine.config.instrument:
            raise ValueError(
                f"unexpected instrument {instrument}; "
                f"expected {self.engine.config.instrument}"
            )
