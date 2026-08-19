"""Static metadata for registered trading-rule implementations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import Enum

from ._values import ParameterValue, freeze_value


def _required_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _text_tuple(name: str, values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(_required_text(name, value) for value in values)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{name} must not contain duplicates")
    return normalized


def _document_value(value: object) -> object:
    """Convert an immutable Rule-definition value to plain JSON data."""

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
class RuleConfigFieldDefinition:
    """One field in a rule's internal, Strategy-resolved config contract."""

    key: str
    name: str
    description: str = ""
    required: bool = False
    default: ParameterValue | None = None
    choices: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _required_text("key", self.key))
        object.__setattr__(self, "name", _required_text("name", self.name))
        if not isinstance(self.description, str):
            raise TypeError("description must be a string")
        if not isinstance(self.required, bool):
            raise TypeError("required must be a bool")
        object.__setattr__(
            self,
            "choices",
            _text_tuple("choices", tuple(self.choices)),
        )
        object.__setattr__(self, "default", freeze_value(self.default))
        if self.required and self.default is not None:
            raise ValueError("a required config field must not define a default")
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
        }


@dataclass(frozen=True, slots=True)
class TradingRuleDefinition:
    """Describes one reusable rule type without runtime state or config."""

    rule_type: str
    display_name: str
    version: str
    summary: str
    input_types: tuple[str, ...]
    description: str = ""
    family: str = ""
    config_fields: tuple[RuleConfigFieldDefinition, ...] = ()
    output_types: tuple[str, ...] = ("RULE_TRANSITION",)
    supported_directions: tuple[str, ...] = ()
    supported_product_types: tuple[str, ...] = ()
    lifecycle: tuple[str, ...] = ()
    formulae: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("rule_type", "display_name", "version", "summary"):
            object.__setattr__(
                self,
                field_name,
                _required_text(field_name, getattr(self, field_name)),
            )
        for field_name in ("description", "family"):
            if not isinstance(getattr(self, field_name), str):
                raise TypeError(f"{field_name} must be a string")

        config_fields = tuple(self.config_fields)
        if any(
            not isinstance(field, RuleConfigFieldDefinition)
            for field in config_fields
        ):
            raise TypeError(
                "config_fields must contain RuleConfigFieldDefinition values"
            )
        config_keys = [field.key for field in config_fields]
        if len(set(config_keys)) != len(config_keys):
            raise ValueError("config field keys must be unique")
        object.__setattr__(self, "config_fields", config_fields)

        for field_name in (
            "input_types",
            "output_types",
            "supported_directions",
            "supported_product_types",
            "lifecycle",
            "formulae",
            "constraints",
            "aliases",
        ):
            object.__setattr__(
                self,
                field_name,
                _text_tuple(field_name, tuple(getattr(self, field_name))),
            )
        if not self.input_types:
            raise ValueError("input_types must declare at least one rule input")
        if not self.output_types:
            raise ValueError("output_types must declare at least one rule output")
        if self.rule_type in self.aliases:
            raise ValueError("rule_type must not also be an alias")

    def to_document(self) -> dict[str, object]:
        """Return the stable, runtime-state-free catalog representation."""

        return {
            "kind": "trading-rule",
            "type": self.rule_type,
            "rule_type": self.rule_type,
            "display_name": self.display_name,
            "version": self.version,
            "summary": self.summary,
            "description": self.description,
            "family": self.family,
            "input_types": list(self.input_types),
            "config_fields": [
                field.to_document() for field in self.config_fields
            ],
            "output_types": list(self.output_types),
            "supported_directions": list(self.supported_directions),
            "supported_product_types": list(
                self.supported_product_types
            ),
            "lifecycle": list(self.lifecycle),
            "formulae": list(self.formulae),
            "constraints": list(self.constraints),
            "aliases": list(self.aliases),
        }
