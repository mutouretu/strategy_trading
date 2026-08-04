"""Long entry sized by liquidation target with one-shot ladder exits."""

from __future__ import annotations

from decimal import Decimal

from .models import (
    EntryPlan,
    LadderState,
    PositionPlan,
    StrategyFill,
    StrategyOrderSide,
    StrategyRole,
    TakeProfitLevel,
    TargetLiquidationLadderConfig,
)
from .ports import TargetLiquidationPositionSizer
from .take_profit_schedule import build_take_profit_schedule


class TargetLiquidationLadderStrategy:
    def __init__(
        self,
        config: TargetLiquidationLadderConfig,
        position_sizer: TargetLiquidationPositionSizer,
    ) -> None:
        self.config = config
        self.position_sizer = position_sizer
        self.state = LadderState.NEW
        self.entry_plan: EntryPlan | None = None
        self.entry_fill: StrategyFill | None = None
        self.position_plan: PositionPlan | None = None
        self.take_profit_levels: tuple[TakeProfitLevel, ...] = ()
        self._filled_level_keys: set[str] = set()
        self._processed_fill_ids: set[str] = set()

    def initialize(self) -> None:
        if self.state != LadderState.NEW:
            raise RuntimeError("strategy is already initialized")
        self.state = LadderState.WAITING_ENTRY

    def plan_entry(self, entry_price: Decimal) -> EntryPlan:
        if self.state != LadderState.WAITING_ENTRY:
            raise RuntimeError("entry can be planned only while waiting")
        price = Decimal(entry_price)
        if price <= self.config.target_liquidation_price:
            raise ValueError(
                "entry_price must be above target_liquidation_price"
            )
        position = self.position_sizer.size_long(
            entry_price=price,
            target_liquidation_price=(
                self.config.target_liquidation_price
            ),
            safety_buffer_ratio=(
                self.config.sizing_safety_buffer_ratio
            ),
        )
        self.entry_plan = EntryPlan(
            intent_key=f"{self.config.strategy_id}:entry:1",
            reference_price=price,
            position=position,
        )
        self.state = LadderState.ENTRY_PENDING
        return self.entry_plan

    def on_fill(
        self,
        fill: StrategyFill,
        *,
        actual_position_plan: PositionPlan | None = None,
    ) -> bool:
        if fill.fill_id in self._processed_fill_ids:
            return False
        if fill.role == StrategyRole.ENTRY:
            self._apply_entry_fill(fill, actual_position_plan)
        else:
            self._apply_take_profit_fill(fill)
        self._processed_fill_ids.add(fill.fill_id)
        return True

    def _apply_entry_fill(
        self,
        fill: StrategyFill,
        actual_position_plan: PositionPlan | None,
    ) -> None:
        if self.state != LadderState.ENTRY_PENDING or self.entry_plan is None:
            raise RuntimeError("entry fill is not expected")
        if fill.intent_key != self.entry_plan.intent_key:
            raise ValueError("entry fill references an unknown intent")
        if fill.side != StrategyOrderSide.BUY:
            raise ValueError("long entry fill must be BUY")
        if fill.quantity != self.entry_plan.position.quantity:
            raise ValueError("partial entry fills are not supported in v1")
        plan = actual_position_plan or self.entry_plan.position
        if plan.quantity != fill.quantity:
            raise ValueError("actual position plan quantity must match fill")
        self.entry_fill = fill
        self.position_plan = plan
        self.take_profit_levels = build_take_profit_schedule(
            strategy_id=self.config.strategy_id,
            entry_price=fill.price,
            position_quantity=fill.quantity,
            first_take_profit_ratio=self.config.first_take_profit_ratio,
            end_price=self.config.take_profit_end_price,
            level_count=self.config.take_profit_count,
            tick_size=self.config.tick_size,
            quantity_step=self.config.quantity_step,
        )
        self.state = LadderState.POSITION_OPEN

    def _apply_take_profit_fill(self, fill: StrategyFill) -> None:
        if self.state not in {
            LadderState.POSITION_OPEN,
            LadderState.PARTIALLY_EXITED,
        }:
            raise RuntimeError("take-profit fill is not expected")
        if fill.side != StrategyOrderSide.SELL:
            raise ValueError("long take-profit fill must be SELL")
        levels = {
            level.intent_key: level for level in self.take_profit_levels
        }
        try:
            level = levels[fill.intent_key]
        except KeyError as exc:
            raise ValueError("take-profit fill references an unknown intent") from exc
        if fill.intent_key in self._filled_level_keys:
            raise ValueError("take-profit intent has already filled")
        if fill.quantity != level.quantity:
            raise ValueError("partial take-profit fills are not supported in v1")
        self._filled_level_keys.add(fill.intent_key)
        self.state = (
            LadderState.COMPLETED
            if len(self._filled_level_keys) == len(self.take_profit_levels)
            else LadderState.PARTIALLY_EXITED
        )

    @property
    def visible_take_profit_levels(self) -> tuple[TakeProfitLevel, ...]:
        return tuple(
            level
            for level in self.take_profit_levels
            if level.intent_key not in self._filled_level_keys
        )

    @property
    def completed_take_profit_level_count(self) -> int:
        return len(self._filled_level_keys)

    @property
    def exited_quantity(self) -> Decimal:
        return sum(
            (
                level.quantity
                for level in self.take_profit_levels
                if level.intent_key in self._filled_level_keys
            ),
            Decimal("0"),
        )

    @property
    def remaining_quantity(self) -> Decimal:
        if self.entry_fill is None:
            return Decimal("0")
        return self.entry_fill.quantity - self.exited_quantity

    @property
    def last_triggered_take_profit_level(self) -> int | None:
        filled = [
            level.level
            for level in self.take_profit_levels
            if level.intent_key in self._filled_level_keys
        ]
        return max(filled) if filled else None
