"""Immutable strategy research Study and protocol models."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from experiment_system import ExperimentPlan, ExperimentSpec, canonical_json

from .errors import StudyConfigError


STUDY_SCHEMA_VERSION = "strategy-study/v1"
OBJECTIVE_PROFILE_SCHEMA_VERSION = "objective-profile/v1"
DATASET_SPLIT_SCHEMA_VERSION = "dataset-split/v1"
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def require_identifier(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise StudyConfigError(
            f"{name} must use lowercase letters, digits, '-' or '_'"
        )
    return value


def require_text(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StudyConfigError(f"{name} must not be empty")
    return value


def frozen_mapping(
    value: Mapping[str, object],
    *,
    name: str,
) -> Mapping[str, object]:
    try:
        normalized = __import__("json").loads(canonical_json(value))
    except (TypeError, ValueError) as exc:
        raise StudyConfigError(f"{name} must contain JSON values") from exc
    if not isinstance(normalized, dict):
        raise StudyConfigError(f"{name} must be an object")
    return MappingProxyType(normalized)


class StudyStatus(StrEnum):
    DRAFT = "DRAFT"
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    EXECUTED = "EXECUTED"
    EVALUATED = "EVALUATED"
    SELECTED = "SELECTED"
    INVALIDATED = "INVALIDATED"


class DatasetStatus(StrEnum):
    DEVELOPMENT = "DEVELOPMENT"
    BOUNDARIES_LOCKED = "BOUNDARIES_LOCKED"
    CONTENT_LOCKED = "CONTENT_LOCKED"


class DatasetRole(StrEnum):
    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    HOLDOUT = "HOLDOUT"


class ObjectiveDirection(StrEnum):
    MAXIMIZE = "MAXIMIZE"
    MINIMIZE = "MINIMIZE"


class ComparisonMode(StrEnum):
    DIRECT = "DIRECT"
    DELTA_FROM_BASELINE = "DELTA_FROM_BASELINE"


class ConstraintOperator(StrEnum):
    EQ = "EQ"
    GTE = "GTE"
    LTE = "LTE"


@dataclass(frozen=True, slots=True)
class MetricSelector:
    metric_set_id: str
    metric_set_version: str
    metric_key: str
    unit: str
    dimensions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_text(self.metric_set_id, name="metric_set_id")
        require_text(self.metric_set_version, name="metric_set_version")
        require_text(self.metric_key, name="metric_key")
        require_text(self.unit, name="metric unit")
        values = {str(key): str(value) for key, value in self.dimensions.items()}
        if any(not key.strip() or not value.strip() for key, value in values.items()):
            raise StudyConfigError("metric dimensions must not be empty")
        object.__setattr__(self, "dimensions", MappingProxyType(values))

    def to_document(self) -> dict[str, object]:
        return {
            "metric_set_id": self.metric_set_id,
            "metric_set_version": self.metric_set_version,
            "metric_key": self.metric_key,
            "unit": self.unit,
            "dimensions": dict(self.dimensions),
        }


@dataclass(frozen=True, slots=True)
class ObjectiveSpec:
    key: str
    selector: MetricSelector
    direction: ObjectiveDirection
    comparison: ComparisonMode = ComparisonMode.DIRECT

    def __post_init__(self) -> None:
        require_identifier(self.key, name="objective key")

    def to_document(self) -> dict[str, object]:
        return {
            "key": self.key,
            "selector": self.selector.to_document(),
            "direction": self.direction.value,
            "comparison": self.comparison.value,
        }


@dataclass(frozen=True, slots=True)
class EligibilityConstraint:
    key: str
    selector: MetricSelector
    operator: ConstraintOperator
    value: object

    def __post_init__(self) -> None:
        require_identifier(self.key, name="constraint key")
        try:
            canonical_json(self.value)
        except (TypeError, ValueError) as exc:
            raise StudyConfigError(
                f"constraint {self.key!r} value must be JSON-compatible"
            ) from exc

    def to_document(self) -> dict[str, object]:
        return {
            "key": self.key,
            "selector": self.selector.to_document(),
            "operator": self.operator.value,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class ObjectiveProfile:
    profile_id: str
    valuation_asset: str
    baseline_strategy_type: str
    objectives: tuple[ObjectiveSpec, ...]
    eligibility_constraints: tuple[EligibilityConstraint, ...]
    description: str = ""
    schema_version: str = OBJECTIVE_PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_identifier(self.profile_id, name="objective profile_id")
        require_text(self.valuation_asset, name="valuation_asset")
        require_text(self.baseline_strategy_type, name="baseline_strategy_type")
        if self.schema_version != OBJECTIVE_PROFILE_SCHEMA_VERSION:
            raise StudyConfigError(
                f"objective profile schema_version must be "
                f"{OBJECTIVE_PROFILE_SCHEMA_VERSION!r}"
            )
        if not self.objectives:
            raise StudyConfigError("objective profile requires objectives")
        objective_keys = [item.key for item in self.objectives]
        constraint_keys = [item.key for item in self.eligibility_constraints]
        if len(objective_keys) != len(set(objective_keys)):
            raise StudyConfigError("objective keys must be unique")
        if len(constraint_keys) != len(set(constraint_keys)):
            raise StudyConfigError("constraint keys must be unique")

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "description": self.description,
            "valuation_asset": self.valuation_asset,
            "baseline_strategy_type": self.baseline_strategy_type,
            "objectives": [item.to_document() for item in self.objectives],
            "eligibility_constraints": [
                item.to_document() for item in self.eligibility_constraints
            ],
        }


@dataclass(frozen=True, slots=True)
class DatasetWindow:
    key: str
    role: DatasetRole
    market_key: str
    start: date
    end_exclusive: date
    content_sha256: str | None = None

    def __post_init__(self) -> None:
        require_identifier(self.key, name="dataset window key")
        require_identifier(self.market_key, name="dataset window market_key")
        if self.start >= self.end_exclusive:
            raise StudyConfigError(
                f"dataset window {self.key!r} must have start < end_exclusive"
            )
        if self.content_sha256 is not None and not _SHA256.fullmatch(
            self.content_sha256
        ):
            raise StudyConfigError(
                f"dataset window {self.key!r} content_sha256 is invalid"
            )

    def to_document(self) -> dict[str, object]:
        return {
            "key": self.key,
            "role": self.role.value,
            "market_key": self.market_key,
            "start": self.start.isoformat(),
            "end_exclusive": self.end_exclusive.isoformat(),
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True, slots=True)
class DatasetSplitSpec:
    split_id: str
    status: DatasetStatus
    source: str
    instrument: str
    interval: str
    windows: tuple[DatasetWindow, ...]
    proxy_market: bool = False
    description: str = ""
    schema_version: str = DATASET_SPLIT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_identifier(self.split_id, name="dataset split_id")
        require_text(self.source, name="dataset source")
        require_text(self.instrument, name="dataset instrument")
        require_text(self.interval, name="dataset interval")
        if self.schema_version != DATASET_SPLIT_SCHEMA_VERSION:
            raise StudyConfigError(
                f"dataset schema_version must be "
                f"{DATASET_SPLIT_SCHEMA_VERSION!r}"
            )
        if not isinstance(self.proxy_market, bool):
            raise StudyConfigError("proxy_market must be a boolean")
        roles = [window.role for window in self.windows]
        expected = {DatasetRole.TRAIN, DatasetRole.VALIDATION, DatasetRole.HOLDOUT}
        if set(roles) != expected or len(roles) != len(expected):
            raise StudyConfigError(
                "dataset split requires exactly one TRAIN, VALIDATION and HOLDOUT window"
            )
        keys = [window.key for window in self.windows]
        market_keys = [window.market_key for window in self.windows]
        if len(keys) != len(set(keys)):
            raise StudyConfigError("dataset window keys must be unique")
        if len(market_keys) != len(set(market_keys)):
            raise StudyConfigError("dataset window market_keys must be unique")
        ordered = sorted(self.windows, key=lambda item: item.start)
        if any(
            left.end_exclusive > right.start
            for left, right in zip(ordered, ordered[1:], strict=False)
        ):
            raise StudyConfigError("dataset windows must not overlap")
        role_order = [window.role for window in ordered]
        if role_order != [
            DatasetRole.TRAIN,
            DatasetRole.VALIDATION,
            DatasetRole.HOLDOUT,
        ]:
            raise StudyConfigError(
                "dataset windows must be ordered TRAIN, VALIDATION, HOLDOUT"
            )
        if self.status is DatasetStatus.CONTENT_LOCKED and any(
            window.content_sha256 is None for window in self.windows
        ):
            raise StudyConfigError(
                "CONTENT_LOCKED dataset windows require content_sha256"
            )

    def window(self, role: DatasetRole) -> DatasetWindow:
        return next(item for item in self.windows if item.role is role)

    @property
    def formal_ready(self) -> bool:
        return self.status is DatasetStatus.CONTENT_LOCKED

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "split_id": self.split_id,
            "description": self.description,
            "status": self.status.value,
            "source": self.source,
            "instrument": self.instrument,
            "interval": self.interval,
            "proxy_market": self.proxy_market,
            "windows": [window.to_document() for window in self.windows],
        }


@dataclass(frozen=True, slots=True)
class StudySpec:
    study_id: str
    strategy_family: str
    baseline_ids: tuple[str, ...]
    objective_profile_id: str
    objective_profile_path: str
    dataset_split_id: str
    dataset_split_path: str
    experiment_spec_path: str
    selection_policy: str
    description: str = ""
    metadata: Mapping[str, object] = field(default_factory=dict)
    schema_version: str = STUDY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        require_identifier(self.study_id, name="study_id")
        require_text(self.strategy_family, name="strategy_family")
        require_identifier(self.objective_profile_id, name="objective_profile_id")
        require_identifier(self.dataset_split_id, name="dataset_split_id")
        require_text(self.objective_profile_path, name="objective_profile_path")
        require_text(self.dataset_split_path, name="dataset_split_path")
        require_text(self.experiment_spec_path, name="experiment_spec_path")
        require_text(self.selection_policy, name="selection_policy")
        if self.schema_version != STUDY_SCHEMA_VERSION:
            raise StudyConfigError(
                f"study schema_version must be {STUDY_SCHEMA_VERSION!r}"
            )
        if not self.baseline_ids:
            raise StudyConfigError("Study requires at least one baseline_id")
        if len(self.baseline_ids) != len(set(self.baseline_ids)):
            raise StudyConfigError("baseline_ids must be unique")
        for value in self.baseline_ids:
            require_text(value, name="baseline_id")
        object.__setattr__(
            self,
            "metadata",
            frozen_mapping(self.metadata, name="study metadata"),
        )

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "study_id": self.study_id,
            "description": self.description,
            "strategy_family": self.strategy_family,
            "baseline_ids": list(self.baseline_ids),
            "objective_profile": {
                "id": self.objective_profile_id,
                "path": self.objective_profile_path,
            },
            "dataset_split": {
                "id": self.dataset_split_id,
                "path": self.dataset_split_path,
            },
            "experiment_spec_path": self.experiment_spec_path,
            "selection_policy": self.selection_policy,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class StudyBundle:
    study: StudySpec
    objective_profile: ObjectiveProfile
    dataset_split: DatasetSplitSpec
    experiment: ExperimentSpec


@dataclass(frozen=True, slots=True)
class CompiledStudy:
    bundle: StudyBundle
    experiment: ExperimentSpec
    study_fingerprint: str
    protocol_fingerprint: str

    @property
    def formal_ready(self) -> bool:
        return self.bundle.dataset_split.formal_ready


@dataclass(frozen=True, slots=True)
class StudyPlan:
    compiled: CompiledStudy
    experiment_plan: ExperimentPlan

    @property
    def candidate_count(self) -> int:
        return self.experiment_plan.scenario_count

    @property
    def run_count(self) -> int:
        return self.experiment_plan.run_count


@dataclass(frozen=True, slots=True)
class StoredStudy:
    study_id: str
    experiment_id: str
    status: StudyStatus
    study_fingerprint: str
    protocol_fingerprint: str
    formal_ready: bool
    created_at: datetime
    updated_at: datetime
