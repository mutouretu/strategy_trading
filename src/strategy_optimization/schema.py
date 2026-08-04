"""Strict JSON loaders for Study and research protocol documents."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from experiment_system import load_experiment_spec

from .errors import StudyConfigError
from .models import (
    DATASET_SPLIT_SCHEMA_VERSION,
    OBJECTIVE_PROFILE_SCHEMA_VERSION,
    STUDY_SCHEMA_VERSION,
    ComparisonMode,
    ConstraintOperator,
    DatasetRole,
    DatasetSplitSpec,
    DatasetStatus,
    DatasetWindow,
    EligibilityConstraint,
    MetricSelector,
    ObjectiveDirection,
    ObjectiveProfile,
    ObjectiveSpec,
    StudyBundle,
    StudySpec,
)


def _object(value: Any, *, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StudyConfigError(f"{path} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise StudyConfigError(f"{path} keys must be strings")
    return value


def _array(value: Any, *, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise StudyConfigError(f"{path} must be an array")
    return value


def _fields(
    document: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str],
    path: str,
) -> None:
    missing = required - set(document)
    if missing:
        raise StudyConfigError(
            f"{path} is missing required fields: {sorted(missing)}"
        )
    unknown = set(document) - required - optional
    if unknown:
        raise StudyConfigError(
            f"{path} contains unknown fields: {sorted(unknown)}"
        )


def _string(value: Any, *, path: str) -> str:
    if not isinstance(value, str):
        raise StudyConfigError(f"{path} must be a string")
    return value


def _boolean(value: Any, *, path: str) -> bool:
    if not isinstance(value, bool):
        raise StudyConfigError(f"{path} must be a boolean")
    return value


def _enum(enum_type, value: Any, *, path: str):
    raw = _string(value, path=path)
    try:
        return enum_type(raw)
    except ValueError as exc:
        choices = ", ".join(item.value for item in enum_type)
        raise StudyConfigError(
            f"{path} must be one of: {choices}"
        ) from exc


def _date(value: Any, *, path: str) -> date:
    raw = _string(value, path=path)
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise StudyConfigError(f"{path} must use YYYY-MM-DD") from exc


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StudyConfigError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise StudyConfigError(
            f"invalid JSON in {path}:{exc.lineno}:{exc.colno}: {exc.msg}"
        ) from exc
    return _object(document, path="$")


def _selector(value: Any, *, path: str) -> MetricSelector:
    document = _object(value, path=path)
    _fields(
        document,
        required={
            "metric_set_id",
            "metric_set_version",
            "metric_key",
            "unit",
        },
        optional={"dimensions"},
        path=path,
    )
    raw_dimensions = _object(
        document.get("dimensions", {}),
        path=f"{path}.dimensions",
    )
    dimensions = {
        _string(key, path=f"{path}.dimensions key"): _string(
            item,
            path=f"{path}.dimensions.{key}",
        )
        for key, item in raw_dimensions.items()
    }
    return MetricSelector(
        metric_set_id=_string(
            document["metric_set_id"],
            path=f"{path}.metric_set_id",
        ),
        metric_set_version=_string(
            document["metric_set_version"],
            path=f"{path}.metric_set_version",
        ),
        metric_key=_string(
            document["metric_key"],
            path=f"{path}.metric_key",
        ),
        unit=_string(document["unit"], path=f"{path}.unit"),
        dimensions=dimensions,
    )


def parse_objective_profile(document: Any) -> ObjectiveProfile:
    root = _object(document, path="$objective_profile")
    _fields(
        root,
        required={
            "schema_version",
            "profile_id",
            "valuation_asset",
            "baseline_strategy_type",
            "objectives",
            "eligibility_constraints",
        },
        optional={"description"},
        path="$objective_profile",
    )
    version = _string(
        root["schema_version"],
        path="$objective_profile.schema_version",
    )
    if version != OBJECTIVE_PROFILE_SCHEMA_VERSION:
        raise StudyConfigError(
            "$objective_profile.schema_version must be "
            f"{OBJECTIVE_PROFILE_SCHEMA_VERSION!r}"
        )
    objectives = _array(
        root["objectives"],
        path="$objective_profile.objectives",
    )
    parsed_objectives = []
    for index, value in enumerate(objectives):
        path = f"$objective_profile.objectives[{index}]"
        item = _object(value, path=path)
        _fields(
            item,
            required={"key", "selector", "direction"},
            optional={"comparison"},
            path=path,
        )
        parsed_objectives.append(
            ObjectiveSpec(
                key=_string(item["key"], path=f"{path}.key"),
                selector=_selector(item["selector"], path=f"{path}.selector"),
                direction=_enum(
                    ObjectiveDirection,
                    item["direction"],
                    path=f"{path}.direction",
                ),
                comparison=_enum(
                    ComparisonMode,
                    item.get("comparison", ComparisonMode.DIRECT.value),
                    path=f"{path}.comparison",
                ),
            )
        )
    constraints = _array(
        root["eligibility_constraints"],
        path="$objective_profile.eligibility_constraints",
    )
    parsed_constraints = []
    for index, value in enumerate(constraints):
        path = f"$objective_profile.eligibility_constraints[{index}]"
        item = _object(value, path=path)
        _fields(
            item,
            required={"key", "selector", "operator", "value"},
            optional=set(),
            path=path,
        )
        parsed_constraints.append(
            EligibilityConstraint(
                key=_string(item["key"], path=f"{path}.key"),
                selector=_selector(item["selector"], path=f"{path}.selector"),
                operator=_enum(
                    ConstraintOperator,
                    item["operator"],
                    path=f"{path}.operator",
                ),
                value=item["value"],
            )
        )
    return ObjectiveProfile(
        schema_version=version,
        profile_id=_string(
            root["profile_id"],
            path="$objective_profile.profile_id",
        ),
        description=_string(
            root.get("description", ""),
            path="$objective_profile.description",
        ),
        valuation_asset=_string(
            root["valuation_asset"],
            path="$objective_profile.valuation_asset",
        ),
        baseline_strategy_type=_string(
            root["baseline_strategy_type"],
            path="$objective_profile.baseline_strategy_type",
        ),
        objectives=tuple(parsed_objectives),
        eligibility_constraints=tuple(parsed_constraints),
    )


def load_objective_profile(path: str | Path) -> ObjectiveProfile:
    return parse_objective_profile(_read_json(Path(path)))


def parse_dataset_split(document: Any) -> DatasetSplitSpec:
    root = _object(document, path="$dataset_split")
    _fields(
        root,
        required={
            "schema_version",
            "split_id",
            "status",
            "source",
            "instrument",
            "interval",
            "proxy_market",
            "windows",
        },
        optional={"description"},
        path="$dataset_split",
    )
    version = _string(
        root["schema_version"],
        path="$dataset_split.schema_version",
    )
    if version != DATASET_SPLIT_SCHEMA_VERSION:
        raise StudyConfigError(
            "$dataset_split.schema_version must be "
            f"{DATASET_SPLIT_SCHEMA_VERSION!r}"
        )
    windows = []
    for index, value in enumerate(
        _array(root["windows"], path="$dataset_split.windows")
    ):
        path = f"$dataset_split.windows[{index}]"
        item = _object(value, path=path)
        _fields(
            item,
            required={
                "key",
                "role",
                "market_key",
                "start",
                "end_exclusive",
                "content_sha256",
            },
            optional=set(),
            path=path,
        )
        content_sha256 = item["content_sha256"]
        if content_sha256 is not None:
            content_sha256 = _string(
                content_sha256,
                path=f"{path}.content_sha256",
            )
        windows.append(
            DatasetWindow(
                key=_string(item["key"], path=f"{path}.key"),
                role=_enum(DatasetRole, item["role"], path=f"{path}.role"),
                market_key=_string(
                    item["market_key"],
                    path=f"{path}.market_key",
                ),
                start=_date(item["start"], path=f"{path}.start"),
                end_exclusive=_date(
                    item["end_exclusive"],
                    path=f"{path}.end_exclusive",
                ),
                content_sha256=content_sha256,
            )
        )
    return DatasetSplitSpec(
        schema_version=version,
        split_id=_string(root["split_id"], path="$dataset_split.split_id"),
        description=_string(
            root.get("description", ""),
            path="$dataset_split.description",
        ),
        status=_enum(
            DatasetStatus,
            root["status"],
            path="$dataset_split.status",
        ),
        source=_string(root["source"], path="$dataset_split.source"),
        instrument=_string(
            root["instrument"],
            path="$dataset_split.instrument",
        ),
        interval=_string(root["interval"], path="$dataset_split.interval"),
        proxy_market=_boolean(
            root["proxy_market"],
            path="$dataset_split.proxy_market",
        ),
        windows=tuple(windows),
    )


def load_dataset_split(path: str | Path) -> DatasetSplitSpec:
    return parse_dataset_split(_read_json(Path(path)))


def parse_study_spec(document: Any) -> StudySpec:
    root = _object(document, path="$study")
    _fields(
        root,
        required={
            "schema_version",
            "study_id",
            "strategy_family",
            "baseline_ids",
            "objective_profile",
            "dataset_split",
            "experiment_spec_path",
            "selection_policy",
        },
        optional={"description", "metadata"},
        path="$study",
    )
    version = _string(root["schema_version"], path="$study.schema_version")
    if version != STUDY_SCHEMA_VERSION:
        raise StudyConfigError(
            f"$study.schema_version must be {STUDY_SCHEMA_VERSION!r}"
        )
    objective = _object(
        root["objective_profile"],
        path="$study.objective_profile",
    )
    dataset = _object(
        root["dataset_split"],
        path="$study.dataset_split",
    )
    for value, path in (
        (objective, "$study.objective_profile"),
        (dataset, "$study.dataset_split"),
    ):
        _fields(value, required={"id", "path"}, optional=set(), path=path)
    baseline_ids = _array(root["baseline_ids"], path="$study.baseline_ids")
    metadata = _object(root.get("metadata", {}), path="$study.metadata")
    return StudySpec(
        schema_version=version,
        study_id=_string(root["study_id"], path="$study.study_id"),
        description=_string(
            root.get("description", ""),
            path="$study.description",
        ),
        strategy_family=_string(
            root["strategy_family"],
            path="$study.strategy_family",
        ),
        baseline_ids=tuple(
            _string(value, path=f"$study.baseline_ids[{index}]")
            for index, value in enumerate(baseline_ids)
        ),
        objective_profile_id=_string(
            objective["id"],
            path="$study.objective_profile.id",
        ),
        objective_profile_path=_string(
            objective["path"],
            path="$study.objective_profile.path",
        ),
        dataset_split_id=_string(
            dataset["id"],
            path="$study.dataset_split.id",
        ),
        dataset_split_path=_string(
            dataset["path"],
            path="$study.dataset_split.path",
        ),
        experiment_spec_path=_string(
            root["experiment_spec_path"],
            path="$study.experiment_spec_path",
        ),
        selection_policy=_string(
            root["selection_policy"],
            path="$study.selection_policy",
        ),
        metadata=metadata,
    )


def load_study_bundle(path: str | Path) -> StudyBundle:
    source = Path(path).resolve()
    study = parse_study_spec(_read_json(source))
    root = source.parent
    objective = load_objective_profile(root / study.objective_profile_path)
    dataset = load_dataset_split(root / study.dataset_split_path)
    experiment = load_experiment_spec(root / study.experiment_spec_path)
    if objective.profile_id != study.objective_profile_id:
        raise StudyConfigError(
            "objective profile reference id does not match loaded profile"
        )
    if dataset.split_id != study.dataset_split_id:
        raise StudyConfigError(
            "dataset split reference id does not match loaded split"
        )
    return StudyBundle(
        study=study,
        objective_profile=objective,
        dataset_split=dataset,
        experiment=experiment,
    )
