"""Simulation plugin for the layered following-grid strategy."""

from __future__ import annotations

from experiment_system import ComponentSpec

from trading_strategies.grid_following import (
    LayeredFollowingGridStrategyConfig,
)

from ..adapters import LayeredFollowingGridSimulationAdapter
from ..registry import (
    SimulationStrategyBinding,
    SimulationStrategyBuildContext,
)
from ._grid_rule import (
    build_rule_config,
    resolve_rule_mapping,
    rule_mapping,
)
from ._values import check_fields, decimal, integer, text


LAYERED_FOLLOWING_GRID_V1 = "layered-following-grid/v1"
_DEFAULTS: dict[str, object] = {
    "strategy_id": "layered-following-grid-coinm-long",
    "deployment_step": "5000",
    "max_layers": None,
}
_FIELDS = {
    "strategy_id",
    "rule",
    "deployment_step",
    "max_layers",
}


class LayeredFollowingGridSimulationPlugin:
    strategy_type = LAYERED_FOLLOWING_GRID_V1

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "strategy",
            "type": self.strategy_type,
            "display_name": "分层跟随网格",
            "family": "跟随网格",
            "version": "v1",
            "description": (
                "按固定价格步长部署多组跟随网格；各层独立调用网格规则，"
                "下位网格上沿触及上位网格下沿时复位。"
            ),
            "formulae": [
                "Aₙ = A₀ - n × deployment_step",
                "Pᵢ₊₁ = Pᵢ ÷ (1 + grid_ratio)",
            ],
            "parameters": [
                {"key": "rule.anchor_price", "name": "首层锚点", "required": True},
                {"key": "deployment_step", "name": "分层步长", "default": "5000"},
                {"key": "rule.grid_ratio", "name": "等比网格间距", "required": True},
                {"key": "rule.grid_count", "name": "每层网格数量", "required": True},
                {"key": "rule.order_coin_quantity", "name": "每格币数量", "required": True},
                {"key": "max_layers", "name": "最大层数", "default": None},
            ],
            "flow": [
                {"title": "部署首层", "detail": "在初始锚点建立跟随网格"},
                {"title": "下跌加层", "detail": "每向下跨过一个步长建立新层"},
                {"title": "独立成交", "detail": "每层分别调用一个网格规则端口"},
                {"title": "碰撞复位", "detail": "下位上沿触及上位下沿时复位"},
            ],
        }

    def resolve(self, component: ComponentSpec) -> ComponentSpec:
        parameters = dict(component.parameters)
        return ComponentSpec(
            key=component.key,
            type=component.type,
            parameters={
                **_DEFAULTS,
                **{
                    key: value
                    for key, value in parameters.items()
                    if key != "rule"
                },
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
        instrument = rule.instrument
        if instrument != context.instrument:
            raise ValueError("strategy and account instruments must match")
        if rule.market_type.value != context.market_type:
            raise ValueError("strategy and account market_type must match")
        if (
            context.market_type == "coinm"
            and rule.contract_size != context.contract_size
        ):
            raise ValueError("strategy and account contract_size must match")
        raw_max_layers = parameters["max_layers"]
        max_layers = (
            None
            if raw_max_layers is None
            else integer(
                parameters,
                "max_layers",
                context=self.strategy_type,
            )
        )
        config = LayeredFollowingGridStrategyConfig(
            strategy_id=text(
                parameters,
                "strategy_id",
                context=self.strategy_type,
            ),
            rule_template=rule,
            deployment_step=decimal(
                parameters,
                "deployment_step",
                context=self.strategy_type,
            ),
            max_layers=max_layers,
        )
        adapter = LayeredFollowingGridSimulationAdapter(config)
        strategy = adapter.strategy

        def summary(result) -> dict[str, object]:
            return {
                "strategy_type": self.strategy_type,
                "strategy_id": config.strategy_id,
                "grid_id": config.rule_template.grid_id,
                "completed_cycles": strategy.completed_cycles,
                "cells_added": strategy.cells_added,
                "cells_reclaimed": strategy.cells_reclaimed,
                "layer_count": strategy.layer_count,
                "reset_count": strategy.reset_count,
                "retiring_grid_count": strategy.retiring_grid_count,
                "layers": [
                    {
                        "layer_index": layer.layer_index,
                        "anchor_price": str(layer.anchor_price),
                        "generation": layer.generation,
                        "lower_edge": str(layer.lower_edge),
                        "upper_edge": str(layer.upper_edge),
                        "waiting_for_reentry": layer.waiting_for_reentry,
                        "reset_count": layer.reset_count,
                        "completed_cycles": layer.completed_cycles,
                        "position_quantity": str(layer.position_quantity),
                    }
                    for layer in strategy.layers
                ],
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
