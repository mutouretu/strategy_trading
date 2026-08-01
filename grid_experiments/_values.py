"""Strict component-parameter conversion helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any


def check_fields(
    parameters: Mapping[str, object],
    *,
    required: set[str],
    optional: set[str],
    context: str,
) -> None:
    missing = required - set(parameters)
    if missing:
        raise ValueError(
            f"{context} is missing parameters: {sorted(missing)}"
        )
    unknown = set(parameters) - required - optional
    if unknown:
        raise ValueError(
            f"{context} has unknown parameters: {sorted(unknown)}"
        )


def string(
    parameters: Mapping[str, object],
    key: str,
    *,
    context: str,
) -> str:
    value = parameters.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}.{key} must be a non-empty string")
    return value


def decimal_value(
    parameters: Mapping[str, object],
    key: str,
    *,
    context: str,
) -> Decimal:
    value = parameters.get(key)
    if not isinstance(value, (str, Decimal)):
        raise ValueError(
            f"{context}.{key} must be a decimal string"
        )
    try:
        converted = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(
            f"{context}.{key} must be a decimal string"
        ) from exc
    if not converted.is_finite():
        raise ValueError(f"{context}.{key} must be finite")
    return converted


def integer(
    parameters: Mapping[str, object],
    key: str,
    *,
    context: str,
) -> int:
    value = parameters.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{context}.{key} must be an integer")
    return value


def boolean(
    parameters: Mapping[str, object],
    key: str,
    *,
    context: str,
) -> bool:
    value = parameters.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{context}.{key} must be a boolean")
    return value


def sequence(
    value: Any,
    *,
    context: str,
) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{context} must be an array")
    return value
