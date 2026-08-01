"""Strict JSON parsing for experiment-spec/v1."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .errors import ExperimentConfigError
from .json_values import freeze_json
from .models import (
    EXPERIMENT_SCHEMA_VERSION,
    ComponentSpec,
    ExperimentControls,
    ExperimentSpec,
    OutputSpec,
    ParameterAxis,
    RetentionClass,
    ScenarioGroupSpec,
)


def _object(value: Any, *, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ExperimentConfigError(f"{path} must be an object")
    for key in value:
        if not isinstance(key, str):
            raise ExperimentConfigError(f"{path} keys must be strings")
    return value


def _array(value: Any, *, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ExperimentConfigError(f"{path} must be an array")
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
        raise ExperimentConfigError(
            f"{path} is missing required fields: {sorted(missing)}"
        )
    unknown = set(document) - required - optional
    if unknown:
        raise ExperimentConfigError(
            f"{path} contains unknown fields: {sorted(unknown)}"
        )


def _string(value: Any, *, path: str) -> str:
    if not isinstance(value, str):
        raise ExperimentConfigError(f"{path} must be a string")
    return value


def _boolean(value: Any, *, path: str) -> bool:
    if not isinstance(value, bool):
        raise ExperimentConfigError(f"{path} must be a boolean")
    return value


def _integer(value: Any, *, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExperimentConfigError(f"{path} must be an integer")
    return value


def _component(value: Any, *, path: str) -> ComponentSpec:
    document = _object(value, path=path)
    _fields(
        document,
        required={"key", "type"},
        optional={"parameters"},
        path=path,
    )
    parameters = document.get("parameters", {})
    frozen_parameters = freeze_json(
        _object(parameters, path=f"{path}.parameters"),
        path=f"{path}.parameters",
    )
    assert isinstance(frozen_parameters, Mapping)
    return ComponentSpec(
        key=_string(document["key"], path=f"{path}.key"),
        type=_string(document["type"], path=f"{path}.type"),
        parameters=frozen_parameters,
    )


def _component_list(value: Any, *, path: str) -> tuple[ComponentSpec, ...]:
    items = _array(value, path=path)
    return tuple(
        _component(item, path=f"{path}[{index}]")
        for index, item in enumerate(items)
    )


def _parameter_axis(value: Any, *, path: str) -> ParameterAxis:
    document = _object(value, path=path)
    _fields(
        document,
        required={"path", "values"},
        optional=set(),
        path=path,
    )
    values = _array(document["values"], path=f"{path}.values")
    frozen = freeze_json(values, path=f"{path}.values")
    assert isinstance(frozen, tuple)
    return ParameterAxis(
        path=_string(document["path"], path=f"{path}.path"),
        values=frozen,
    )


def _scenario_group(value: Any, *, path: str) -> ScenarioGroupSpec:
    document = _object(value, path=path)
    _fields(
        document,
        required={
            "key",
            "run_provider",
            "markets",
            "strategies",
            "executions",
            "accounts",
        },
        optional={"parameter_axes"},
        path=path,
    )
    axes = _array(
        document.get("parameter_axes", []),
        path=f"{path}.parameter_axes",
    )
    return ScenarioGroupSpec(
        key=_string(document["key"], path=f"{path}.key"),
        run_provider=_string(
            document["run_provider"],
            path=f"{path}.run_provider",
        ),
        markets=_component_list(
            document["markets"],
            path=f"{path}.markets",
        ),
        strategies=_component_list(
            document["strategies"],
            path=f"{path}.strategies",
        ),
        executions=_component_list(
            document["executions"],
            path=f"{path}.executions",
        ),
        accounts=_component_list(
            document["accounts"],
            path=f"{path}.accounts",
        ),
        parameter_axes=tuple(
            _parameter_axis(item, path=f"{path}.parameter_axes[{index}]")
            for index, item in enumerate(axes)
        ),
    )


def _output(value: Any) -> OutputSpec:
    document = _object(value, path="$.output")
    _fields(
        document,
        required=set(),
        optional={"root", "default_retention_class"},
        path="$.output",
    )
    raw_retention = _string(
        document.get("default_retention_class", "standard"),
        path="$.output.default_retention_class",
    ).upper()
    try:
        retention = RetentionClass(raw_retention)
    except ValueError as exc:
        raise ExperimentConfigError(
            "$.output.default_retention_class must be "
            "'standard' or 'archived'"
        ) from exc
    return OutputSpec(
        root=_string(
            document.get("root", "experiment_results"),
            path="$.output.root",
        ),
        default_retention_class=retention,
    )


def _controls(value: Any) -> ExperimentControls:
    document = _object(value, path="$.controls")
    _fields(
        document,
        required=set(),
        optional={"max_runs", "continue_on_error"},
        path="$.controls",
    )
    return ExperimentControls(
        max_runs=_integer(
            document.get("max_runs", 1_000),
            path="$.controls.max_runs",
        ),
        continue_on_error=_boolean(
            document.get("continue_on_error", True),
            path="$.controls.continue_on_error",
        ),
    )


def parse_experiment_spec(document: Any) -> ExperimentSpec:
    """Parse and validate a decoded JSON experiment document."""

    root = _object(document, path="$")
    _fields(
        root,
        required={"schema_version", "experiment_id", "scenario_groups", "seeds"},
        optional={"description", "output", "controls", "metadata"},
        path="$",
    )
    version = _string(root["schema_version"], path="$.schema_version")
    if version != EXPERIMENT_SCHEMA_VERSION:
        raise ExperimentConfigError(
            f"$.schema_version must be {EXPERIMENT_SCHEMA_VERSION!r}"
        )
    groups = _array(root["scenario_groups"], path="$.scenario_groups")
    raw_seeds = _array(root["seeds"], path="$.seeds")
    metadata = freeze_json(
        _object(root.get("metadata", {}), path="$.metadata"),
        path="$.metadata",
    )
    assert isinstance(metadata, Mapping)
    return ExperimentSpec(
        schema_version=version,
        experiment_id=_string(
            root["experiment_id"],
            path="$.experiment_id",
        ),
        description=_string(
            root.get("description", ""),
            path="$.description",
        ),
        scenario_groups=tuple(
            _scenario_group(item, path=f"$.scenario_groups[{index}]")
            for index, item in enumerate(groups)
        ),
        seeds=tuple(
            _integer(seed, path=f"$.seeds[{index}]")
            for index, seed in enumerate(raw_seeds)
        ),
        output=_output(root.get("output", {})),
        controls=_controls(root.get("controls", {})),
        metadata=metadata,
    )


def load_experiment_spec(path: str | Path) -> ExperimentSpec:
    """Load an experiment specification from a UTF-8 JSON file."""

    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ExperimentConfigError(
            f"cannot read experiment spec {source}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ExperimentConfigError(
            f"invalid JSON in {source}:{exc.lineno}:{exc.colno}: {exc.msg}"
        ) from exc
    return parse_experiment_spec(document)
