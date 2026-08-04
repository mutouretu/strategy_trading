"""Simulation plugin for one fixed long or short grid."""

from __future__ import annotations

from experiment_system import ComponentSpec
from trading_strategies.grid_following import FixedGridStrategyConfig

from ..adapters import FixedGridSimulationAdapter
from ..registry import (
    SimulationStrategyBinding,
    SimulationStrategyBuildContext,
)
from ._grid_rule import build_rule_config, resolve_rule_mapping, rule_mapping
from ._values import check_fields, text


FIXED_GRID_V1 = "fixed-grid/v1"
_FIELDS = {"strategy_id", "rule"}


class FixedGridSimulationPlugin:
    strategy_type = FIXED_GRID_V1

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "strategy",
            "type": self.strategy_type,
            "display_name": "固定区间网格",
            "family": "网格",
            "version": "v1",
            "description": (
                "在固定价格区间运行一组多头或空头网格；越界后不移动，"
                "用于复现交易所固定区间合约网格。"
            ),
            "formulae": [
                "Pᵢ₊₁ = Pᵢ × (1 + grid_ratio)",
                "K 线 low ≤ target ≤ high 时被动成交",
            ],
            "parameters": [
                {"key": "rule.mode", "name": "多头/空头", "required": True},
                {"key": "rule.anchor_price", "name": "区间起点", "required": True},
                {"key": "rule.grid_ratio", "name": "等比网格间距", "required": True},
                {"key": "rule.grid_count", "name": "网格数量", "required": True},
                {"key": "rule.order_notional", "name": "每格名义价值", "required": True},
            ],
            "flow": [
                {"title": "建立区间", "detail": "一次性建立固定网格"},
                {"title": "等待覆盖", "detail": "市场 K 线覆盖挂单价"},
                {"title": "相邻平仓", "detail": "成交后设置 reduce-only 出场"},
                {"title": "保持边界", "detail": "价格越界时不移动网格"},
            ],
        }

    def resolve(self, component: ComponentSpec) -> ComponentSpec:
        parameters = dict(component.parameters)
        rule = resolve_rule_mapping(parameters, context=self.strategy_type)
        rule["move_grid"] = False
        return ComponentSpec(
            key=component.key,
            type=component.type,
            parameters={
                "strategy_id": parameters.get("strategy_id", "fixed-grid"),
                "rule": rule,
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
        config = FixedGridStrategyConfig(
            strategy_id=text(
                parameters, "strategy_id", context=self.strategy_type
            ),
            rule=rule,
        )
        adapter = FixedGridSimulationAdapter(config)
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
