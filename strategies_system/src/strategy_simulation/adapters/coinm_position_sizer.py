"""Size a COIN-M long using the same ledger and margin model as execution."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from market_protocol import MarketFrame
from simulation_runtime import (
    FeeModel,
    LiquidityRole,
    OrderSide,
    SimFill,
    TradeInstruction,
    TradeIntentMode,
)

from trading_strategies.btc_accumulation import PositionPlan


class CoinMTargetLiquidationPositionSizer:
    def __init__(
        self,
        *,
        instrument: str,
        ledger_factory,
        margin_model,
        fee_model: FeeModel,
        quantity_step: Decimal,
        settlement_asset: str,
    ) -> None:
        self.instrument = instrument
        self.ledger_factory = ledger_factory
        self.margin_model = margin_model
        self.fee_model = fee_model
        self.quantity_step = Decimal(quantity_step)
        self.settlement_asset = settlement_asset.upper()
        if not self.instrument.strip():
            raise ValueError("instrument must not be empty")
        if self.margin_model is None:
            raise ValueError("target-liquidation sizing requires a margin model")
        if self.quantity_step <= 0:
            raise ValueError("quantity_step must be > 0")

    def size_long(
        self,
        *,
        entry_price: Decimal,
        target_liquidation_price: Decimal,
        safety_buffer_ratio: Decimal = Decimal("0"),
    ) -> PositionPlan:
        entry = Decimal(entry_price)
        target = Decimal(target_liquidation_price)
        buffer = Decimal(safety_buffer_ratio)
        if entry <= 0 or target <= 0:
            raise ValueError("entry and target liquidation prices must be > 0")
        if entry <= target:
            raise ValueError("entry_price must exceed target_liquidation_price")
        if not Decimal("0") <= buffer < Decimal("1"):
            raise ValueError("safety_buffer_ratio must be >= 0 and < 1")
        effective_target = target * (Decimal("1") - buffer)

        def candidate(step_count: int) -> PositionPlan:
            return self.evaluate_long(
                entry_price=entry,
                quantity=self.quantity_step * Decimal(step_count),
            )

        def acceptable(plan: PositionPlan) -> bool:
            return (
                plan.estimated_liquidation_price <= effective_target
                and plan.margin_buffer > 0
            )

        try:
            first = candidate(1)
        except (ValueError, RuntimeError) as exc:
            raise ValueError(
                "account cannot open one quantity step within the target "
                "liquidation constraint"
            ) from exc
        if not acceptable(first):
            raise ValueError(
                "account cannot open one quantity step within the target "
                "liquidation constraint"
            )

        lower = 1
        upper = 2
        for _ in range(128):
            try:
                plan = candidate(upper)
            except (ValueError, RuntimeError):
                break
            if not acceptable(plan):
                break
            lower = upper
            upper *= 2
        else:
            raise RuntimeError("unable to find a finite sizing upper bound")

        while lower + 1 < upper:
            middle = (lower + upper) // 2
            try:
                plan = candidate(middle)
                valid = acceptable(plan)
            except (ValueError, RuntimeError):
                valid = False
            if valid:
                lower = middle
            else:
                upper = middle
        return candidate(lower)

    def evaluate_long(
        self,
        *,
        entry_price: Decimal,
        quantity: Decimal,
    ) -> PositionPlan:
        price = Decimal(entry_price)
        amount = Decimal(quantity)
        if price <= 0 or amount <= 0:
            raise ValueError("entry_price and quantity must be > 0")
        if amount % self.quantity_step != 0:
            raise ValueError("quantity must align with quantity_step")
        frame = MarketFrame(
            sequence=0,
            timestamp=0,
            instrument=self.instrument,
            open=price,
            high=price,
            low=price,
            close=price,
        )
        instruction = TradeInstruction(
            instruction_key="position-sizing:entry",
            source_intent_key="position-sizing:entry",
            instrument=self.instrument,
            side=OrderSide.BUY,
            quantity=amount,
            price=price,
            frame_sequence=0,
            intent_mode=TradeIntentMode.ACTIVE,
            tags={"role": "entry", "purpose": "position-sizing"},
        )
        provisional = SimFill(
            fill_id="position-sizing:entry@0",
            instruction_key=instruction.instruction_key,
            source_intent_key=instruction.source_intent_key,
            intent_mode=instruction.intent_mode,
            instrument=self.instrument,
            side=instruction.side,
            price=price,
            quantity=amount,
            sequence=0,
            timestamp=0,
            liquidity_role=LiquidityRole.TAKER,
            fee_rate=Decimal("0"),
            fee_amount=Decimal("0"),
            fee_asset=self.settlement_asset,
            reduce_only=False,
            tags=instruction.tags,
        )
        fee = self.fee_model.calculate(instruction, provisional)
        fill = replace(
            provisional,
            liquidity_role=fee.liquidity_role,
            fee_rate=fee.fee_rate,
            fee_amount=fee.fee_amount,
            fee_asset=fee.fee_asset,
        )
        snapshot = self.margin_model.projected_snapshot(
            self.ledger_factory(),
            fill=fill,
            mark_price=price,
            frame=frame,
            mark_price_source="strategy_entry_projection",
        )
        liquidation = snapshot.estimated_liquidation_price
        if liquidation is None:
            raise ValueError("margin model did not produce a liquidation price")
        if snapshot.liquidation_triggered or snapshot.bankrupt:
            raise ValueError("projected entry is already liquidated")
        if snapshot.available_balance < 0:
            raise ValueError("projected entry exceeds available initial margin")
        return PositionPlan(
            quantity=amount,
            quantity_unit=snapshot.position_unit,
            estimated_liquidation_price=liquidation,
            initial_margin=snapshot.position_initial_margin,
            maintenance_margin=snapshot.maintenance_margin,
            margin_buffer=snapshot.margin_buffer,
            model_version=(
                f"coinm-margin/{self.margin_model.maintenance_schedule_version};"
                f"leverage={snapshot.leverage}"
            ),
        )
