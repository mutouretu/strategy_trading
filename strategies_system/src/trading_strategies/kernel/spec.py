"""Immutable Strategy-resolved configuration for one trading rule."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, is_dataclass
from typing import Generic, TypeVar

from ._values import ParameterValue, freeze_mapping


RuleConfigT = TypeVar("RuleConfigT")


def _required_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _freeze_config(config: RuleConfigT) -> RuleConfigT:
    if isinstance(config, Mapping):
        return freeze_mapping(config)  # type: ignore[return-value]
    if is_dataclass(config):
        dataclass_parameters = getattr(type(config), "__dataclass_params__")
        if not dataclass_parameters.frozen:
            raise TypeError("rule config dataclasses must be frozen")
        return config
    if config is None or isinstance(
        config,
        (str, bytes, int, float, bool, tuple, frozenset),
    ):
        return config
    raise TypeError("rule config must be immutable")


@dataclass(frozen=True, slots=True)
class TradingRuleSpec(Generic[RuleConfigT]):
    """Binds one rule type to resolved internal config, not Strategy input."""

    rule_type: str
    config: RuleConfigT
    capability_references: Mapping[str, ParameterValue] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "rule_type",
            _required_text("rule_type", self.rule_type),
        )
        object.__setattr__(self, "config", _freeze_config(self.config))
        object.__setattr__(
            self,
            "capability_references",
            freeze_mapping(self.capability_references),
        )
