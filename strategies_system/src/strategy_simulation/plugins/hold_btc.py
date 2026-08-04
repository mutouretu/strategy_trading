from __future__ import annotations

from experiment_system import ComponentSpec

from trading_strategies.baselines import HoldBtcConfig, HoldBtcStrategy

from ..adapters import HoldBtcSimulationAdapter
from ..registry import (
    SimulationStrategyBinding,
    SimulationStrategyBuildContext,
)
from ._values import check_fields, text


HOLD_BTC_V1 = "hold-btc/v1"
_DEFAULTS = {"strategy_id": "hold-btc-baseline"}
_FIELDS = {"strategy_id", "instrument"}


class HoldBtcSimulationPlugin:
    strategy_type = HOLD_BTC_V1

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "strategy",
            "type": self.strategy_type,
            "display_name": "BTC 持有基准",
            "family": "基准策略",
            "version": "v1",
            "description": (
                "不产生任何合约交易，用于区分 BTC 数量变化与 BTC 市价变化。"
            ),
            "formulae": [
                "BTC 数量收益 = 0",
                "USDT 权益 = BTC 总权益 × BTC 市价",
            ],
            "parameters": [
                {"key": "instrument", "name": "交易标的", "required": True}
            ],
            "flow": [
                {"title": "初始化", "detail": "记录账户初始权益"},
                {"title": "观察行情", "detail": "不生成交易意图"},
                {"title": "期末估值", "detail": "保持原始 BTC 数量"},
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
        config = HoldBtcConfig(
            strategy_id=text(
                parameters, "strategy_id", context=self.strategy_type
            ),
            instrument=text(parameters, "instrument", context=self.strategy_type),
        )
        if config.instrument != context.instrument:
            raise ValueError("strategy and account instruments must match")
        strategy = HoldBtcStrategy(config)
        adapter = HoldBtcSimulationAdapter(strategy)

        def summary(result) -> dict[str, object]:
            return {
                "strategy_type": self.strategy_type,
                "strategy_id": config.strategy_id,
                "market_observation_count": strategy.market_observation_count,
                "completed": result.completed,
                "intent_count": len(result.intents),
                "instruction_count": len(result.instructions),
                "fill_count": len(result.fills),
            }

        return SimulationStrategyBinding(
            strategy_type=self.strategy_type,
            instrument=config.instrument,
            trade_port=adapter,
            summary_reader=summary,
            descriptor=self.descriptor(),
        )
