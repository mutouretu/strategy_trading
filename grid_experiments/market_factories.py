"""Market component resolution and construction."""

from __future__ import annotations

from collections.abc import Mapping

from experiment_system import ComponentSpec
from market_simulator import AnchoredGBMMarketSource

from ._values import (
    check_fields,
    decimal_value,
    integer,
    sequence,
    string,
)


ANCHORED_GBM_V1 = "anchored-gbm/v1"
_CONTEXT = ANCHORED_GBM_V1
_DEFAULTS: dict[str, object] = {
    "annual_volatility": "0.60",
    "intraday_steps": 24,
    "periods_per_year": 365,
    "price_quantum": "0.01",
    "price_floor": None,
    "price_ceiling": None,
    "interval": "1d",
}
_REQUIRED = {
    "instrument",
    "anchors",
    "annual_volatility",
    "intraday_steps",
    "periods_per_year",
    "price_quantum",
    "price_floor",
    "price_ceiling",
    "interval",
}


def _normalized_anchors(value: object) -> list[dict[str, str]]:
    anchors = sequence(value, context=f"{_CONTEXT}.anchors")
    normalized: list[dict[str, str]] = []
    for index, anchor in enumerate(anchors):
        context = f"{_CONTEXT}.anchors[{index}]"
        if isinstance(anchor, Mapping):
            if set(anchor) != {"date", "price"}:
                raise ValueError(
                    f"{context} must contain only date and price"
                )
            raw_date = anchor["date"]
            raw_price = anchor["price"]
        else:
            pair = sequence(anchor, context=context)
            if len(pair) != 2:
                raise ValueError(
                    f"{context} must contain date and price"
                )
            raw_date, raw_price = pair
        if not isinstance(raw_date, str) or not raw_date.strip():
            raise ValueError(f"{context}.date must be a string")
        if not isinstance(raw_price, str) or not raw_price.strip():
            raise ValueError(
                f"{context}.price must be a decimal string"
            )
        normalized.append(
            {"date": raw_date, "price": raw_price}
        )
    return normalized


def resolve_market_component(component: ComponentSpec) -> ComponentSpec:
    if component.type != ANCHORED_GBM_V1:
        raise ValueError(
            f"unsupported market component type {component.type!r}"
        )
    parameters = {**_DEFAULTS, **dict(component.parameters)}
    if "anchors" in parameters:
        parameters["anchors"] = _normalized_anchors(
            parameters["anchors"]
        )
    return ComponentSpec(
        key=component.key,
        type=component.type,
        parameters=parameters,
    )


def build_market_source(
    component: ComponentSpec,
) -> AnchoredGBMMarketSource:
    if component.type != ANCHORED_GBM_V1:
        raise ValueError(
            f"unsupported market component type {component.type!r}"
        )
    parameters = component.parameters
    check_fields(
        parameters,
        required=_REQUIRED,
        optional=set(),
        context=_CONTEXT,
    )
    interval = string(parameters, "interval", context=_CONTEXT)
    if interval != "1d":
        raise ValueError(f"{_CONTEXT}.interval must be '1d'")
    anchors = _normalized_anchors(parameters["anchors"])
    price_floor = parameters["price_floor"]
    price_ceiling = parameters["price_ceiling"]
    for key, value in (
        ("price_floor", price_floor),
        ("price_ceiling", price_ceiling),
    ):
        if value is not None:
            decimal_value(parameters, key, context=_CONTEXT)
    return AnchoredGBMMarketSource(
        string(parameters, "instrument", context=_CONTEXT),
        [
            (anchor["date"], anchor["price"])
            for anchor in anchors
        ],
        annual_volatility=decimal_value(
            parameters,
            "annual_volatility",
            context=_CONTEXT,
        ),
        intraday_steps=integer(
            parameters,
            "intraday_steps",
            context=_CONTEXT,
        ),
        periods_per_year=integer(
            parameters,
            "periods_per_year",
            context=_CONTEXT,
        ),
        price_quantum=decimal_value(
            parameters,
            "price_quantum",
            context=_CONTEXT,
        ),
        price_floor=price_floor,
        price_ceiling=price_ceiling,
    )
