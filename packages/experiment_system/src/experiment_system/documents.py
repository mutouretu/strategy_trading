"""Stable JSON documents for experiment metadata and planned Runs."""

from __future__ import annotations

from .json_values import to_plain_json
from .models import (
    ExperimentManifest,
    ExperimentSpec,
    RunSpec,
)


def experiment_spec_to_document(
    spec: ExperimentSpec,
) -> dict[str, object]:
    return {
        "schema_version": spec.schema_version,
        "experiment_id": spec.experiment_id,
        "description": spec.description,
        "scenario_groups": [
            {
                "key": group.key,
                "run_provider": group.run_provider,
                "markets": [
                    component.to_document()
                    for component in group.markets
                ],
                "strategies": [
                    component.to_document()
                    for component in group.strategies
                ],
                "executions": [
                    component.to_document()
                    for component in group.executions
                ],
                "accounts": [
                    component.to_document()
                    for component in group.accounts
                ],
                "parameter_axes": [
                    {
                        "path": axis.path,
                        "values": to_plain_json(axis.values),
                    }
                    for axis in group.parameter_axes
                ],
            }
            for group in spec.scenario_groups
        ],
        "seeds": list(spec.seeds),
        "output": {
            "root": spec.output.root,
            "default_retention_class": (
                spec.output.default_retention_class.value.lower()
            ),
        },
        "controls": {
            "max_runs": spec.controls.max_runs,
            "continue_on_error": spec.controls.continue_on_error,
        },
        "metadata": to_plain_json(spec.metadata),
    }


def run_spec_to_document(run_spec: RunSpec) -> dict[str, object]:
    configuration = run_spec.configuration
    return {
        "schema_version": "run-spec/v1",
        "run_id": run_spec.run_id,
        "experiment_id": run_spec.experiment_id,
        "scenario_id": run_spec.scenario.scenario_id,
        "scenario_hash": run_spec.scenario.scenario_hash,
        "scenario_group": configuration.group_key,
        "run_provider": configuration.run_provider,
        "market": configuration.market.to_document(),
        "strategy": configuration.strategy.to_document(),
        "execution": configuration.execution.to_document(),
        "account": configuration.account.to_document(),
        "parameter_values": to_plain_json(
            configuration.parameter_values
        ),
        "seed": run_spec.seed,
        "configuration_hash": run_spec.configuration_hash,
        "run_fingerprint": run_spec.run_fingerprint,
    }


def code_revisions_to_document(
    revisions,
) -> dict[str, object]:
    return {
        name: revision.to_document()
        for name, revision in revisions.items()
    }


def manifest_to_document(
    manifest: ExperimentManifest,
) -> dict[str, object]:
    return {
        "schema_version": "experiment-manifest/v1",
        "experiment": experiment_spec_to_document(
            manifest.experiment
        ),
        "code_revisions": code_revisions_to_document(
            manifest.code_revisions
        ),
        "reproducible": manifest.reproducible,
        "created_at": manifest.created_at.isoformat(),
        "planned_run_count": manifest.planned_run_count,
    }
