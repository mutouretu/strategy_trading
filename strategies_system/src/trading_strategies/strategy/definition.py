"""Static Strategy contracts and catalog metadata."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from enum import Enum
from typing import ClassVar, Generic, TypeVar

from ..kernel._values import ParameterValue, freeze_mapping, freeze_value
from .spec import StrategySpec
from .values import StrategyCoordinationPolicy, required_text


StrategyParametersT = TypeVar("StrategyParametersT")


def _text_tuple(name: str, values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(required_text(name, value) for value in values)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{name} must not contain duplicates")
    return normalized


def _document_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            field.name: _document_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {
            str(key): _document_value(item) for key, item in value.items()
        }
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_document_value(item) for item in value]
    return str(value)


@dataclass(frozen=True, slots=True)
class StrategyParameterDefinition:
    """One external parameter accepted by a StrategyDefinition."""

    key: str
    name: str
    description: str = ""
    required: bool = False
    default: ParameterValue | None = None
    choices: tuple[str, ...] = ()
    group: str = ""
    maps_to: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", required_text("key", self.key))
        object.__setattr__(self, "name", required_text("name", self.name))
        for field_name in ("description", "group"):
            if not isinstance(getattr(self, field_name), str):
                raise TypeError(f"{field_name} must be a string")
        if not isinstance(self.required, bool):
            raise TypeError("required must be a bool")
        object.__setattr__(self, "default", freeze_value(self.default))
        object.__setattr__(
            self,
            "choices",
            _text_tuple("choices", tuple(self.choices)),
        )
        object.__setattr__(
            self,
            "maps_to",
            _text_tuple("maps_to", tuple(self.maps_to)),
        )
        if self.required and self.default is not None:
            raise ValueError("a required parameter must not define a default")
        if self.choices and self.default is not None:
            if str(self.default) not in self.choices:
                raise ValueError("default must be one of choices")

    def to_document(self) -> dict[str, object]:
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "required": self.required,
            "default": _document_value(self.default),
            "choices": list(self.choices),
            "group": self.group,
            "maps_to": list(self.maps_to),
        }


@dataclass(frozen=True, slots=True)
class StrategyRuleCompositionDefinition:
    """One named Rule slot and its responsibilities inside a Strategy."""

    rule_key: str
    rule_type: str
    role: str
    summary: str
    permissions: tuple[str, ...]
    subscriptions: tuple[str, ...]
    parameter_mappings: Mapping[str, str]

    def __post_init__(self) -> None:
        for field_name in ("rule_key", "rule_type", "role", "summary"):
            object.__setattr__(
                self,
                field_name,
                required_text(field_name, getattr(self, field_name)),
            )
        for field_name in ("permissions", "subscriptions"):
            values = _text_tuple(field_name, tuple(getattr(self, field_name)))
            if not values:
                raise ValueError(f"{field_name} must not be empty")
            object.__setattr__(self, field_name, values)
        mappings = {
            required_text("parameter mapping source", source): required_text(
                "parameter mapping target", target
            )
            for source, target in self.parameter_mappings.items()
        }
        object.__setattr__(
            self,
            "parameter_mappings",
            freeze_mapping(mappings),
        )

    def to_document(self) -> dict[str, object]:
        return {
            "rule_key": self.rule_key,
            "rule_type": self.rule_type,
            "role": self.role,
            "summary": self.summary,
            "permissions": list(self.permissions),
            "subscriptions": list(self.subscriptions),
            "parameter_mappings": dict(self.parameter_mappings),
        }


@dataclass(frozen=True, slots=True)
class StrategyDefinitionDescriptor:
    """Runtime-independent catalog record for one Strategy type."""

    strategy_type: str
    display_name: str
    version: str
    summary: str
    family: str
    parameters: tuple[StrategyParameterDefinition, ...]
    rule_composition: tuple[StrategyRuleCompositionDefinition, ...]
    coordination_policy: StrategyCoordinationPolicy
    description: str = ""
    supported_directions: tuple[str, ...] = ()
    supported_product_types: tuple[str, ...] = ()
    lifecycle: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    primary_rule_key: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "strategy_type",
            "display_name",
            "version",
            "summary",
            "family",
        ):
            object.__setattr__(
                self,
                field_name,
                required_text(field_name, getattr(self, field_name)),
            )
        if not isinstance(self.description, str):
            raise TypeError("description must be a string")
        parameters = tuple(self.parameters)
        if any(
            not isinstance(item, StrategyParameterDefinition)
            for item in parameters
        ):
            raise TypeError(
                "parameters must contain StrategyParameterDefinition values"
            )
        if len({item.key for item in parameters}) != len(parameters):
            raise ValueError("Strategy parameter keys must be unique")
        object.__setattr__(self, "parameters", parameters)
        rules = tuple(self.rule_composition)
        if not rules:
            raise ValueError("rule_composition must contain at least one Rule")
        if any(
            not isinstance(item, StrategyRuleCompositionDefinition)
            for item in rules
        ):
            raise TypeError(
                "rule_composition must contain "
                "StrategyRuleCompositionDefinition values"
            )
        if len({item.rule_key for item in rules}) != len(rules):
            raise ValueError("Strategy rule keys must be unique")
        object.__setattr__(self, "rule_composition", rules)
        if not isinstance(self.coordination_policy, StrategyCoordinationPolicy):
            raise TypeError(
                "coordination_policy must be a StrategyCoordinationPolicy"
            )
        for field_name in (
            "supported_directions",
            "supported_product_types",
            "lifecycle",
            "constraints",
            "aliases",
        ):
            object.__setattr__(
                self,
                field_name,
                _text_tuple(field_name, tuple(getattr(self, field_name))),
            )
        if self.strategy_type in self.aliases:
            raise ValueError("strategy_type must not also be an alias")
        if self.primary_rule_key is not None:
            object.__setattr__(
                self,
                "primary_rule_key",
                required_text("primary_rule_key", self.primary_rule_key),
            )
            if self.primary_rule_key not in {item.rule_key for item in rules}:
                raise ValueError("primary_rule_key must identify a Rule slot")

    def to_document(self) -> dict[str, object]:
        return {
            "kind": "strategy-definition",
            "type": self.strategy_type,
            "strategy_type": self.strategy_type,
            "display_name": self.display_name,
            "version": self.version,
            "summary": self.summary,
            "description": self.description,
            "family": self.family,
            "parameters": [item.to_document() for item in self.parameters],
            "rule_composition": [
                item.to_document() for item in self.rule_composition
            ],
            "coordination_policy": self.coordination_policy.to_document(),
            "supported_directions": list(self.supported_directions),
            "supported_product_types": list(
                self.supported_product_types
            ),
            "lifecycle": list(self.lifecycle),
            "constraints": list(self.constraints),
            "aliases": list(self.aliases),
            "primary_rule_key": self.primary_rule_key,
        }


class StrategyDefinition(ABC, Generic[StrategyParametersT]):
    strategy_type: ClassVar[str]

    @classmethod
    @abstractmethod
    def definition(cls) -> StrategyDefinitionDescriptor:
        """Return the state-free catalog contract for this Strategy type."""

        ...

    @abstractmethod
    def bind(self, parameters: StrategyParametersT) -> StrategySpec:
        """Validate Strategy Parameters and resolve every RuleConfig."""

        ...
