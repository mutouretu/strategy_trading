"""Shared single-Run and multi-Run execution lifecycle."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import Callable

from simulation_runtime import (
    SimulationResult,
    simulation_result_to_document,
)

from .errors import (
    ExperimentValidationError,
    SingleRunExecutionError,
)
from .json_values import JsonValue, freeze_json, require_mapping, to_plain_json
from .market_data import MarketReference, ParquetMarketStore
from .models import (
    ExperimentManifest,
    ExperimentPlan,
    ExperimentStatus,
    RunRecord,
    RunSpec,
    RunStatus,
)
from .registry import PreparedRun, ProviderRegistry
from .repository import ExperimentRepository


_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|api[_-]?secret|secret|password|token|authorization)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER_CREDENTIAL = re.compile(
    r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"
)


def _safe_error_message(error: Exception) -> str:
    message = _BEARER_CREDENTIAL.sub(
        "Bearer [REDACTED]",
        str(error),
    )
    message = _CREDENTIAL_ASSIGNMENT.sub(
        lambda match: (
            f"{match.group(1)}{match.group(2)}[REDACTED]"
        ),
        message,
    )
    if len(message) > 2_000:
        return f"{message[:2_000]}…[truncated]"
    return message


@dataclass(frozen=True, slots=True)
class SingleRunOutcome:
    record: RunRecord
    summary: Mapping[str, object]
    market_reference: MarketReference


@dataclass(frozen=True, slots=True)
class ExperimentOutcome:
    experiment_id: str
    runs: tuple[SingleRunOutcome, ...]
    records: tuple[RunRecord, ...]
    executed_run_ids: tuple[str, ...]
    skipped_run_ids: tuple[str, ...]
    recovered_run_ids: tuple[str, ...]
    retried_run_ids: tuple[str, ...]

    @property
    def run_count(self) -> int:
        return len(self.records)

    @property
    def succeeded_count(self) -> int:
        return sum(
            record.status is RunStatus.SUCCEEDED
            for record in self.records
        )

    @property
    def failed_count(self) -> int:
        return sum(
            record.status is RunStatus.FAILED
            for record in self.records
        )

    @property
    def planned_count(self) -> int:
        return sum(
            record.status is RunStatus.PLANNED
            for record in self.records
        )

    @property
    def running_count(self) -> int:
        return sum(
            record.status is RunStatus.RUNNING
            for record in self.records
        )

    @property
    def executed_count(self) -> int:
        return len(self.executed_run_ids)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped_run_ids)

    @property
    def status(self) -> ExperimentStatus:
        if self.failed_count:
            return ExperimentStatus.FAILED
        if self.running_count:
            return ExperimentStatus.RUNNING
        if self.planned_count:
            if self.succeeded_count:
                return ExperimentStatus.RUNNING
            return ExperimentStatus.PLANNED
        return ExperimentStatus.SUCCEEDED


def _reporting_value(
    parameters: Mapping[str, JsonValue],
    key: str,
    default: str,
) -> str:
    value = parameters.get(key, default)
    if not isinstance(value, str) or not value.strip():
        return default
    return value


def _result_documents(
    result: SimulationResult,
    *,
    run_spec: RunSpec,
    reproducible: bool,
    provider_summary: Mapping[str, JsonValue],
    market_reference: MarketReference,
) -> tuple[dict[str, object], dict[str, object]]:
    configuration = run_spec.configuration
    viewer_document = simulation_result_to_document(
        result,
        run_id=run_spec.run_id,
        interval=_reporting_value(
            configuration.market.parameters,
            "interval",
            "1d",
        ),
        source=configuration.market.type,
        seed=run_spec.seed,
        manifest={
            "experiment_id": run_spec.experiment_id,
            "scenario_id": run_spec.scenario.scenario_id,
            "configuration_hash": run_spec.configuration_hash,
            "run_fingerprint": run_spec.run_fingerprint,
            "market_path_id": market_reference.market_path_id,
        },
    )
    runtime_summary = viewer_document.pop("summary")
    viewer_document.pop("market")
    if not isinstance(runtime_summary, dict):
        raise ExperimentValidationError(
            "runtime reporting summary must be an object"
        )
    summary = {
        "schema_version": "run-summary/v1",
        "run_id": run_spec.run_id,
        "experiment_id": run_spec.experiment_id,
        "scenario_id": run_spec.scenario.scenario_id,
        "seed": run_spec.seed,
        "configuration_hash": run_spec.configuration_hash,
        "run_fingerprint": run_spec.run_fingerprint,
        "reproducible": reproducible,
        "market_path_id": market_reference.market_path_id,
        "result": runtime_summary,
        "provider_summary": {
            configuration.run_provider: to_plain_json(provider_summary)
        },
    }
    trace = {
        "schema_version": "simulation-trace/v1",
        "viewer_schema_version": viewer_document.pop("schema_version"),
        "market_path_id": market_reference.market_path_id,
        **viewer_document,
    }
    return summary, trace


def _provider_summary(
    prepared: PreparedRun,
    result: SimulationResult,
) -> Mapping[str, JsonValue]:
    summary = prepared.summarize(result)
    frozen = freeze_json(summary, path="provider_summary")
    return require_mapping(frozen, path="provider_summary")


def _load_success_outcome(
    run_id: str,
    *,
    repository: ExperimentRepository,
) -> SingleRunOutcome:
    record = repository.get_run_record(run_id)
    if record.status is not RunStatus.SUCCEEDED:
        raise ExperimentValidationError(
            f"Run {run_id!r} is not successful"
        )
    return SingleRunOutcome(
        record=record,
        summary=repository.get_summary(run_id),
        market_reference=repository.get_market_reference(run_id),
    )


def _execute_planned_run(
    run_spec: RunSpec,
    *,
    reproducible: bool,
    registry: ProviderRegistry,
    repository: ExperimentRepository,
    market_store: ParquetMarketStore,
    clock: Callable[[], datetime],
    timer: Callable[[], float],
) -> SingleRunOutcome:
    started_at = clock()
    started_tick = timer()
    repository.start_run(run_spec, started_at=started_at)
    try:
        provider = registry.get(run_spec.configuration.run_provider)
        prepared = provider.prepare(run_spec)
        if not isinstance(prepared, PreparedRun):
            raise ExperimentValidationError(
                "provider prepare() must return PreparedRun"
            )
        result = prepared.execute()
        if not isinstance(result, SimulationResult):
            raise ExperimentValidationError(
                "PreparedRun.execute() must return SimulationResult"
            )
        market_reference = market_store.persist(result.frames)
        provider_summary = _provider_summary(prepared, result)
        summary, trace = _result_documents(
            result,
            run_spec=run_spec,
            reproducible=reproducible,
            provider_summary=provider_summary,
            market_reference=market_reference,
        )
        finished_at = clock()
        duration = max(0.0, timer() - started_tick)
        repository.complete_run(
            run_spec,
            summary=summary,
            trace=trace,
            market_reference=market_reference,
            finished_at=finished_at,
            duration_seconds=duration,
        )
    except Exception as exc:
        finished_at = clock()
        duration = max(0.0, timer() - started_tick)
        error_document = {
            "error_type": type(exc).__name__,
            "message": _safe_error_message(exc),
        }
        try:
            repository.fail_run(
                run_spec,
                error=error_document,
                finished_at=finished_at,
                duration_seconds=duration,
            )
        except Exception as persistence_error:
            raise SingleRunExecutionError(
                run_spec.run_id,
                persistence_error,
            ) from exc
        raise SingleRunExecutionError(run_spec.run_id, exc) from exc

    record = repository.get_run_record(run_spec.run_id)
    return SingleRunOutcome(
        record=record,
        summary=repository.get_summary(run_spec.run_id),
        market_reference=market_reference,
    )


def execute_experiment(
    plan: ExperimentPlan,
    *,
    registry: ProviderRegistry,
    repository: ExperimentRepository,
    market_store: ParquetMarketStore,
    allow_dirty: bool = False,
    rerun_failed: bool = False,
    resume_interrupted: bool = False,
    clock: Callable[[], datetime] | None = None,
    timer: Callable[[], float] = monotonic,
) -> ExperimentOutcome:
    """Execute every planned Run sequentially in deterministic plan order."""

    dirty_repositories = sorted(
        name
        for name, revision in plan.code_revisions.items()
        if revision.dirty
    )
    if dirty_repositories and not allow_dirty:
        raise ExperimentValidationError(
            "formal experiment execution requires clean repositories; "
            f"dirty: {dirty_repositories}. Pass allow_dirty=True only "
            "for an exploratory, non-reproducible Experiment"
        )
    now = clock or (lambda: datetime.now(timezone.utc))
    manifest = ExperimentManifest(
        experiment=plan.experiment,
        code_revisions=plan.code_revisions,
        created_at=now(),
        planned_run_count=plan.run_count,
    )
    if dirty_repositories:
        repository.create_experiment(plan, manifest)
    else:
        repository.create_or_resume_experiment(plan, manifest)

    initial_records = tuple(
        repository.get_run_record(run.run_id)
        for run in plan.runs
    )
    interrupted = tuple(
        record.run_id
        for record in initial_records
        if record.status is RunStatus.RUNNING
    )
    if interrupted and not resume_interrupted:
        raise ExperimentValidationError(
            "experiment contains interrupted RUNNING Runs; pass "
            f"resume_interrupted=True to recover them: {interrupted}"
        )

    successful: dict[str, SingleRunOutcome] = {}
    executed: list[str] = []
    skipped: list[str] = []
    recovered: list[str] = []
    retried: list[str] = []
    for run_spec in plan.runs:
        record = repository.get_run_record(run_spec.run_id)
        if record.status is RunStatus.SUCCEEDED:
            skipped.append(run_spec.run_id)
            successful[run_spec.run_id] = _load_success_outcome(
                run_spec.run_id,
                repository=repository,
            )
            continue
        if record.status is RunStatus.FAILED:
            if not rerun_failed:
                skipped.append(run_spec.run_id)
                continue
            repository.reset_failed_run(run_spec)
            retried.append(run_spec.run_id)
        elif record.status is RunStatus.RUNNING:
            repository.recover_interrupted_run(run_spec)
            recovered.append(run_spec.run_id)

        executed.append(run_spec.run_id)
        try:
            successful[run_spec.run_id] = _execute_planned_run(
                run_spec,
                reproducible=plan.reproducible,
                registry=registry,
                repository=repository,
                market_store=market_store,
                clock=now,
                timer=timer,
            )
        except SingleRunExecutionError:
            if (
                plan.run_count == 1
                or not plan.experiment.controls.continue_on_error
            ):
                raise

    records = tuple(
        repository.get_run_record(run.run_id)
        for run in plan.runs
    )
    return ExperimentOutcome(
        experiment_id=plan.experiment.experiment_id,
        runs=tuple(
            successful[run.run_id]
            for run in plan.runs
            if run.run_id in successful
        ),
        records=records,
        executed_run_ids=tuple(executed),
        skipped_run_ids=tuple(skipped),
        recovered_run_ids=tuple(recovered),
        retried_run_ids=tuple(retried),
    )


def execute_single_run(
    plan: ExperimentPlan,
    *,
    registry: ProviderRegistry,
    repository: ExperimentRepository,
    market_store: ParquetMarketStore,
    allow_dirty: bool = False,
    rerun_failed: bool = False,
    resume_interrupted: bool = False,
    clock: Callable[[], datetime] | None = None,
    timer: Callable[[], float] = monotonic,
) -> SingleRunOutcome:
    """Execute one Run through the shared Experiment execution path."""

    if plan.run_count != 1:
        raise ExperimentValidationError(
            "execute_single_run requires a plan containing one Run"
        )
    outcome = execute_experiment(
        plan,
        registry=registry,
        repository=repository,
        market_store=market_store,
        allow_dirty=allow_dirty,
        rerun_failed=rerun_failed,
        resume_interrupted=resume_interrupted,
        clock=clock,
        timer=timer,
    )
    if not outcome.runs:
        record = outcome.records[0]
        raise ExperimentValidationError(
            f"Run {record.run_id!r} remains {record.status.value}; "
            "explicit retry or interrupted recovery is required"
        )
    return outcome.runs[0]
