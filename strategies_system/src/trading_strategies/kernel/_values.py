"""Small immutable-value helpers used by strategy kernel contracts."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import TypeAlias


ParameterValue: TypeAlias = object


def freeze_value(value: ParameterValue) -> ParameterValue:
    """Recursively detach mutable configuration containers."""

    if isinstance(value, Mapping):
        return freeze_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze_value(item) for item in value)
    return value


def freeze_mapping(
    values: Mapping[str, ParameterValue],
) -> Mapping[str, ParameterValue]:
    frozen: dict[str, ParameterValue] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("parameter keys must be non-empty strings")
        frozen[key] = freeze_value(value)
    return MappingProxyType(frozen)
