"""Rule-input routing for the COIN-M ladder StrategyApplication."""

from __future__ import annotations

from collections.abc import Mapping

from market_protocol import MarketFrame
from simulation_runtime import SimFill
from strategy_application import (
    ApplicationIntentOwner,
    StrategyApplication,
    to_sized_position,
)
from trading_strategies.btc_accumulation import StrategyRole
from trading_strategies.btc_accumulation.long_take_profit_ladder import (
    ENTRY_RULE_KEY,
    TAKE_PROFIT_RULE_KEY,
)
from trading_strategies.kernel import TradeSide
from trading_strategies.rules import (
    InitialEntryFillInput,
    InitialEntryPhase,
    InitialEntryRuleState,
    InitialEntryStartInput,
    LadderFillInput,
    LadderPositionOpenedInput,
)

from .coinm_position_sizer import CoinMPositionSizer


class CoinMLongTakeProfitLadderEventRouter:
    def __init__(
        self,
        *,
        strategy_instance_id: str,
        position_sizer: CoinMPositionSizer,
    ) -> None:
        self.strategy_instance_id = strategy_instance_id
        self.position_sizer = position_sizer

    def inputs_before_instructions(
        self,
        application: StrategyApplication,
        frame: MarketFrame,
    ) -> Mapping[str, Mapping[str, object]]:
        instance = application.instances[self.strategy_instance_id]
        state = instance.rule_state(ENTRY_RULE_KEY)
        if not isinstance(state, InitialEntryRuleState):
            raise TypeError("entry Rule state has an unexpected type")
        if state.phase != InitialEntryPhase.WAITING_START:
            return {}
        return {
            self.strategy_instance_id: {
                ENTRY_RULE_KEY: InitialEntryStartInput(frame.open)
            }
        }

    def inputs_for_fill(
        self,
        application: StrategyApplication,
        fill: SimFill,
        owner: ApplicationIntentOwner,
    ) -> Mapping[str, object]:
        side = TradeSide(fill.side.value)
        if owner.rule_key == ENTRY_RULE_KEY:
            plan = self.position_sizer.evaluate_long(
                entry_price=fill.price,
                quantity=fill.quantity,
            )
            return {
                ENTRY_RULE_KEY: InitialEntryFillInput(
                    fill_id=fill.fill_id,
                    intent_key=owner.local_intent_key,
                    side=side,
                    price=fill.price,
                    quantity=fill.quantity,
                    actual_position=to_sized_position(plan),
                ),
                TAKE_PROFIT_RULE_KEY: LadderPositionOpenedInput(
                    entry_price=fill.price,
                    position_quantity=fill.quantity,
                ),
            }
        if owner.rule_key == TAKE_PROFIT_RULE_KEY:
            return {
                TAKE_PROFIT_RULE_KEY: LadderFillInput(
                    fill_id=fill.fill_id,
                    intent_key=owner.local_intent_key,
                    side=side,
                    price=fill.price,
                    quantity=fill.quantity,
                )
            }
        raise ValueError(f"unsupported ladder Rule owner: {owner.rule_key}")


__all__ = ["CoinMLongTakeProfitLadderEventRouter", "StrategyRole"]
