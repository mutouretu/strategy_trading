"""Stable metric definitions and result values."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from .errors import MetricDefinitionError


class MetricInputLevel(StrEnum):
    SUMMARY = "SUMMARY"
    TRACE = "TRACE"
    MARKET = "MARKET"

    @property
    def rank(self) -> int:
        return {
            MetricInputLevel.SUMMARY: 1,
            MetricInputLevel.TRACE: 2,
            MetricInputLevel.MARKET: 3,
        }[self]


class MetricValueType(StrEnum):
    DECIMAL = "DECIMAL"
    INTEGER = "INTEGER"
    BOOLEAN = "BOOLEAN"
    TIMESTAMP = "TIMESTAMP"
    TEXT = "TEXT"


class MetricValueStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"


class MetricEvaluationStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    INVALID = "INVALID"


class AdverseDirection(StrEnum):
    HIGHER = "HIGHER"
    LOWER = "LOWER"
    NONE = "NONE"


def _identifier(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MetricDefinitionError(f"{name} must not be empty")
    return value


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise MetricDefinitionError("metric Decimal must be finite")
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def canonical_document(document: object) -> str:
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def document_hash(document: object) -> str:
    return hashlib.sha256(
        canonical_document(document).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    metric_key: str
    display_name: str
    category: str
    description: str
    value_type: MetricValueType
    unit_kind: str
    required_input_level: MetricInputLevel
    dimensions: tuple[str, ...] = ()
    adverse_direction: AdverseDirection = AdverseDirection.NONE
    formula_version: str = "1"

    def __post_init__(self) -> None:
        for name in (
            "metric_key",
            "display_name",
            "category",
            "description",
            "unit_kind",
            "formula_version",
        ):
            _identifier(getattr(self, name), name=name)
        if len(self.dimensions) != len(set(self.dimensions)):
            raise MetricDefinitionError(
                f"metric {self.metric_key!r} has duplicate dimensions"
            )
        if any(not item.strip() for item in self.dimensions):
            raise MetricDefinitionError("metric dimensions must not be empty")

    def to_document(self) -> dict[str, object]:
        return {
            "metric_key": self.metric_key,
            "display_name": self.display_name,
            "category": self.category,
            "description": self.description,
            "value_type": self.value_type.value,
            "unit_kind": self.unit_kind,
            "required_input_level": self.required_input_level.value,
            "dimensions": list(self.dimensions),
            "adverse_direction": self.adverse_direction.value,
            "formula_version": self.formula_version,
        }


@dataclass(frozen=True, slots=True)
class MetricSet:
    metric_set_id: str
    version: str
    definitions: tuple[MetricDefinition, ...]
    description: str

    def __post_init__(self) -> None:
        _identifier(self.metric_set_id, name="metric_set_id")
        _identifier(self.version, name="version")
        _identifier(self.description, name="description")
        if not self.definitions:
            raise MetricDefinitionError("MetricSet definitions must not be empty")
        keys = [definition.metric_key for definition in self.definitions]
        if len(keys) != len(set(keys)):
            raise MetricDefinitionError("MetricSet metric keys must be unique")

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "metric-set/v1",
            "metric_set_id": self.metric_set_id,
            "version": self.version,
            "description": self.description,
            "definitions": [
                definition.to_document()
                for definition in sorted(
                    self.definitions,
                    key=lambda item: item.metric_key,
                )
            ],
        }

    @property
    def definition_hash(self) -> str:
        return document_hash(self.to_document())

    def definition(self, metric_key: str) -> MetricDefinition:
        for definition in self.definitions:
            if definition.metric_key == metric_key:
                return definition
        raise MetricDefinitionError(
            f"metric {metric_key!r} is not registered in "
            f"{self.metric_set_id}/{self.version}"
        )


MetricScalar = Decimal | int | bool | str


@dataclass(frozen=True, slots=True)
class MetricValue:
    metric_key: str
    value_type: MetricValueType
    unit: str
    source_level: MetricInputLevel
    status: MetricValueStatus
    value: MetricScalar | None = None
    dimensions: Mapping[str, str] = field(default_factory=dict)
    reason_code: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.metric_key, name="metric_key")
        _identifier(self.unit, name="unit")
        dimensions = dict(self.dimensions)
        if any(not str(key).strip() for key in dimensions):
            raise MetricDefinitionError("dimension keys must not be empty")
        object.__setattr__(
            self,
            "dimensions",
            MappingProxyType(
                {str(key): str(value) for key, value in dimensions.items()}
            ),
        )
        if self.status is MetricValueStatus.AVAILABLE:
            if self.value is None:
                raise MetricDefinitionError(
                    "available metric values require a value"
                )
            if self.reason_code is not None:
                raise MetricDefinitionError(
                    "available metric values cannot have a reason_code"
                )
            self._validate_type()
        else:
            if self.value is not None:
                raise MetricDefinitionError(
                    "unavailable or invalid values must not carry a value"
                )
            _identifier(self.reason_code or "", name="reason_code")

    def _validate_type(self) -> None:
        assert self.value is not None
        expected = {
            MetricValueType.DECIMAL: Decimal,
            MetricValueType.INTEGER: int,
            MetricValueType.BOOLEAN: bool,
            MetricValueType.TIMESTAMP: int,
            MetricValueType.TEXT: str,
        }[self.value_type]
        if self.value_type in {
            MetricValueType.INTEGER,
            MetricValueType.TIMESTAMP,
        } and isinstance(self.value, bool):
            raise MetricDefinitionError("boolean is not an integer metric")
        if not isinstance(self.value, expected):
            raise MetricDefinitionError(
                f"metric {self.metric_key!r} expected "
                f"{self.value_type.value}"
            )
        if isinstance(self.value, Decimal) and not self.value.is_finite():
            raise MetricDefinitionError("metric Decimal must be finite")

    @property
    def dimensions_json(self) -> str:
        return canonical_document(dict(self.dimensions))

    def to_document(self) -> dict[str, object]:
        if isinstance(self.value, Decimal):
            value: object = _decimal_text(self.value)
        else:
            value = self.value
        return {
            "metric_key": self.metric_key,
            "value_type": self.value_type.value,
            "unit": self.unit,
            "source_level": self.source_level.value,
            "status": self.status.value,
            "value": value,
            "dimensions": dict(self.dimensions),
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class RunMetricEvaluation:
    run_id: str
    scenario_id: str
    metric_set: MetricSet
    input_fingerprint: str
    input_level: MetricInputLevel
    recomputable: bool
    status: MetricEvaluationStatus
    values: tuple[MetricValue, ...]
    input_hashes: Mapping[str, str | None]
    issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier(self.run_id, name="run_id")
        _identifier(self.scenario_id, name="scenario_id")
        _identifier(self.input_fingerprint, name="input_fingerprint")
        object.__setattr__(
            self,
            "input_hashes",
            MappingProxyType(dict(self.input_hashes)),
        )
        identities = [
            (value.metric_key, value.dimensions_json)
            for value in self.values
        ]
        if len(identities) != len(set(identities)):
            raise MetricDefinitionError(
                "metric evaluation has duplicate key/dimensions values"
            )
        for value in self.values:
            definition = self.metric_set.definition(value.metric_key)
            if value.value_type is not definition.value_type:
                raise MetricDefinitionError(
                    f"metric {value.metric_key!r} value type differs "
                    "from its definition"
                )
            if set(value.dimensions) != set(definition.dimensions):
                raise MetricDefinitionError(
                    f"metric {value.metric_key!r} dimensions differ "
                    "from its definition"
                )

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "run-metric-evaluation/v1",
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "metric_set_id": self.metric_set.metric_set_id,
            "metric_set_version": self.metric_set.version,
            "definition_hash": self.metric_set.definition_hash,
            "input_fingerprint": self.input_fingerprint,
            "input_level": self.input_level.value,
            "recomputable": self.recomputable,
            "status": self.status.value,
            "input_hashes": dict(self.input_hashes),
            "issues": list(self.issues),
            "values": [value.to_document() for value in self.values],
        }
