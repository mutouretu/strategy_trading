from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from market_protocol import MarketFrame
from simulation_runtime import (
    IntentSnapshot,
    OrderSide,
    SimFill,
    TradeInstruction,
    TradeIntentMode,
)

from trading_strategies.btc_accumulation import (
    LadderState,
    StrategyFill,
    StrategyOrderSide,
    StrategyRole,
    TargetLiquidationLadderStrategy,
)


class TargetLiquidationLadderSimulationAdapter:
    def __init__(
        self,
        strategy: TargetLiquidationLadderStrategy,
        *,
        contract_size: Decimal,
    ) -> None:
        self.strategy = strategy
        self.contract_size = Decimal(contract_size)
        if self.contract_size <= 0:
            raise ValueError("contract_size must be > 0")
        self._instruction_counter = 0

    def initialize(self, frame: MarketFrame) -> None:
        self._validate(frame)
        self.strategy.initialize()

    def instructions_for(self, frame: MarketFrame) -> tuple[TradeInstruction, ...]:
        self._validate(frame)
        if self.strategy.state == LadderState.WAITING_ENTRY:
            plan = self.strategy.plan_entry(frame.open)
            return (
                self._instruction(
                    frame=frame,
                    intent_key=plan.intent_key,
                    side=OrderSide.BUY,
                    quantity=plan.position.quantity,
                    price=frame.open,
                    intent_mode=TradeIntentMode.ACTIVE,
                    reduce_only=False,
                    role=StrategyRole.ENTRY,
                ),
            )
        if self.strategy.state not in {
            LadderState.POSITION_OPEN,
            LadderState.PARTIALLY_EXITED,
        }:
            return ()
        return tuple(
            self._instruction(
                frame=frame,
                intent_key=level.intent_key,
                side=OrderSide.SELL,
                quantity=level.quantity,
                price=level.target_price,
                intent_mode=TradeIntentMode.PASSIVE,
                reduce_only=True,
                role=StrategyRole.TAKE_PROFIT,
                level=level.level,
            )
            for level in self.strategy.visible_take_profit_levels
            if frame.low <= level.target_price <= frame.high
        )

    def on_fills(self, fills: Sequence[SimFill]) -> None:
        for fill in fills:
            role = StrategyRole(fill.tags["role"])
            strategy_fill = StrategyFill(
                fill_id=fill.fill_id,
                intent_key=fill.source_intent_key,
                role=role,
                side=StrategyOrderSide(fill.side.value),
                price=fill.price,
                quantity=fill.quantity,
            )
            actual_plan = None
            if role == StrategyRole.ENTRY:
                actual_plan = self.strategy.position_sizer.evaluate_long(
                    entry_price=fill.price,
                    quantity=fill.quantity,
                )
            self.strategy.on_fill(
                strategy_fill,
                actual_position_plan=actual_plan,
            )

    def on_market(self, frame: MarketFrame) -> None:
        self._validate(frame)

    def visible_intents(self) -> tuple[IntentSnapshot, ...]:
        if (
            self.strategy.state == LadderState.ENTRY_PENDING
            and self.strategy.entry_plan is not None
        ):
            plan = self.strategy.entry_plan
            return (
                IntentSnapshot(
                    intent_key=plan.intent_key,
                    instrument=self.strategy.config.instrument,
                    side=OrderSide.BUY,
                    quantity=plan.position.quantity,
                    intent_mode=TradeIntentMode.ACTIVE,
                    tags=self._tags(StrategyRole.ENTRY),
                ),
            )
        return tuple(
            IntentSnapshot(
                intent_key=level.intent_key,
                instrument=self.strategy.config.instrument,
                side=OrderSide.SELL,
                quantity=level.quantity,
                intent_mode=TradeIntentMode.PASSIVE,
                target_price=level.target_price,
                reduce_only=True,
                tags=self._tags(StrategyRole.TAKE_PROFIT, level=level.level),
            )
            for level in self.strategy.visible_take_profit_levels
        )

    def _instruction(
        self,
        *,
        frame: MarketFrame,
        intent_key: str,
        side: OrderSide,
        quantity: Decimal,
        price: Decimal,
        intent_mode: TradeIntentMode,
        reduce_only: bool,
        role: StrategyRole,
        level: int | None = None,
    ) -> TradeInstruction:
        self._instruction_counter += 1
        return TradeInstruction(
            instruction_key=(
                f"{intent_key}:instruction:{self._instruction_counter}"
            ),
            source_intent_key=intent_key,
            instrument=frame.instrument,
            side=side,
            quantity=quantity,
            price=price,
            frame_sequence=frame.sequence,
            intent_mode=intent_mode,
            reduce_only=reduce_only,
            tags=self._tags(role, level=level),
        )

    def _tags(
        self,
        role: StrategyRole,
        *,
        level: int | None = None,
    ) -> dict[str, str]:
        tags = {
            "strategy_id": self.strategy.config.strategy_id,
            "strategy_type": "target-liquidation-ladder-long/v1",
            "role": role.value,
            "target_liquidation_price": str(
                self.strategy.config.target_liquidation_price
            ),
            "quantity_unit": "contracts",
            "contract_size": str(self.contract_size),
        }
        if level is not None:
            tags["take_profit_level"] = str(level)
        return tags

    def _validate(self, frame: MarketFrame) -> None:
        if frame.instrument != self.strategy.config.instrument:
            raise ValueError("market and strategy instruments must match")
