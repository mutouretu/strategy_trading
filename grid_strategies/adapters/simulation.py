"""Simulation adapter for the single following-grid strategy."""

from __future__ import annotations

from typing import Sequence

from grid_rule import (
    GridFill,
    GridOrderIntent,
    GridOrderSide,
)
from grid_strategies.single_following_grid import (
    SingleFollowingGridStrategy,
    SingleFollowingGridStrategyConfig,
)
from market_protocol import MarketFrame
from simulation_runtime import (
    OrderSide,
    OrderType,
    SimFill,
    SimOrder,
    SimulationDecision,
)


class SingleFollowingGridSimulationAdapter:
    """Expose the strategy through the generic simulation decision port."""

    def __init__(
        self,
        config: SingleFollowingGridStrategyConfig,
    ) -> None:
        self.strategy = SingleFollowingGridStrategy(config)

    def initialize(self, frame: MarketFrame) -> SimulationDecision:
        self._check_instrument(frame.instrument)
        return self._decision(self.strategy.initialize(frame.close))

    def on_market(self, frame: MarketFrame) -> SimulationDecision:
        self._check_instrument(frame.instrument)
        return self._decision(self.strategy.on_market(frame.close))

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
        return self._decision(self.strategy.on_fills(grid_fills))

    def _decision(
        self,
        intents: tuple[GridOrderIntent, ...],
    ) -> SimulationDecision:
        rule = self.strategy.config.rule
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
                        "strategy": "single_following_grid",
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
                    },
                )
                for intent in intents
            )
        )

    def _check_instrument(self, instrument: str) -> None:
        expected = self.strategy.config.rule.instrument
        if instrument != expected:
            raise ValueError(
                f"unexpected instrument {instrument}; expected {expected}"
            )
