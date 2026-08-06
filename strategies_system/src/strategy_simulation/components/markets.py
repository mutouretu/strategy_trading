"""Market component resolution and construction for strategy experiments."""

from __future__ import annotations

import json
from collections.abc import Mapping

from experiment_system import ComponentSpec, sha256_document
from market_protocol import MarketSource
from market_simulator import (
    AnchoredGBMIntradayMarketSource,
    AnchoredGBMMarketSource,
    ParquetMarketSource,
)

from .._bootstrap import PROJECT_ROOT, SIMULATOR_ROOT
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
LOCKED_MARKET_PATH_V1 = "locked-market-path/v1"
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
_LOCKED_PATH_REQUIRED = {
    "path_set_id",
    "path_set_lock_fingerprint",
    "manifest_sha256",
    "path_key",
    "scenario_id",
    "role",
    "market_seed",
    "origin",
    "market_path_id",
    "path",
    "instrument",
    "interval",
    "frame_count",
    "content_sha256",
    "file_sha256",
}


def _validate_sha256(value: str, *, context: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{context} must be a SHA-256 digest")
    return normalized


def _validate_locked_manifest(
    parameters: Mapping[str, object],
) -> None:
    context = LOCKED_MARKET_PATH_V1
    path_set_id = string(parameters, "path_set_id", context=context)
    manifest_path = (
        SIMULATOR_ROOT
        / "market_environments"
        / "manifests"
        / f"{path_set_id}.json"
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"{context} cannot read the referenced PathSet manifest"
        ) from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"{context} PathSet manifest must be an object")
    expected_manifest_sha = _validate_sha256(
        string(parameters, "manifest_sha256", context=context),
        context=f"{context}.manifest_sha256",
    )
    if sha256_document(manifest) != expected_manifest_sha:
        raise ValueError(f"{context} PathSet manifest SHA-256 mismatch")
    expected_lock = _validate_sha256(
        string(parameters, "path_set_lock_fingerprint", context=context),
        context=f"{context}.path_set_lock_fingerprint",
    )
    if (
        manifest.get("path_set_id") != path_set_id
        or manifest.get("status") != "CONTENT_LOCKED"
        or manifest.get("reproducible") is not True
        or manifest.get("holdout_strategy_execution_allowed") is not False
        or manifest.get("lock_fingerprint") != expected_lock
    ):
        raise ValueError(f"{context} PathSet lock metadata does not match")
    raw_entries = manifest.get("paths")
    if not isinstance(raw_entries, list):
        raise ValueError(f"{context} PathSet manifest paths are invalid")
    path_key = string(parameters, "path_key", context=context)
    matches = [
        entry
        for entry in raw_entries
        if isinstance(entry, dict) and entry.get("path_key") == path_key
    ]
    if len(matches) != 1:
        raise ValueError(f"{context} path_key is not unique in its manifest")
    entry = matches[0]
    pairs = {
        "scenario_id": "scenario_id",
        "role": "role",
        "market_seed": "market_seed",
        "origin": "origin",
        "market_path_id": "market_path_id",
        "path": "storage_path",
        "instrument": "instrument",
        "interval": "interval",
        "frame_count": "frame_count",
        "content_sha256": "content_sha256",
        "file_sha256": "file_sha256",
    }
    if any(
        parameters[parameter_key] != entry.get(manifest_key)
        for parameter_key, manifest_key in pairs.items()
    ):
        raise ValueError(f"{context} component does not match its manifest path")


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
    elif component.type in {HISTORICAL_PARQUET_V1, LOCKED_MARKET_PATH_V1}:
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
    if component.type in {HISTORICAL_PARQUET_V1, LOCKED_MARKET_PATH_V1}:
        parameters = component.parameters
        context = component.type
        check_fields(
            parameters,
            required=(
                _HISTORICAL_REQUIRED
                if component.type == HISTORICAL_PARQUET_V1
                else _LOCKED_PATH_REQUIRED
            ),
            optional=(
                _HISTORICAL_OPTIONAL
                if component.type == HISTORICAL_PARQUET_V1
                else set()
            ),
            context=context,
        )
        interval = string(parameters, "interval", context=context)
        step_milliseconds = _interval_milliseconds(interval)
        raw_path = string(parameters, "path", context=context)
        if component.type == LOCKED_MARKET_PATH_V1:
            role = string(parameters, "role", context=context)
            if role not in {"TRAIN", "VALIDATION"}:
                raise ValueError(
                    f"{LOCKED_MARKET_PATH_V1}.role cannot be HOLDOUT"
                )
            origin = string(parameters, "origin", context=context)
            if origin not in {"SYNTHETIC", "HISTORICAL"}:
                raise ValueError(
                    f"{LOCKED_MARKET_PATH_V1}.origin is unsupported"
                )
            content_sha256 = string(
                parameters,
                "content_sha256",
                context=context,
            )
            market_path_id = string(
                parameters,
                "market_path_id",
                context=context,
            )
            if market_path_id != content_sha256[:20]:
                raise ValueError(
                    f"{LOCKED_MARKET_PATH_V1}.market_path_id does not "
                    "match content_sha256"
                )
            _validate_sha256(
                content_sha256,
                context=f"{LOCKED_MARKET_PATH_V1}.content_sha256",
            )
            _validate_sha256(
                string(parameters, "file_sha256", context=context),
                context=f"{LOCKED_MARKET_PATH_V1}.file_sha256",
            )
            _validate_locked_manifest(parameters)
            path = (SIMULATOR_ROOT / raw_path).resolve()
            generated_root = (
                SIMULATOR_ROOT / "market_environments" / "generated"
            ).resolve()
            try:
                path.relative_to(generated_root)
            except ValueError as exc:
                raise ValueError(
                    f"{LOCKED_MARKET_PATH_V1}.path must stay inside "
                    "market_environments/generated"
                ) from exc
        else:
            path = PROJECT_ROOT / raw_path
            if path.is_absolute():
                path = path.resolve()
        return ParquetMarketSource(
            path,
            expected_instrument=string(
                parameters,
                "instrument",
                context=context,
            ),
            expected_file_sha256=string(
                parameters,
                "file_sha256",
                context=context,
            ),
            expected_frame_count=integer(
                parameters,
                "frame_count",
                context=context,
            ),
            expected_content_sha256=(
                string(
                    parameters,
                    "content_sha256",
                    context=context,
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
