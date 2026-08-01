"""Translate strategy-owned rule templates into the grid-rule public model."""

from __future__ import annotations

from collections.abc import Mapping

from grid_rule import GridMarketType, GridMode, GridRuleConfig

from ._values import boolean, check_fields, decimal, integer, text


RULE_DEFAULTS: dict[str, object] = {
    "mode": "long",
    "move_grid": True,
    "market_type": "coinm",
    "order_notional": "0",
    "tick_size": "0.1",
    "quantity_step": "1",
    "contract_size": "100",
}
RULE_FIELDS = {
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


def rule_mapping(
    parameters: Mapping[str, object],
    *,
    context: str,
) -> dict[str, object]:
    raw = parameters.get("rule")
    if not isinstance(raw, Mapping):
        raise ValueError(f"{context}.rule must be an object")
    return dict(raw)


def resolve_rule_mapping(
    parameters: Mapping[str, object],
    *,
    context: str,
) -> dict[str, object]:
    return {
        **RULE_DEFAULTS,
        **rule_mapping(parameters, context=context),
    }


def build_rule_config(
    parameters: Mapping[str, object],
    *,
    context: str,
) -> GridRuleConfig:
    check_fields(parameters, RULE_FIELDS, context=context)
    try:
        mode = GridMode(text(parameters, "mode", context=context).lower())
    except ValueError as exc:
        raise ValueError(f"{context}.mode must be 'long' or 'short'") from exc
    try:
        market_type = GridMarketType(
            text(parameters, "market_type", context=context).lower()
        )
    except ValueError as exc:
        raise ValueError(f"{context}.market_type must be 'coinm'") from exc
    if market_type != GridMarketType.COINM:
        raise ValueError(f"{context} supports only COIN-M")
    return GridRuleConfig(
        grid_id=text(parameters, "grid_id", context=context),
        instrument=text(parameters, "instrument", context=context),
        mode=mode,
        anchor_price=decimal(parameters, "anchor_price", context=context),
        grid_ratio=decimal(parameters, "grid_ratio", context=context),
        grid_count=integer(parameters, "grid_count", context=context),
        order_notional=decimal(parameters, "order_notional", context=context),
        tick_size=decimal(parameters, "tick_size", context=context),
        quantity_step=decimal(parameters, "quantity_step", context=context),
        move_grid=boolean(parameters, "move_grid", context=context),
        market_type=market_type,
        order_coin_qty=decimal(
            parameters,
            "order_coin_quantity",
            context=context,
        ),
        contract_size=decimal(
            parameters,
            "contract_size",
            context=context,
        ),
    )
