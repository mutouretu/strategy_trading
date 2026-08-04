"""Domain models for experiment configuration and deterministic plans."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType

from .errors import ExperimentConfigError
from .json_values import JsonValue, freeze_json, require_mapping, to_plain_json


EXPERIMENT_SCHEMA_VERSION = "experiment-spec/v1"
_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _require_identifier(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_PATTERN.fullmatch(value):
        raise ExperimentConfigError(
            f"{name} must use lowercase letters, digits, '-' or '_'"
        )
    return value


def _require_nonempty(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExperimentConfigError(f"{name} must not be empty")
    return value


def _require_unique_component_keys(
    components: tuple["ComponentSpec", ...],
    *,
    name: str,
) -> None:
    keys = [component.key for component in components]
    if len(keys) != len(set(keys)):
        raise ExperimentConfigError(f"{name} component keys must be unique")


class RetentionClass(str, Enum):
    STANDARD = "STANDARD"
    ARCHIVED = "ARCHIVED"


class RunStatus(str, Enum):
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ExperimentStatus(str, Enum):
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class TraceState(str, Enum):
    STORED = "STORED"
    PURGED = "PURGED"


@dataclass(frozen=True, slots=True)
class ComponentSpec:
    key: str
    type: str
    parameters: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_identifier(self.key, name="component key")
        _require_nonempty(self.type, name="component type")
        frozen = freeze_json(self.parameters, path=f"component[{self.key}]")
        object.__setattr__(
            self,
            "parameters",
            require_mapping(frozen, path=f"component[{self.key}]"),
        )

    def to_document(self, *, include_key: bool = True) -> dict[str, object]:
        document: dict[str, object] = {
            "type": self.type,
            "parameters": to_plain_json(self.parameters),
        }
        if include_key:
            document["key"] = self.key
        return document


@dataclass(frozen=True, slots=True)
class ParameterAxis:
    path: str
    values: tuple[JsonValue, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path.startswith("/"):
            raise ExperimentConfigError(
                "parameter axis path must be an absolute JSON Pointer"
            )
        frozen = freeze_json(self.values, path=f"axis[{self.path}].values")
        if not isinstance(frozen, tuple) or not frozen:
            raise ExperimentConfigError(
                f"parameter axis {self.path!r} must contain values"
            )
        object.__setattr__(self, "values", frozen)


@dataclass(frozen=True, slots=True)
class ScenarioGroupSpec:
    key: str
    run_provider: str
    markets: tuple[ComponentSpec, ...]
    strategies: tuple[ComponentSpec, ...]
    executions: tuple[ComponentSpec, ...]
    accounts: tuple[ComponentSpec, ...]
    parameter_axes: tuple[ParameterAxis, ...] = ()

    def __post_init__(self) -> None:
        _require_identifier(self.key, name="scenario group key")
        _require_nonempty(self.run_provider, name="run_provider")
        component_sets = {
            "markets": self.markets,
            "strategies": self.strategies,
            "executions": self.executions,
            "accounts": self.accounts,
        }
        for name, components in component_sets.items():
            if not components:
                raise ExperimentConfigError(
                    f"scenario group {self.key!r} requires {name}"
                )
            _require_unique_component_keys(components, name=name)
        axis_paths = [axis.path for axis in self.parameter_axes]
        if len(axis_paths) != len(set(axis_paths)):
            raise ExperimentConfigError(
                f"scenario group {self.key!r} has duplicate parameter paths"
            )


@dataclass(frozen=True, slots=True)
class OutputSpec:
    root: str = "experiment_results"
    default_retention_class: RetentionClass = RetentionClass.STANDARD

    def __post_init__(self) -> None:
        _require_nonempty(self.root, name="output.root")


@dataclass(frozen=True, slots=True)
class ExperimentControls:
    max_runs: int = 1_000
    continue_on_error: bool = True

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_runs, bool)
            or not isinstance(self.max_runs, int)
            or self.max_runs <= 0
        ):
            raise ExperimentConfigError("controls.max_runs must be > 0")
        if not isinstance(self.continue_on_error, bool):
            raise ExperimentConfigError(
                "controls.continue_on_error must be a boolean"
            )


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    experiment_id: str
    scenario_groups: tuple[ScenarioGroupSpec, ...]
    seeds: tuple[int, ...]
    description: str = ""
    schema_version: str = EXPERIMENT_SCHEMA_VERSION
    output: OutputSpec = field(default_factory=OutputSpec)
    controls: ExperimentControls = field(default_factory=ExperimentControls)
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_identifier(self.experiment_id, name="experiment_id")
        if self.schema_version != EXPERIMENT_SCHEMA_VERSION:
            raise ExperimentConfigError(
                f"schema_version must be {EXPERIMENT_SCHEMA_VERSION!r}"
            )
        if not isinstance(self.description, str):
            raise ExperimentConfigError("description must be a string")
        if not self.scenario_groups:
            raise ExperimentConfigError("scenario_groups must not be empty")
        group_keys = [group.key for group in self.scenario_groups]
        if len(group_keys) != len(set(group_keys)):
            raise ExperimentConfigError(
                "scenario group keys must be unique"
            )
        if not self.seeds:
            raise ExperimentConfigError("seeds must not be empty")
        for seed in self.seeds:
            if isinstance(seed, bool) or not isinstance(seed, int):
                raise ExperimentConfigError("seeds must contain integers")
        if len(self.seeds) != len(set(self.seeds)):
            raise ExperimentConfigError("seeds must not contain duplicates")
        frozen = freeze_json(self.metadata, path="metadata")
        object.__setattr__(
            self,
            "metadata",
            require_mapping(frozen, path="metadata"),
        )


@dataclass(frozen=True, slots=True)
class ScenarioConfiguration:
    group_key: str
    run_provider: str
    market: ComponentSpec
    strategy: ComponentSpec
    execution: ComponentSpec
    account: ComponentSpec
    parameter_values: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_identifier(self.group_key, name="scenario group key")
        _require_nonempty(self.run_provider, name="run_provider")
        frozen = freeze_json(self.parameter_values, path="parameter_values")
        object.__setattr__(
            self,
            "parameter_values",
            require_mapping(frozen, path="parameter_values"),
        )

    def semantic_document(self) -> dict[str, object]:
        return {
            "schema_version": "scenario-config/v1",
            "run_provider": self.run_provider,
            "market": self.market.to_document(include_key=False),
            "strategy": self.strategy.to_document(include_key=False),
            "execution": self.execution.to_document(include_key=False),
            "account": self.account.to_document(include_key=False),
        }


@dataclass(frozen=True, slots=True)
class Scenario:
    configuration: ScenarioConfiguration
    scenario_hash: str
    scenario_id: str


@dataclass(frozen=True, slots=True)
class CodeRevision:
    commit: str
    dirty: bool = False
    dirty_fingerprint: str | None = None
    tag: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty(self.commit, name="code revision commit")
        if not isinstance(self.dirty, bool):
            raise ExperimentConfigError("code revision dirty must be boolean")
        if self.dirty_fingerprint is not None:
            _require_nonempty(
                self.dirty_fingerprint,
                name="dirty code revision fingerprint",
            )
        if self.dirty and not self.dirty_fingerprint:
            raise ExperimentConfigError(
                "dirty code revisions require dirty_fingerprint"
            )
        if not self.dirty and self.dirty_fingerprint is not None:
            raise ExperimentConfigError(
                "clean code revisions cannot have dirty_fingerprint"
            )
        if self.tag is not None:
            _require_nonempty(self.tag, name="code revision tag")

    def fingerprint_document(self) -> dict[str, object]:
        return {
            "commit": self.commit,
            "dirty": self.dirty,
            "dirty_fingerprint": self.dirty_fingerprint,
        }

    def to_document(self) -> dict[str, object]:
        return {
            **self.fingerprint_document(),
            "tag": self.tag,
        }


@dataclass(frozen=True, slots=True)
class RunSpec:
    experiment_id: str
    scenario: Scenario
    seed: int
    configuration_hash: str
    run_fingerprint: str
    run_id: str

    @property
    def configuration(self) -> ScenarioConfiguration:
        return self.scenario.configuration

    def semantic_document(self) -> dict[str, object]:
        return {
            **self.configuration.semantic_document(),
            "seed": self.seed,
        }


@dataclass(frozen=True, slots=True)
class ExperimentPlan:
    experiment: ExperimentSpec
    scenarios: tuple[Scenario, ...]
    runs: tuple[RunSpec, ...]
    code_revisions: Mapping[str, CodeRevision]

    def __post_init__(self) -> None:
        revisions = dict(self.code_revisions)
        if not revisions:
            raise ExperimentConfigError(
                "an experiment plan requires code revisions"
            )
        for name, revision in revisions.items():
            _require_identifier(name, name="code repository name")
            if not isinstance(revision, CodeRevision):
                raise ExperimentConfigError(
                    f"code revision {name!r} must be CodeRevision"
                )
        object.__setattr__(
            self,
            "code_revisions",
            MappingProxyType(revisions),
        )

    @property
    def scenario_count(self) -> int:
        return len(self.scenarios)

    @property
    def run_count(self) -> int:
        return len(self.runs)

    @property
    def reproducible(self) -> bool:
        return all(
            not revision.dirty
            for revision in self.code_revisions.values()
        )


@dataclass(frozen=True, slots=True)
class ExperimentManifest:
    experiment: ExperimentSpec
    code_revisions: Mapping[str, CodeRevision]
    created_at: datetime
    planned_run_count: int

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None:
            raise ExperimentConfigError(
                "manifest created_at must be timezone-aware"
            )
        if self.planned_run_count <= 0:
            raise ExperimentConfigError(
                "manifest planned_run_count must be > 0"
            )
        revisions = dict(self.code_revisions)
        if not revisions:
            raise ExperimentConfigError(
                "manifest requires code revisions"
            )
        for name, revision in revisions.items():
            _require_identifier(name, name="code repository name")
            if not isinstance(revision, CodeRevision):
                raise ExperimentConfigError(
                    f"code revision {name!r} must be CodeRevision"
                )
        object.__setattr__(
            self,
            "code_revisions",
            MappingProxyType(revisions),
        )

    @property
    def reproducible(self) -> bool:
        return all(
            not revision.dirty
            for revision in self.code_revisions.values()
        )


@dataclass(frozen=True, slots=True)
class ValidationReport:
    experiment_id: str
    scenario_count: int
    run_count: int
    provider_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    experiment_id: str
    scenario_id: str
    configuration_hash: str
    run_fingerprint: str
    seed: int
    status: RunStatus
    code_revisions: Mapping[str, CodeRevision]
    retention_class: RetentionClass = RetentionClass.STANDARD
    trace_state: TraceState | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    error: Mapping[str, JsonValue] | None = None
    market_path_id: str | None = None
    archived_at: datetime | None = None
    archive_reason: str | None = None

    def __post_init__(self) -> None:
        revisions_dict = dict(self.code_revisions)
        if not revisions_dict:
            raise ExperimentConfigError(
                "RunRecord requires code revisions"
            )
        for name, revision in revisions_dict.items():
            _require_identifier(name, name="code repository name")
            if not isinstance(revision, CodeRevision):
                raise ExperimentConfigError(
                    f"code revision {name!r} must be CodeRevision"
                )
        revisions = MappingProxyType(revisions_dict)
        object.__setattr__(self, "code_revisions", revisions)
        for name, timestamp in (
            ("started_at", self.started_at),
            ("finished_at", self.finished_at),
            ("archived_at", self.archived_at),
        ):
            if timestamp is not None and timestamp.tzinfo is None:
                raise ExperimentConfigError(
                    f"{name} must be timezone-aware"
                )
        if self.market_path_id is not None:
            _require_nonempty(
                self.market_path_id,
                name="market_path_id",
            )
        if self.archive_reason is not None:
            _require_nonempty(
                self.archive_reason,
                name="archive_reason",
            )
        if (
            self.duration_seconds is not None
            and self.duration_seconds < 0
        ):
            raise ExperimentConfigError(
                "duration_seconds must be >= 0"
            )
        if self.error is not None:
            frozen = freeze_json(self.error, path="run error")
            object.__setattr__(
                self,
                "error",
                require_mapping(frozen, path="run error"),
            )

    @property
    def reproducible(self) -> bool:
        return all(
            not revision.dirty
            for revision in self.code_revisions.values()
        )


@dataclass(frozen=True, slots=True)
class TracePurgeReport:
    run_ids: tuple[str, ...]
    payload_bytes: int

    def __post_init__(self) -> None:
        if self.payload_bytes < 0:
            raise ExperimentConfigError(
                "payload_bytes must be >= 0"
            )

    @property
    def run_count(self) -> int:
        return len(self.run_ids)
