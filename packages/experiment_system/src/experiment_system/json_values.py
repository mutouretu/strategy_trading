"""Immutable JSON-compatible values used by experiment specifications."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any, TypeAlias

from .errors import ExperimentConfigError


JsonScalar: TypeAlias = None | bool | int | str | Decimal
JsonValue: TypeAlias = (
    JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]
)


def freeze_json(value: Any, *, path: str = "$") -> JsonValue:
    """Validate and recursively freeze a JSON-like value.

    Binary floats are intentionally rejected. Financial decimal values must
    be represented by strings in input documents; providers may use Decimal
    while resolving defaults.
    """

    if value is None or isinstance(value, (str, Decimal)):
        if isinstance(value, Decimal) and not value.is_finite():
            raise ExperimentConfigError(f"{path} must be a finite Decimal")
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        raise ExperimentConfigError(
            f"{path} must not use a JSON float; use a decimal string"
        )
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ExperimentConfigError(
                    f"{path} object keys must be strings"
                )
            frozen[key] = freeze_json(item, path=f"{path}/{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(
            freeze_json(item, path=f"{path}/{index}")
            for index, item in enumerate(value)
        )
    raise ExperimentConfigError(
        f"{path} contains unsupported value type "
        f"{type(value).__name__}"
    )


def _decimal_to_string(value: Decimal) -> str:
    normalized = format(value, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    if normalized in {"", "-0"}:
        return "0"
    return normalized


def to_plain_json(value: Any) -> Any:
    """Convert frozen/domain values to ordinary JSON-compatible objects."""

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ExperimentConfigError("Decimal values must be finite")
        return _decimal_to_string(value)
    if isinstance(value, Enum):
        return to_plain_json(value.value)
    if isinstance(value, Mapping):
        return {
            str(key): to_plain_json(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [to_plain_json(item) for item in value]
    raise ExperimentConfigError(
        f"cannot convert {type(value).__name__} to canonical JSON"
    )


def require_mapping(
    value: JsonValue,
    *,
    path: str,
) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ExperimentConfigError(f"{path} must be an object")
    return value
