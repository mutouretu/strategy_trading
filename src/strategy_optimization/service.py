"""Study validation and deterministic planning services."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from experiment_system import (
    CodeRevision,
    ProviderRegistry,
    plan_experiment,
    plan_to_document,
    validate_experiment,
)
from metric_system import MetricRegistry

from .compiler import compile_study
from .models import StudyBundle, StudyPlan


@dataclass(frozen=True, slots=True)
class StudyValidationReport:
    study_id: str
    experiment_id: str
    candidate_count: int
    run_count: int
    study_fingerprint: str
    protocol_fingerprint: str
    dataset_status: str
    formal_ready: bool


def validate_study(
    bundle: StudyBundle,
    *,
    provider_registry: ProviderRegistry,
    metric_registry: MetricRegistry,
) -> StudyValidationReport:
    compiled = compile_study(bundle, metric_registry=metric_registry)
    report = validate_experiment(compiled.experiment, provider_registry)
    return StudyValidationReport(
        study_id=bundle.study.study_id,
        experiment_id=compiled.experiment.experiment_id,
        candidate_count=report.scenario_count,
        run_count=report.run_count,
        study_fingerprint=compiled.study_fingerprint,
        protocol_fingerprint=compiled.protocol_fingerprint,
        dataset_status=bundle.dataset_split.status.value,
        formal_ready=compiled.formal_ready,
    )


def plan_study(
    bundle: StudyBundle,
    *,
    provider_registry: ProviderRegistry,
    metric_registry: MetricRegistry,
    code_revisions: Mapping[str, CodeRevision],
) -> StudyPlan:
    compiled = compile_study(bundle, metric_registry=metric_registry)
    experiment_plan = plan_experiment(
        compiled.experiment,
        provider_registry,
        code_revisions=code_revisions,
    )
    return StudyPlan(compiled=compiled, experiment_plan=experiment_plan)


def study_plan_to_document(plan: StudyPlan) -> dict[str, object]:
    return {
        "study_id": plan.compiled.bundle.study.study_id,
        "study_fingerprint": plan.compiled.study_fingerprint,
        "protocol_fingerprint": plan.compiled.protocol_fingerprint,
        "dataset_status": plan.compiled.bundle.dataset_split.status.value,
        "formal_ready": plan.compiled.formal_ready,
        "candidate_count": plan.candidate_count,
        **plan_to_document(plan.experiment_plan),
    }
