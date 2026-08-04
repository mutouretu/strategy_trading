"""Explicit metric calculator and input-contributor registry."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from .errors import MetricDefinitionError
from .inputs import MetricInput, MetricInputContributor
from .models import MetricSet, MetricValue


class MetricCalculator(Protocol):
    metric_set: MetricSet

    def calculate(self, metric_input: MetricInput) -> tuple[MetricValue, ...]: ...


class MetricRegistry:
    def __init__(self) -> None:
        self._calculators: dict[tuple[str, str], MetricCalculator] = {}
        self._contributors: dict[str, list[MetricInputContributor]] = {}

    def register_calculator(self, calculator: MetricCalculator) -> None:
        key = (calculator.metric_set.metric_set_id, calculator.metric_set.version)
        if key in self._calculators:
            raise MetricDefinitionError(
                f"metric calculator {key[0]}/{key[1]} is already registered"
            )
        self._calculators[key] = calculator

    def register_contributor(
        self,
        contributor: MetricInputContributor,
    ) -> None:
        values = self._contributors.setdefault(contributor.provider_id, [])
        if any(
            item.contributor_name == contributor.contributor_name
            for item in values
        ):
            raise MetricDefinitionError(
                f"metric contributor {contributor.contributor_name!r} "
                "is already registered"
            )
        values.append(contributor)

    def calculator(self, metric_set_id: str, version: str) -> MetricCalculator:
        try:
            return self._calculators[(metric_set_id, version)]
        except KeyError as exc:
            raise MetricDefinitionError(
                f"metric set {metric_set_id}/{version} is not registered"
            ) from exc

    def calculators(self) -> tuple[MetricCalculator, ...]:
        return tuple(
            self._calculators[key]
            for key in sorted(self._calculators)
        )

    def contribute(self, metric_input: MetricInput) -> MetricInput:
        result = metric_input
        for contributor in self._contributors.get(
            metric_input.run_provider,
            (),
        ):
            result = contributor.contribute(result)
        return result

    def extend(self, calculators: Iterable[MetricCalculator]) -> None:
        for calculator in calculators:
            self.register_calculator(calculator)
