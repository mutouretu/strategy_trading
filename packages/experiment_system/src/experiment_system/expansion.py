"""Deterministic scenario-group and parameter-axis expansion."""

from __future__ import annotations

from itertools import product
from math import prod

from .errors import ExperimentValidationError
from .hashing import scenario_hash
from .json_pointer import replace_pointer
from .json_values import JsonValue, freeze_json
from .models import (
    ComponentSpec,
    ExperimentSpec,
    Scenario,
    ScenarioConfiguration,
    ScenarioGroupSpec,
)
from .registry import ProviderRegistry


def _group_run_count(group: ScenarioGroupSpec, seed_count: int) -> int:
    axis_count = prod(len(axis.values) for axis in group.parameter_axes)
    return (
        len(group.markets)
        * len(group.strategies)
        * len(group.executions)
        * len(group.accounts)
        * axis_count
        * seed_count
    )


def planned_run_count(spec: ExperimentSpec) -> int:
    return sum(
        _group_run_count(group, len(spec.seeds))
        for group in spec.scenario_groups
    )


def _component_document(
    market: ComponentSpec,
    strategy: ComponentSpec,
    execution: ComponentSpec,
    account: ComponentSpec,
) -> dict[str, object]:
    return {
        "market": market.to_document(),
        "strategy": strategy.to_document(),
        "execution": execution.to_document(),
        "account": account.to_document(),
    }


def _component_from_document(document: object, *, role: str) -> ComponentSpec:
    if not isinstance(document, dict):
        raise ExperimentValidationError(
            f"expanded {role} component must be an object"
        )
    try:
        key = document["key"]
        component_type = document["type"]
        parameters = document["parameters"]
    except KeyError as exc:
        raise ExperimentValidationError(
            f"expanded {role} component is missing {exc.args[0]!r}"
        ) from exc
    if not isinstance(key, str) or not isinstance(component_type, str):
        raise ExperimentValidationError(
            f"expanded {role} component key and type must be strings"
        )
    if not isinstance(parameters, dict):
        raise ExperimentValidationError(
            f"expanded {role} parameters must be an object"
        )
    return ComponentSpec(
        key=key,
        type=component_type,
        parameters=freeze_json(
            parameters,
            path=f"expanded.{role}.parameters",
        ),
    )


def _apply_axis_values(
    base: dict[str, object],
    group: ScenarioGroupSpec,
    values: tuple[JsonValue, ...],
) -> tuple[dict[str, object], dict[str, JsonValue]]:
    updated = base
    selected: dict[str, JsonValue] = {}
    for axis, value in zip(group.parameter_axes, values, strict=True):
        updated = replace_pointer(updated, axis.path, value)
        selected[axis.path] = value
    return updated, selected


def expand_scenarios(
    spec: ExperimentSpec,
    registry: ProviderRegistry,
) -> tuple[Scenario, ...]:
    """Expand and validate every scenario before any Run can execute."""

    run_count = planned_run_count(spec)
    if run_count > spec.controls.max_runs:
        raise ExperimentValidationError(
            f"experiment expands to {run_count} runs, exceeding "
            f"controls.max_runs={spec.controls.max_runs}"
        )

    scenarios: list[Scenario] = []
    seen_hashes: dict[str, str] = {}
    seen_ids: dict[str, str] = {}
    for group in spec.scenario_groups:
        provider = registry.get(group.run_provider)
        axis_products = (
            product(*(axis.values for axis in group.parameter_axes))
            if group.parameter_axes
            else [()]
        )
        # Materialize because the same axis value set applies to every
        # component combination and itertools.product iterators are one-shot.
        axis_combinations = tuple(axis_products)

        for market, strategy, execution, account in product(
            group.markets,
            group.strategies,
            group.executions,
            group.accounts,
        ):
            base = _component_document(
                market,
                strategy,
                execution,
                account,
            )
            for axis_values in axis_combinations:
                expanded, selected = _apply_axis_values(
                    base,
                    group,
                    axis_values,
                )
                candidate = ScenarioConfiguration(
                    group_key=group.key,
                    run_provider=group.run_provider,
                    market=_component_from_document(
                        expanded["market"],
                        role="market",
                    ),
                    strategy=_component_from_document(
                        expanded["strategy"],
                        role="strategy",
                    ),
                    execution=_component_from_document(
                        expanded["execution"],
                        role="execution",
                    ),
                    account=_component_from_document(
                        expanded["account"],
                        role="account",
                    ),
                    parameter_values=selected,
                )
                try:
                    resolved = provider.resolve(candidate)
                except Exception as exc:
                    raise ExperimentValidationError(
                        f"provider {group.run_provider!r} could not resolve "
                        f"scenario group {group.key!r}: {exc}"
                    ) from exc
                if not isinstance(resolved, ScenarioConfiguration):
                    raise ExperimentValidationError(
                        f"provider {group.run_provider!r} resolve() must "
                        "return ScenarioConfiguration"
                    )
                if (
                    resolved.group_key != candidate.group_key
                    or resolved.run_provider != candidate.run_provider
                ):
                    raise ExperimentValidationError(
                        f"provider {group.run_provider!r} cannot change "
                        "group_key or run_provider during resolution"
                    )
                if (
                    resolved.parameter_values != candidate.parameter_values
                    or resolved.market.key != candidate.market.key
                    or resolved.strategy.key != candidate.strategy.key
                    or resolved.execution.key != candidate.execution.key
                    or resolved.account.key != candidate.account.key
                ):
                    raise ExperimentValidationError(
                        f"provider {group.run_provider!r} cannot change "
                        "component keys or selected parameter-axis values"
                    )
                try:
                    provider.validate(resolved)
                except Exception as exc:
                    raise ExperimentValidationError(
                        f"provider {group.run_provider!r} rejected "
                        f"scenario group {group.key!r}: {exc}"
                    ) from exc

                full_hash = scenario_hash(resolved)
                if full_hash in seen_hashes:
                    raise ExperimentValidationError(
                        "duplicate resolved scenario configuration in "
                        f"groups {seen_hashes[full_hash]!r} and {group.key!r}"
                    )
                seen_hashes[full_hash] = group.key
                scenario_id = full_hash[:16]
                if (
                    scenario_id in seen_ids
                    and seen_ids[scenario_id] != full_hash
                ):
                    raise ExperimentValidationError(
                        f"scenario_id prefix collision for {scenario_id}"
                    )
                seen_ids[scenario_id] = full_hash
                scenarios.append(
                    Scenario(
                        configuration=resolved,
                        scenario_hash=full_hash,
                        scenario_id=scenario_id,
                    )
                )
    return tuple(scenarios)
