"""Registered bridge to the already verified grid strategy implementation."""

from __future__ import annotations

from experiment_system import ComponentSpec
from grid_experiments.strategy_factories import (
    SINGLE_FOLLOWING_GRID_V1,
    adapter_rule_config,
    build_strategy_adapter,
    resolve_strategy_component,
)

from ..registry import (
    SimulationStrategyBinding,
    SimulationStrategyBuildContext,
)


class SingleFollowingGridBridgePlugin:
    strategy_type = SINGLE_FOLLOWING_GRID_V1

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "strategy",
            "type": self.strategy_type,
            "display_name": "单组跟随网格",
            "family": "跟随网格",
            "version": "v1",
            "description": (
                "复用 grid_trading 已验证的单组跟随网格和仿真 Adapter；"
                "本插件只负责注册与实验组装，不复制网格规则。"
            ),
            "formulae": [
                "Pᵢ = anchor × grid_ratio^i",
                "K 线 low ≤ target ≤ high 时被动成交",
            ],
            "parameters": [
                {"key": "anchor_price", "name": "锚点价格", "required": True},
                {"key": "grid_ratio", "name": "等比网格间距", "required": True},
                {"key": "grid_count", "name": "网格数量", "required": True},
                {"key": "order_coin_quantity", "name": "每格币数量", "required": True},
            ],
            "flow": [
                {"title": "建立网格", "detail": "按锚点、间距和格数生成 Cell"},
                {"title": "等待覆盖", "detail": "K 线高低价覆盖挂单价"},
                {"title": "完成换手", "detail": "成交后安排相邻反向意图"},
                {"title": "跟随移动", "detail": "越界后补充并回收 Cell"},
            ],
        }

    def resolve(self, component: ComponentSpec) -> ComponentSpec:
        return resolve_strategy_component(component)

    def build(
        self,
        component: ComponentSpec,
        context: SimulationStrategyBuildContext,
    ) -> SimulationStrategyBinding:
        adapter = build_strategy_adapter(component)
        rule = adapter_rule_config(adapter)
        if rule.instrument != context.instrument:
            raise ValueError("strategy and account instruments must match")
        if rule.contract_size != context.contract_size:
            raise ValueError("strategy and account contract_size must match")
        strategy = adapter.strategy

        def summary(result) -> dict[str, object]:
            engine = strategy.engine
            return {
                "strategy_type": self.strategy_type,
                "strategy_id": strategy.config.strategy_id,
                "grid_id": strategy.config.rule.grid_id,
                "completed_cycles": engine.completed_cycles,
                "cells_added": engine.cells_added,
                "cells_reclaimed": engine.cells_reclaimed,
                "final_cell_count": len(engine.cells),
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
