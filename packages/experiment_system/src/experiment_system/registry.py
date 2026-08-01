"""Explicit provider registry; experiment files cannot import arbitrary code."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from simulation_runtime import SimulationResult

from .errors import (
    DuplicateProviderError,
    ExperimentValidationError,
    UnknownProviderError,
)
from .json_values import JsonValue
from .models import RunSpec, ScenarioConfiguration


@runtime_checkable
class PreparedRun(Protocol):
    def execute(self) -> SimulationResult:
        """Execute one already prepared simulation."""

    def summarize(
        self,
        result: SimulationResult,
    ) -> Mapping[str, JsonValue]:
        """Return provider-specific raw summary facts."""


@runtime_checkable
class ExperimentRunProvider(Protocol):
    provider_id: str

    def resolve(
        self,
        configuration: ScenarioConfiguration,
    ) -> ScenarioConfiguration:
        """Fill defaults and return the canonical scenario configuration."""

    def validate(self, configuration: ScenarioConfiguration) -> None:
        """Reject incompatible market/strategy/execution/account combinations."""

    def prepare(self, run_spec: RunSpec) -> PreparedRun:
        """Prepare an executable simulation. Not called during planning."""


class ProviderRegistry:
    """Mutable registry assembled explicitly by the host application."""

    def __init__(self) -> None:
        self._providers: dict[str, ExperimentRunProvider] = {}

    def register(self, provider: ExperimentRunProvider) -> None:
        provider_id = getattr(provider, "provider_id", None)
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ExperimentValidationError(
                "providers require a non-empty provider_id"
            )
        for method_name in ("resolve", "validate", "prepare"):
            if not callable(getattr(provider, method_name, None)):
                raise ExperimentValidationError(
                    f"provider {provider_id!r} requires {method_name}()"
                )
        if provider_id in self._providers:
            raise DuplicateProviderError(
                f"provider {provider_id!r} is already registered"
            )
        self._providers[provider_id] = provider

    def get(self, provider_id: str) -> ExperimentRunProvider:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise UnknownProviderError(
                f"provider {provider_id!r} is not registered"
            ) from exc

    @property
    def provider_ids(self) -> tuple[str, ...]:
        return tuple(self._providers)
