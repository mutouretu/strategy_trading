"""Public validation and deterministic planning services."""

from __future__ import annotations

from collections.abc import Mapping

from .errors import ExperimentValidationError
from .expansion import expand_scenarios
from .hashing import run_configuration_hash, run_fingerprint
from .json_values import to_plain_json
from .models import (
    CodeRevision,
    ExperimentPlan,
    ExperimentSpec,
    RunSpec,
    ValidationReport,
)
from .registry import ProviderRegistry


def validate_experiment(
    spec: ExperimentSpec,
    registry: ProviderRegistry,
) -> ValidationReport:
    scenarios = expand_scenarios(spec, registry)
    return ValidationReport(
        experiment_id=spec.experiment_id,
        scenario_count=len(scenarios),
        run_count=len(scenarios) * len(spec.seeds),
        provider_ids=tuple(
            dict.fromkeys(
                group.run_provider for group in spec.scenario_groups
            )
        ),
    )


def plan_experiment(
    spec: ExperimentSpec,
    registry: ProviderRegistry,
    *,
    code_revisions: Mapping[str, CodeRevision],
) -> ExperimentPlan:
    """Fully validate and deterministically identify every planned Run."""

    revisions = dict(code_revisions)
    if not revisions:
        raise ExperimentValidationError(
            "plan_experiment requires at least one code revision"
        )
    for name, revision in revisions.items():
        if not isinstance(name, str) or not name.strip():
            raise ExperimentValidationError(
                "code revision names must be non-empty strings"
            )
        if not isinstance(revision, CodeRevision):
            raise ExperimentValidationError(
                f"code revision {name!r} must be CodeRevision"
            )
    scenarios = expand_scenarios(spec, registry)
    runs: list[RunSpec] = []
    seen_configuration_hashes: set[str] = set()
    seen_run_ids: set[str] = set()

    for scenario in scenarios:
        for seed in spec.seeds:
            configuration_hash = run_configuration_hash(
                scenario.configuration,
                seed,
            )
            if configuration_hash in seen_configuration_hashes:
                raise ExperimentValidationError(
                    "duplicate resolved Run configuration"
                )
            seen_configuration_hashes.add(configuration_hash)
            fingerprint = run_fingerprint(
                configuration_hash,
                revisions,
            )
            run_id = fingerprint[:20]
            if run_id in seen_run_ids:
                raise ExperimentValidationError(
                    f"run_id prefix collision for {run_id}"
                )
            seen_run_ids.add(run_id)
            runs.append(
                RunSpec(
                    experiment_id=spec.experiment_id,
                    scenario=scenario,
                    seed=seed,
                    configuration_hash=configuration_hash,
                    run_fingerprint=fingerprint,
                    run_id=run_id,
                )
            )

    return ExperimentPlan(
        experiment=spec,
        scenarios=scenarios,
        runs=tuple(runs),
        code_revisions=revisions,
    )


def plan_to_document(plan: ExperimentPlan) -> dict[str, object]:
    return {
        "experiment_id": plan.experiment.experiment_id,
        "scenario_count": plan.scenario_count,
        "seed_count": len(plan.experiment.seeds),
        "run_count": plan.run_count,
        "output_root": plan.experiment.output.root,
        "default_retention_class": (
            plan.experiment.output.default_retention_class.value
        ),
        "code_revisions": {
            name: revision.to_document()
            for name, revision in plan.code_revisions.items()
        },
        "reproducible": plan.reproducible,
        "runs": [
            {
                "position": position,
                "run_id": run.run_id,
                "scenario_id": run.scenario.scenario_id,
                "scenario_group": run.configuration.group_key,
                "run_provider": run.configuration.run_provider,
                "market_key": run.configuration.market.key,
                "strategy_key": run.configuration.strategy.key,
                "execution_key": run.configuration.execution.key,
                "account_key": run.configuration.account.key,
                "parameter_values": to_plain_json(
                    run.configuration.parameter_values
                ),
                "seed": run.seed,
                "configuration_hash": run.configuration_hash,
                "run_fingerprint": run.run_fingerprint,
            }
            for position, run in enumerate(plan.runs, start=1)
        ],
    }
