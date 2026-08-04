from __future__ import annotations

from experiment_system import ComponentSpec

from trading_strategies.btc_accumulation import (
    LadderState,
    TargetLiquidationLadderConfig,
    TargetLiquidationLadderStrategy,
)

from ..adapters import (
    CoinMTargetLiquidationPositionSizer,
    TargetLiquidationLadderSimulationAdapter,
)
from ..registry import (
    SimulationStrategyBinding,
    SimulationStrategyBuildContext,
)
from ._values import boolean, check_fields, decimal, integer, text


TARGET_LIQUIDATION_LADDER_LONG_V1 = (
    "target-liquidation-ladder-long/v1"
)
_DEFAULTS: dict[str, object] = {
    "strategy_id": "btc-target-liquidation-ladder",
    "side": "LONG",
    "entry_timing": "NEXT_OPEN",
    "first_take_profit_ratio": "1.10",
    "take_profit_count": 10,
    "take_profit_spacing": "GEOMETRIC",
    "take_profit_quantity_mode": "EQUAL_CONTRACTS",
    "close_all_at_last_level": True,
    "tick_size": "0.1",
    "quantity_step": "1",
    "sizing_safety_buffer_ratio": "0",
}
_FIELDS = {
    "strategy_id",
    "instrument",
    "side",
    "entry_timing",
    "target_liquidation_price",
    "first_take_profit_ratio",
    "take_profit_end_price",
    "take_profit_count",
    "take_profit_spacing",
    "take_profit_quantity_mode",
    "close_all_at_last_level",
    "tick_size",
    "quantity_step",
    "sizing_safety_buffer_ratio",
}


class TargetLiquidationLadderSimulationPlugin:
    strategy_type = TARGET_LIQUIDATION_LADDER_LONG_V1

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "strategy",
            "type": self.strategy_type,
            "display_name": "目标强平价阶梯止盈多头",
            "family": "BTC 建仓与退出",
            "version": "v1",
            "description": (
                "按目标强平价反算最大合法 COIN-M 多仓，成交后用几何价格阶梯"
                "逐级 reduce-only 止盈；不补仓、不重开。"
            ),
            "formulae": [
                "Q* = max{Q | P_liq(Q) ≤ P_target}",
                "P₀ = P_entry × first_take_profit_ratio",
                "Pᵢ = P₀ × (P_end / P₀)^(i / (n - 1))",
            ],
            "parameters": [
                {"key": "target_liquidation_price", "name": "目标强平价", "required": True},
                {"key": "first_take_profit_ratio", "name": "首档止盈倍数", "default": "1.10"},
                {"key": "take_profit_end_price", "name": "末档止盈价", "required": True},
                {"key": "take_profit_count", "name": "止盈档位数", "default": 10},
                {"key": "sizing_safety_buffer_ratio", "name": "仓位安全余量", "default": "0"},
            ],
            "constraints": [
                "只开 COIN-M 多仓",
                "实际预计强平价不得高于目标值",
                "所有退出均为 reduce-only，最后一档清空余量",
            ],
            "flow": [
                {"title": "反算仓位", "detail": "按下一根 open 和目标强平价求最大合约数"},
                {"title": "主动建仓", "detail": "在第一个可成交 open 买入"},
                {"title": "布置阶梯", "detail": "按成交价生成几何止盈意图"},
                {"title": "逐级退出", "detail": "K 线覆盖档位后 reduce-only 卖出"},
            ],
        }

    def resolve(self, component: ComponentSpec) -> ComponentSpec:
        return ComponentSpec(
            key=component.key,
            type=component.type,
            parameters={**_DEFAULTS, **dict(component.parameters)},
        )

    def build(
        self,
        component: ComponentSpec,
        context: SimulationStrategyBuildContext,
    ) -> SimulationStrategyBinding:
        parameters = component.parameters
        check_fields(parameters, _FIELDS, context=self.strategy_type)
        for key, expected in (
            ("side", "LONG"),
            ("entry_timing", "NEXT_OPEN"),
            ("take_profit_spacing", "GEOMETRIC"),
            ("take_profit_quantity_mode", "EQUAL_CONTRACTS"),
        ):
            actual = text(parameters, key, context=self.strategy_type).upper()
            if actual != expected:
                raise ValueError(f"{self.strategy_type}.{key} must be {expected!r}")
        if not boolean(
            parameters, "close_all_at_last_level", context=self.strategy_type
        ):
            raise ValueError("close_all_at_last_level must be true in v1")
        instrument = text(parameters, "instrument", context=self.strategy_type)
        if instrument != context.instrument:
            raise ValueError("strategy and account instruments must match")
        quantity_step = decimal(
            parameters, "quantity_step", context=self.strategy_type
        )
        config = TargetLiquidationLadderConfig(
            strategy_id=text(
                parameters, "strategy_id", context=self.strategy_type
            ),
            instrument=instrument,
            target_liquidation_price=decimal(
                parameters, "target_liquidation_price", context=self.strategy_type
            ),
            first_take_profit_ratio=decimal(
                parameters, "first_take_profit_ratio", context=self.strategy_type
            ),
            take_profit_end_price=decimal(
                parameters, "take_profit_end_price", context=self.strategy_type
            ),
            take_profit_count=integer(
                parameters, "take_profit_count", context=self.strategy_type
            ),
            tick_size=decimal(
                parameters, "tick_size", context=self.strategy_type
            ),
            quantity_step=quantity_step,
            sizing_safety_buffer_ratio=decimal(
                parameters,
                "sizing_safety_buffer_ratio",
                context=self.strategy_type,
            ),
        )
        sizer = CoinMTargetLiquidationPositionSizer(
            instrument=instrument,
            ledger_factory=context.ledger_factory,
            margin_model=context.margin_model,
            fee_model=context.fee_model,
            quantity_step=quantity_step,
            settlement_asset=context.settlement_asset,
        )
        strategy = TargetLiquidationLadderStrategy(config, sizer)
        adapter = TargetLiquidationLadderSimulationAdapter(
            strategy,
            contract_size=context.contract_size,
        )

        def summary(result) -> dict[str, object]:
            entry_plan = strategy.entry_plan
            entry_fill = strategy.entry_fill
            position_plan = strategy.position_plan
            actual_liq = (
                None
                if position_plan is None
                else position_plan.estimated_liquidation_price
            )
            target = config.target_liquidation_price
            return {
                "strategy_type": self.strategy_type,
                "strategy_id": config.strategy_id,
                "state": strategy.state.value,
                "planned_entry_price": (
                    None if entry_plan is None else str(entry_plan.reference_price)
                ),
                "actual_entry_price": (
                    None if entry_fill is None else str(entry_fill.price)
                ),
                "entry_contracts": (
                    "0" if entry_fill is None else str(entry_fill.quantity)
                ),
                "target_liquidation_price": str(target),
                "estimated_liquidation_price_after_entry": (
                    None if actual_liq is None else str(actual_liq)
                ),
                "liquidation_target_deviation_rate": (
                    None
                    if actual_liq is None
                    else str(actual_liq / target - 1)
                ),
                "take_profit_level_count": len(strategy.take_profit_levels),
                "completed_take_profit_level_count": (
                    strategy.completed_take_profit_level_count
                ),
                "exited_contracts": str(strategy.exited_quantity),
                "remaining_contracts": str(strategy.remaining_quantity),
                "completed": strategy.state == LadderState.COMPLETED,
                "last_triggered_take_profit_level": (
                    strategy.last_triggered_take_profit_level
                ),
                "intent_count": len(result.intents),
                "instruction_count": len(result.instructions),
                "fill_count": len(result.fills),
            }

        return SimulationStrategyBinding(
            strategy_type=self.strategy_type,
            instrument=instrument,
            trade_port=adapter,
            summary_reader=summary,
            descriptor=self.descriptor(),
        )
