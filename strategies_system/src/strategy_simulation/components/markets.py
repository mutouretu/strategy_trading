"""Market component resolution and construction for strategy experiments."""

from __future__ import annotations

from collections.abc import Mapping

from experiment_system import ComponentSpec
from market_protocol import MarketSource
from market_simulator import (
    AnchoredGBMIntradayMarketSource,
    AnchoredGBMMarketSource,
    ParquetMarketSource,
)

from .._bootstrap import PROJECT_ROOT
from ._values import (
    check_fields,
    decimal_value,
    integer,
    sequence,
    string,
)


ANCHORED_GBM_V1 = "anchored-gbm/v1"
ANCHORED_GBM_INTRADAY_V1 = "anchored-gbm-intraday/v1"
HISTORICAL_PARQUET_V1 = "historical-parquet/v1"
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
_INTRADAY_DEFAULTS: dict[str, object] = {
    "annual_volatility": "0.60",
    "bars_per_day": 288,
    "periods_per_year": 365,
    "price_quantum": "0.01",
    "price_floor": None,
    "price_ceiling": None,
    "interval": "5m",
}
_INTRADAY_REQUIRED = {
    "instrument",
    "anchors",
    "annual_volatility",
    "bars_per_day",
    "periods_per_year",
    "price_quantum",
    "price_floor",
    "price_ceiling",
    "interval",
}
_HISTORICAL_REQUIRED = {
    "path",
    "instrument",
    "interval",
    "frame_count",
    "file_sha256",
}
_HISTORICAL_OPTIONAL = {"content_sha256"}


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
    if component.type == ANCHORED_GBM_V1:
        defaults = _DEFAULTS
    elif component.type == ANCHORED_GBM_INTRADAY_V1:
        defaults = _INTRADAY_DEFAULTS
    elif component.type == HISTORICAL_PARQUET_V1:
        defaults = {}
    else:
        raise ValueError(
            f"unsupported market component type {component.type!r}"
        )
    parameters = {**defaults, **dict(component.parameters)}
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
) -> MarketSource:
    if component.type == HISTORICAL_PARQUET_V1:
        parameters = component.parameters
        check_fields(
            parameters,
            required=_HISTORICAL_REQUIRED,
            optional=_HISTORICAL_OPTIONAL,
            context=HISTORICAL_PARQUET_V1,
        )
        interval = string(
            parameters, "interval", context=HISTORICAL_PARQUET_V1
        )
        step_milliseconds = _interval_milliseconds(interval)
        raw_path = string(
            parameters, "path", context=HISTORICAL_PARQUET_V1
        )
        path = PROJECT_ROOT / raw_path
        if path.is_absolute():
            path = path.resolve()
        return ParquetMarketSource(
            path,
            expected_instrument=string(
                parameters,
                "instrument",
                context=HISTORICAL_PARQUET_V1,
            ),
            expected_file_sha256=string(
                parameters,
                "file_sha256",
                context=HISTORICAL_PARQUET_V1,
            ),
            expected_frame_count=integer(
                parameters,
                "frame_count",
                context=HISTORICAL_PARQUET_V1,
            ),
            expected_content_sha256=(
                string(
                    parameters,
                    "content_sha256",
                    context=HISTORICAL_PARQUET_V1,
                )
                if "content_sha256" in parameters
                else None
            ),
            step_milliseconds=step_milliseconds,
        )
    if component.type not in {ANCHORED_GBM_V1, ANCHORED_GBM_INTRADAY_V1}:
        raise ValueError(
            f"unsupported market component type {component.type!r}"
        )
    parameters = component.parameters
    check_fields(
        parameters,
        required=(
            _REQUIRED
            if component.type == ANCHORED_GBM_V1
            else _INTRADAY_REQUIRED
        ),
        optional=set(),
        context=_CONTEXT,
    )
    interval = string(parameters, "interval", context=_CONTEXT)
    if component.type == ANCHORED_GBM_V1 and interval != "1d":
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
    common = dict(
        annual_volatility=decimal_value(
            parameters,
            "annual_volatility",
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
    instrument = string(parameters, "instrument", context=_CONTEXT)
    parsed_anchors = [
        (anchor["date"], anchor["price"]) for anchor in anchors
    ]
    if component.type == ANCHORED_GBM_INTRADAY_V1:
        bars_per_day = integer(
            parameters, "bars_per_day", context=_CONTEXT
        )
        expected_interval = _interval_label(bars_per_day)
        if interval != expected_interval:
            raise ValueError(
                f"{ANCHORED_GBM_INTRADAY_V1}.interval must be "
                f"{expected_interval!r} for bars_per_day={bars_per_day}"
            )
        return AnchoredGBMIntradayMarketSource(
            instrument,
            parsed_anchors,
            bars_per_day=bars_per_day,
            **common,
        )
    return AnchoredGBMMarketSource(
        instrument,
        parsed_anchors,
        intraday_steps=integer(
            parameters,
            "intraday_steps",
            context=_CONTEXT,
        ),
        **common,
    )


def _interval_label(bars_per_day: int) -> str:
    if bars_per_day <= 0 or 86_400 % bars_per_day:
        raise ValueError("bars_per_day must divide 86400 exactly")
    seconds = 86_400 // bars_per_day
    if seconds % 3_600 == 0:
        return f"{seconds // 3_600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _interval_milliseconds(interval: str) -> int:
    suffix_multipliers = {
        "s": 1_000,
        "m": 60_000,
        "h": 3_600_000,
        "d": 86_400_000,
    }
    if len(interval) < 2 or interval[-1] not in suffix_multipliers:
        raise ValueError(
            f"{HISTORICAL_PARQUET_V1}.interval must use s, m, h or d"
        )
    try:
        count = int(interval[:-1])
    except ValueError as exc:
        raise ValueError(
            f"{HISTORICAL_PARQUET_V1}.interval has an invalid count"
        ) from exc
    if count <= 0:
        raise ValueError(
            f"{HISTORICAL_PARQUET_V1}.interval count must be > 0"
        )
    return count * suffix_multipliers[interval[-1]]
