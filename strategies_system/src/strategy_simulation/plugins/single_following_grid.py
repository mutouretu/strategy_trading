"""Simulation plugin for the strategy-owned single following grid."""

from __future__ import annotations

from experiment_system import ComponentSpec
from trading_strategies.grid_following import (
    SingleFollowingGridStrategyConfig,
)

from ..adapters import SingleFollowingGridSimulationAdapter
from ..registry import (
    SimulationStrategyBinding,
    SimulationStrategyBuildContext,
)
from ._grid_rule import (
    build_rule_config,
    resolve_rule_mapping,
    rule_mapping,
)
from ._values import check_fields, text


SINGLE_FOLLOWING_GRID_V1 = "single-following-grid/v1"
_DEFAULT_STRATEGY_ID = "single-following-grid-coinm-long"
_FIELDS = {"strategy_id", "rule"}


class SingleFollowingGridSimulationPlugin:
    strategy_type = SINGLE_FOLLOWING_GRID_V1

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "strategy",
            "type": self.strategy_type,
            "display_name": "单组跟随网格",
            "family": "跟随网格",
            "version": "v1",
            "description": (
                "启动时建立一组跟随网格；策略只维护生命周期，"
                "每格成交与移动由注入的网格规则执行。"
            ),
            "formulae": [
                "Pᵢ₊₁ = Pᵢ ÷ (1 + grid_ratio)",
                "K 线 low ≤ target ≤ high 时被动成交",
            ],
            "parameters": [
                {"key": "rule.anchor_price", "name": "锚点价格", "required": True},
                {"key": "rule.grid_ratio", "name": "等比网格间距", "required": True},
                {"key": "rule.grid_count", "name": "网格数量", "required": True},
                {"key": "rule.order_coin_quantity", "name": "每格币数量", "required": True},
            ],
            "flow": [
                {"title": "建立网格", "detail": "策略请求创建一个规则实例"},
                {"title": "等待覆盖", "detail": "K 线高低价覆盖挂单价"},
                {"title": "完成换手", "detail": "规则安排相邻反向意图"},
                {"title": "跟随移动", "detail": "规则补充并回收 Cell"},
            ],
        }

    def resolve(self, component: ComponentSpec) -> ComponentSpec:
        parameters = dict(component.parameters)
        return ComponentSpec(
            key=component.key,
            type=component.type,
            parameters={
                "strategy_id": parameters.get(
                    "strategy_id",
                    _DEFAULT_STRATEGY_ID,
                ),
                "rule": resolve_rule_mapping(
                    parameters,
                    context=self.strategy_type,
                ),
            },
        )

    def build(
        self,
        component: ComponentSpec,
        context: SimulationStrategyBuildContext,
    ) -> SimulationStrategyBinding:
        parameters = component.parameters
        check_fields(parameters, _FIELDS, context=self.strategy_type)
        rule = build_rule_config(
            rule_mapping(parameters, context=self.strategy_type),
            context=f"{self.strategy_type}.rule",
        )
        if rule.instrument != context.instrument:
            raise ValueError("strategy and account instruments must match")
        if rule.market_type.value != context.market_type:
            raise ValueError("strategy and account market_type must match")
        if (
            context.market_type == "coinm"
            and rule.contract_size != context.contract_size
        ):
            raise ValueError("strategy and account contract_size must match")
        config = SingleFollowingGridStrategyConfig(
            strategy_id=text(
                parameters,
                "strategy_id",
                context=self.strategy_type,
            ),
            rule=rule,
        )
        adapter = SingleFollowingGridSimulationAdapter(config)
        strategy = adapter.strategy

        def summary(result) -> dict[str, object]:
            snapshot = strategy.rule.snapshot()
            return {
                "strategy_type": self.strategy_type,
                "strategy_id": config.strategy_id,
                "grid_id": rule.grid_id,
                "completed_cycles": snapshot.completed_cycles,
                "cells_added": snapshot.cells_added,
                "cells_reclaimed": snapshot.cells_reclaimed,
                "final_cell_count": len(snapshot.cells),
                "intent_count": len(result.intents),
                "instruction_count": len(result.instructions),
                "fill_count": len(result.fills),
            }

        return SimulationStrategyBinding(
            strategy_type=self.strategy_type,
            instrument=rule.instrument,
            trade_port=adapter,
            summary_reader=summary,
            descriptor=self.descriptor(),
        )
