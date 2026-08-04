"""Strict JSON loading for market environment definitions."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .models import (
    ASSET_PROFILE_SCHEMA_VERSION,
    MARKET_PATH_SET_SCHEMA_VERSION,
    MARKET_SCENARIO_SCHEMA_VERSION,
    AnchorTarget,
    AnchorTargetType,
    AssetProfile,
    MarketModelSpec,
    MarketPathRole,
    MarketPathSet,
    MarketScenario,
    ScenarioAnchor,
    ScenarioOrigin,
    ScenarioReference,
    ScenarioStatus,
    VolatilityRegime,
)


class MarketEnvironmentConfigError(ValueError):
    """A versioned market environment document is malformed."""


def _object(value: object, *, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise MarketEnvironmentConfigError(f"{context} must be an object")
    return value


def _array(value: object, *, context: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise MarketEnvironmentConfigError(f"{context} must be an array")
    return value


def _fields(
    value: Mapping[str, object],
    *,
    required: set[str],
    optional: set[str] = frozenset(),
    context: str,
) -> None:
    keys = set(value)
    missing = sorted(required - keys)
    unknown = sorted(keys - required - optional)
    if missing or unknown:
        raise MarketEnvironmentConfigError(
            f"{context} fields mismatch; missing={missing}, unknown={unknown}"
        )


def _string(value: object, *, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MarketEnvironmentConfigError(f"{context} must be a non-empty string")
    return value


def _integer(value: object, *, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MarketEnvironmentConfigError(f"{context} must be an integer")
    return value


def _decimal(value: object, *, context: str) -> Decimal:
    if not isinstance(value, str) or not value.strip():
        raise MarketEnvironmentConfigError(f"{context} must be a decimal string")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise MarketEnvironmentConfigError(f"{context} is not a decimal") from exc
    if not result.is_finite():
        raise MarketEnvironmentConfigError(f"{context} must be finite")
    return result


def _date(value: object, *, context: str) -> date:
    text = _string(value, context=context)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise MarketEnvironmentConfigError(f"{context} is not an ISO date") from exc


def _enum(enum_type, value: object, *, context: str):
    text = _string(value, context=context)
    try:
        return enum_type(text)
    except ValueError as exc:
        raise MarketEnvironmentConfigError(f"{context} has unsupported value {text!r}") from exc


def _metadata(value: object, *, context: str) -> Mapping[str, object]:
    document = _object(value, context=context)
    try:
        json.dumps(document, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise MarketEnvironmentConfigError(f"{context} must contain JSON values") from exc
    return document


def _load_json(path: str | Path) -> Mapping[str, object]:
    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MarketEnvironmentConfigError(f"cannot load {source}: {exc}") from exc
    return _object(document, context=str(source))


def parse_asset_profile(document: Mapping[str, object]) -> AssetProfile:
    context = ASSET_PROFILE_SCHEMA_VERSION
    _fields(
        document,
        required={
            "schema_version",
            "profile_id",
            "name",
            "calendar",
            "periods_per_year",
            "price_quantum",
            "default_interval",
            "metadata",
        },
        context=context,
    )
    if document["schema_version"] != ASSET_PROFILE_SCHEMA_VERSION:
        raise MarketEnvironmentConfigError("unsupported asset profile schema version")
    try:
        return AssetProfile(
            profile_id=_string(document["profile_id"], context=f"{context}.profile_id"),
            name=_string(document["name"], context=f"{context}.name"),
            calendar=_string(document["calendar"], context=f"{context}.calendar"),
            periods_per_year=_integer(
                document["periods_per_year"], context=f"{context}.periods_per_year"
            ),
            price_quantum=_decimal(
                document["price_quantum"], context=f"{context}.price_quantum"
            ),
            default_interval=_string(
                document["default_interval"], context=f"{context}.default_interval"
            ),
            metadata=_metadata(document["metadata"], context=f"{context}.metadata"),
        )
    except ValueError as exc:
        raise MarketEnvironmentConfigError(str(exc)) from exc


def load_asset_profile(path: str | Path) -> AssetProfile:
    return parse_asset_profile(_load_json(path))


def _anchor(value: object, *, index: int) -> ScenarioAnchor:
    context = f"{MARKET_SCENARIO_SCHEMA_VERSION}.anchors[{index}]"
    item = _object(value, context=context)
    _fields(item, required={"date", "target"}, context=context)
    target_context = f"{context}.target"
    target = _object(item["target"], context=target_context)
    target_type = _enum(
        AnchorTargetType, target.get("type"), context=f"{target_context}.type"
    )
    try:
        if target_type is AnchorTargetType.HARD:
            _fields(target, required={"type", "price"}, context=target_context)
            parsed_target = AnchorTarget(
                type=target_type,
                price=_decimal(target["price"], context=f"{target_context}.price"),
            )
        else:
            _fields(
                target,
                required={"type", "minimum", "maximum"},
                context=target_context,
            )
            parsed_target = AnchorTarget(
                type=target_type,
                minimum=_decimal(
                    target["minimum"], context=f"{target_context}.minimum"
                ),
                maximum=_decimal(
                    target["maximum"], context=f"{target_context}.maximum"
                ),
            )
        return ScenarioAnchor(
            date=_date(item["date"], context=f"{context}.date"),
            target=parsed_target,
        )
    except ValueError as exc:
        raise MarketEnvironmentConfigError(str(exc)) from exc


def _regime(value: object, *, index: int) -> VolatilityRegime:
    context = f"{MARKET_SCENARIO_SCHEMA_VERSION}.volatility_regimes[{index}]"
    item = _object(value, context=context)
    _fields(
        item,
        required={"start", "end_exclusive", "annual_volatility"},
        context=context,
    )
    volatility_context = f"{context}.annual_volatility"
    volatility = _object(item["annual_volatility"], context=volatility_context)
    _fields(volatility, required={"minimum", "maximum"}, context=volatility_context)
    try:
        return VolatilityRegime(
            start=_date(item["start"], context=f"{context}.start"),
            end_exclusive=_date(
                item["end_exclusive"], context=f"{context}.end_exclusive"
            ),
            minimum=_decimal(
                volatility["minimum"], context=f"{volatility_context}.minimum"
            ),
            maximum=_decimal(
                volatility["maximum"], context=f"{volatility_context}.maximum"
            ),
        )
    except ValueError as exc:
        raise MarketEnvironmentConfigError(str(exc)) from exc


def parse_market_scenario(document: Mapping[str, object]) -> MarketScenario:
    context = MARKET_SCENARIO_SCHEMA_VERSION
    _fields(
        document,
        required={
            "schema_version",
            "scenario_id",
            "name",
            "description",
            "origin",
            "asset_profile_id",
            "instrument",
            "horizon",
            "interval",
            "model",
            "anchors",
            "volatility_regimes",
            "status",
            "metadata",
        },
        context=context,
    )
    if document["schema_version"] != MARKET_SCENARIO_SCHEMA_VERSION:
        raise MarketEnvironmentConfigError("unsupported market scenario schema version")
    horizon = _object(document["horizon"], context=f"{context}.horizon")
    _fields(horizon, required={"start", "end"}, context=f"{context}.horizon")
    model_raw = _object(document["model"], context=f"{context}.model")
    _fields(
        model_raw,
        required={"type", "price_quantum", "periods_per_year", "substeps_per_bar"},
        context=f"{context}.model",
    )
    anchors_raw = _array(document["anchors"], context=f"{context}.anchors")
    regimes_raw = _array(
        document["volatility_regimes"], context=f"{context}.volatility_regimes"
    )
    try:
        model = MarketModelSpec(
            type=_string(model_raw["type"], context=f"{context}.model.type"),
            price_quantum=_decimal(
                model_raw["price_quantum"], context=f"{context}.model.price_quantum"
            ),
            periods_per_year=_integer(
                model_raw["periods_per_year"],
                context=f"{context}.model.periods_per_year",
            ),
            substeps_per_bar=_integer(
                model_raw["substeps_per_bar"],
                context=f"{context}.model.substeps_per_bar",
            ),
        )
        return MarketScenario(
            scenario_id=_string(
                document["scenario_id"], context=f"{context}.scenario_id"
            ),
            name=_string(document["name"], context=f"{context}.name"),
            description=_string(
                document["description"], context=f"{context}.description"
            ),
            origin=_enum(
                ScenarioOrigin, document["origin"], context=f"{context}.origin"
            ),
            asset_profile_id=_string(
                document["asset_profile_id"],
                context=f"{context}.asset_profile_id",
            ),
            instrument=_string(
                document["instrument"], context=f"{context}.instrument"
            ),
            start=_date(horizon["start"], context=f"{context}.horizon.start"),
            end=_date(horizon["end"], context=f"{context}.horizon.end"),
            interval=_string(document["interval"], context=f"{context}.interval"),
            model=model,
            anchors=tuple(_anchor(item, index=index) for index, item in enumerate(anchors_raw)),
            volatility_regimes=tuple(
                _regime(item, index=index) for index, item in enumerate(regimes_raw)
            ),
            status=_enum(
                ScenarioStatus, document["status"], context=f"{context}.status"
            ),
            metadata=_metadata(document["metadata"], context=f"{context}.metadata"),
        )
    except ValueError as exc:
        raise MarketEnvironmentConfigError(str(exc)) from exc


def load_market_scenario(path: str | Path) -> MarketScenario:
    return parse_market_scenario(_load_json(path))


def parse_market_path_set(document: Mapping[str, object]) -> MarketPathSet:
    context = MARKET_PATH_SET_SCHEMA_VERSION
    _fields(
        document,
        required={
            "schema_version",
            "path_set_id",
            "description",
            "asset_profile_path",
            "scenarios",
            "roles",
            "status",
        },
        context=context,
    )
    if document["schema_version"] != MARKET_PATH_SET_SCHEMA_VERSION:
        raise MarketEnvironmentConfigError("unsupported market path set schema version")
    scenarios_raw = _array(document["scenarios"], context=f"{context}.scenarios")
    references: list[ScenarioReference] = []
    for index, raw in enumerate(scenarios_raw):
        item_context = f"{context}.scenarios[{index}]"
        item = _object(raw, context=item_context)
        _fields(item, required={"scenario_id", "path"}, context=item_context)
        references.append(
            ScenarioReference(
                scenario_id=_string(
                    item["scenario_id"], context=f"{item_context}.scenario_id"
                ),
                path=_string(item["path"], context=f"{item_context}.path"),
            )
        )
    roles = _object(document["roles"], context=f"{context}.roles")
    _fields(
        roles,
        required={role.value for role in MarketPathRole},
        context=f"{context}.roles",
    )
    role_seeds: dict[MarketPathRole, tuple[int, ...]] = {}
    for role in MarketPathRole:
        seeds_raw = _array(roles[role.value], context=f"{context}.roles.{role.value}")
        role_seeds[role] = tuple(
            _integer(seed, context=f"{context}.roles.{role.value}[{index}]")
            for index, seed in enumerate(seeds_raw)
        )
    try:
        return MarketPathSet(
            path_set_id=_string(
                document["path_set_id"], context=f"{context}.path_set_id"
            ),
            description=_string(
                document["description"], context=f"{context}.description"
            ),
            asset_profile_path=_string(
                document["asset_profile_path"],
                context=f"{context}.asset_profile_path",
            ),
            scenarios=tuple(references),
            role_seeds=role_seeds,
            status=_enum(
                ScenarioStatus, document["status"], context=f"{context}.status"
            ),
        )
    except ValueError as exc:
        raise MarketEnvironmentConfigError(str(exc)) from exc


def load_market_path_set(path: str | Path) -> MarketPathSet:
    return parse_market_path_set(_load_json(path))
