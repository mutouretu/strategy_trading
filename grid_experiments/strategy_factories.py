"""Grid strategy component resolution and construction."""

from __future__ import annotations

from experiment_system import ComponentSpec
from grid_rule import GridMarketType, GridMode, GridRuleConfig
from grid_strategies import (
    LayeredFollowingGridStrategyConfig,
    SingleFollowingGridStrategyConfig,
)
from grid_strategies.adapters import (
    LayeredFollowingGridSimulationAdapter,
    SingleFollowingGridSimulationAdapter,
)

from ._values import (
    boolean,
    check_fields,
    decimal_value,
    integer,
    string,
)


SINGLE_FOLLOWING_GRID_V1 = "single-following-grid/v1"
LAYERED_FOLLOWING_GRID_V1 = "layered-following-grid/v1"
GridStrategyAdapter = (
    SingleFollowingGridSimulationAdapter
    | LayeredFollowingGridSimulationAdapter
)
_SINGLE_DEFAULTS: dict[str, object] = {
    "strategy_id": "single-following-grid-coinm-long",
    "grid_id": "single-following-grid-rule",
    "mode": "long",
    "move_grid": True,
    "market_type": "coinm",
    "order_notional": "0",
    "tick_size": "0.1",
    "quantity_step": "1",
    "contract_size": "100",
}
_LAYERED_DEFAULTS: dict[str, object] = {
    **_SINGLE_DEFAULTS,
    "strategy_id": "layered-following-grid-coinm-long",
    "grid_id": "layered-following-grid-template",
    "deployment_step": "5000",
    "max_layers": None,
}
_RULE_FIELDS = {
    "strategy_id",
    "grid_id",
    "instrument",
    "mode",
    "anchor_price",
    "grid_ratio",
    "grid_count",
    "order_notional",
    "tick_size",
    "quantity_step",
    "move_grid",
    "market_type",
    "order_coin_quantity",
    "contract_size",
}
_LAYERED_FIELDS = {
    *_RULE_FIELDS,
    "deployment_step",
    "max_layers",
}


def resolve_strategy_component(
    component: ComponentSpec,
) -> ComponentSpec:
    if component.type == SINGLE_FOLLOWING_GRID_V1:
        defaults = _SINGLE_DEFAULTS
    elif component.type == LAYERED_FOLLOWING_GRID_V1:
        defaults = _LAYERED_DEFAULTS
    else:
        raise ValueError(
            f"unsupported strategy component type {component.type!r}"
        )
    return ComponentSpec(
        key=component.key,
        type=component.type,
        parameters={**defaults, **dict(component.parameters)},
    )


def build_rule_config(component: ComponentSpec) -> GridRuleConfig:
    if component.type not in {
        SINGLE_FOLLOWING_GRID_V1,
        LAYERED_FOLLOWING_GRID_V1,
    }:
        raise ValueError(
            f"unsupported strategy component type {component.type!r}"
        )
    context = component.type
    parameters = component.parameters
    check_fields(
        parameters,
        required=(
            _RULE_FIELDS
            if component.type == SINGLE_FOLLOWING_GRID_V1
            else _LAYERED_FIELDS
        ),
        optional=set(),
        context=context,
    )
    try:
        mode = GridMode(
            string(parameters, "mode", context=context)
        )
    except ValueError as exc:
        raise ValueError(
            f"{context}.mode must be 'long' or 'short'"
        ) from exc
    try:
        market_type = GridMarketType(
            string(parameters, "market_type", context=context)
        )
    except ValueError as exc:
        raise ValueError(
            f"{context}.market_type must be 'coinm'"
        ) from exc
    if market_type != GridMarketType.COINM:
        raise ValueError(
            f"{context} supports only COIN-M"
        )
    move_grid = boolean(
        parameters,
        "move_grid",
        context=context,
    )
    if not move_grid:
        raise ValueError(
            f"{context}.move_grid must be true"
        )
    return GridRuleConfig(
        grid_id=string(parameters, "grid_id", context=context),
        instrument=string(
            parameters,
            "instrument",
            context=context,
        ),
        mode=mode,
        anchor_price=decimal_value(
            parameters,
            "anchor_price",
            context=context,
        ),
        grid_ratio=decimal_value(
            parameters,
            "grid_ratio",
            context=context,
        ),
        grid_count=integer(
            parameters,
            "grid_count",
            context=context,
        ),
        order_notional=decimal_value(
            parameters,
            "order_notional",
            context=context,
        ),
        tick_size=decimal_value(
            parameters,
            "tick_size",
            context=context,
        ),
        quantity_step=decimal_value(
            parameters,
            "quantity_step",
            context=context,
        ),
        move_grid=move_grid,
        market_type=market_type,
        order_coin_qty=decimal_value(
            parameters,
            "order_coin_quantity",
            context=context,
        ),
        contract_size=decimal_value(
            parameters,
            "contract_size",
            context=context,
        ),
    )


def build_strategy_adapter(
    component: ComponentSpec,
) -> GridStrategyAdapter:
    parameters = component.parameters
    context = component.type
    strategy_id = string(
        parameters,
        "strategy_id",
        context=context,
    )
    rule = build_rule_config(component)
    if component.type == SINGLE_FOLLOWING_GRID_V1:
        return SingleFollowingGridSimulationAdapter(
            SingleFollowingGridStrategyConfig(
                strategy_id=strategy_id,
                rule=rule,
            )
        )
    if component.type == LAYERED_FOLLOWING_GRID_V1:
        raw_max_layers = parameters["max_layers"]
        max_layers = (
            None
            if raw_max_layers is None
            else integer(
                parameters,
                "max_layers",
                context=context,
            )
        )
        return LayeredFollowingGridSimulationAdapter(
            LayeredFollowingGridStrategyConfig(
                strategy_id=strategy_id,
                rule_template=rule,
                deployment_step=decimal_value(
                    parameters,
                    "deployment_step",
                    context=context,
                ),
                max_layers=max_layers,
            )
        )
    raise ValueError(
        f"unsupported strategy component type {component.type!r}"
    )


def adapter_rule_config(
    adapter: GridStrategyAdapter,
) -> GridRuleConfig:
    if isinstance(adapter, SingleFollowingGridSimulationAdapter):
        return adapter.strategy.config.rule
    return adapter.strategy.config.rule_template
