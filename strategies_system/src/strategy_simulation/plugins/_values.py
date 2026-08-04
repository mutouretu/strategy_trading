from __future__ import annotations

from decimal import Decimal, DecimalException
from typing import Mapping


def check_fields(
    parameters: Mapping[str, object],
    required: set[str],
    *,
    context: str,
) -> None:
    missing = required - set(parameters)
    extra = set(parameters) - required
    if missing:
        raise ValueError(f"{context} is missing parameters: {sorted(missing)}")
    if extra:
        raise ValueError(f"{context} has unknown parameters: {sorted(extra)}")


def text(parameters: Mapping[str, object], key: str, *, context: str) -> str:
    value = parameters[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}.{key} must be a non-empty string")
    return value.strip()


def decimal(
    parameters: Mapping[str, object], key: str, *, context: str
) -> Decimal:
    value = parameters[key]
    if isinstance(value, bool):
        raise ValueError(f"{context}.{key} must be a decimal")
    try:
        converted = Decimal(str(value))
    except (DecimalException, ValueError) as exc:
        raise ValueError(f"{context}.{key} must be a decimal") from exc
    if not converted.is_finite():
        raise ValueError(f"{context}.{key} must be finite")
    return converted


def integer(parameters: Mapping[str, object], key: str, *, context: str) -> int:
    value = parameters[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{context}.{key} must be an integer")
    return value


def boolean(parameters: Mapping[str, object], key: str, *, context: str) -> bool:
    value = parameters[key]
    if not isinstance(value, bool):
        raise ValueError(f"{context}.{key} must be a boolean")
    return value
