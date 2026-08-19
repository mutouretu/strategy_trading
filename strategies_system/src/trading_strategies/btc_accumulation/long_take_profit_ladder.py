"""StrategyDefinition for initial entry followed by ladder take-profit."""

from __future__ import annotations

from ..entry_then_ladder_exit import (
    ENTRY_RULE_KEY,
    EXIT_RULE_KEY,
    EntryThenLadderExitParameters,
    EntryThenLadderExitStrategyDefinition,
)
from ..strategy import (
    StrategyDefinition,
    StrategyDirection,
    StrategyProductType,
    StrategySpec,
)
from .models import CoinMLongTakeProfitLadderConfig


TAKE_PROFIT_RULE_KEY = EXIT_RULE_KEY


class CoinMLongTakeProfitLadderStrategyDefinition(
    StrategyDefinition[CoinMLongTakeProfitLadderConfig]
):
    """Resolve legacy COIN-M parameters into two product-neutral Rules."""

    strategy_type = EntryThenLadderExitStrategyDefinition.strategy_type

    @classmethod
    def definition(cls):
        return EntryThenLadderExitStrategyDefinition.definition()

    def bind(self, parameters: CoinMLongTakeProfitLadderConfig) -> StrategySpec:
        if not isinstance(parameters, CoinMLongTakeProfitLadderConfig):
            raise TypeError(
                "parameters must be CoinMLongTakeProfitLadderConfig"
            )
        sizing_parameters = {
            "sizing_safety_buffer_ratio": (
                parameters.sizing_safety_buffer_ratio
            ),
        }
        if parameters.target_liquidation_price is not None:
            sizing_parameters["target_liquidation_price"] = (
                parameters.target_liquidation_price
            )
        if parameters.entry_effective_leverage is not None:
            sizing_parameters["entry_effective_leverage"] = (
                parameters.entry_effective_leverage
            )
        return EntryThenLadderExitStrategyDefinition().bind(
            EntryThenLadderExitParameters(
                strategy_id=parameters.strategy_id,
                instrument=parameters.instrument,
                direction=StrategyDirection.LONG,
                product_type=StrategyProductType.INVERSE_PERPETUAL,
                entry_sizing_policy=parameters.entry_sizing_mode.value,
                entry_sizing_parameters=sizing_parameters,
                first_exit_ratio=parameters.first_take_profit_ratio,
                exit_end_price=parameters.take_profit_end_price,
                exit_level_count=parameters.take_profit_count,
                tick_size=parameters.tick_size,
                quantity_step=parameters.quantity_step,
                quantity_unit="contracts",
            )
        )
