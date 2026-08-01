"""Evaluate and persist metric sets for stored experiments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from experiment_system import (
    ExperimentMetricStore,
    ExperimentReader,
    RunQuery,
    sha256_document,
)

from .aggregation import aggregate_scenario
from .errors import MetricEvaluationError, MetricInputError
from .inputs import MetricInputBuilder
from .models import (
    MetricEvaluationStatus,
    MetricInputLevel,
    RunMetricEvaluation,
    document_hash,
)
from .registry import MetricRegistry


@dataclass(frozen=True, slots=True)
class EvaluationBatchOutcome:
    metric_set_id: str
    metric_set_version: str
    run_count: int
    evaluated_count: int
    skipped_count: int
    invalid_count: int
    aggregate_count: int = 0


class MetricEvaluationService:
    def __init__(
        self,
        database_path: str | Path,
        *,
        registry: MetricRegistry,
        evaluator_revisions: Mapping[str, object] | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.reader = ExperimentReader(self.database_path)
        self.store = ExperimentMetricStore(self.database_path)
        self.registry = registry
        self.evaluator_revisions = dict(evaluator_revisions or {})
        self.builder = MetricInputBuilder()

    def evaluate_run(
        self,
        run_id: str,
        *,
        metric_set_id: str,
        version: str,
        recompute: bool = False,
    ) -> tuple[dict[str, object], bool]:
        calculator = self.registry.calculator(metric_set_id, version)
        existing = self.store.run_evaluation(run_id, metric_set_id, version)
        if existing is not None and not recompute:
            return existing, False
        detail = self.reader.run_detail(run_id)
        trace = None
        if detail.get("trace_state") == "STORED":
            trace = self.reader.load_trace(run_id)
        elif existing is not None and recompute:
            raise MetricEvaluationError(
                f"Run {run_id!r} Trace is not available; refusing to "
                "replace its stored metric result"
            )
        summary = detail.get("summary")
        run_spec = detail.get("run_spec")
        market_hash = None
        if detail.get("market_path_id") is not None:
            market_hash = self.reader.market_reference(run_id).content_hash
        input_hashes = {
            "summary": sha256_document(summary),
            "trace": sha256_document(trace) if trace is not None else None,
            "market": market_hash,
            "run_spec": sha256_document(run_spec),
        }
        try:
            metric_input = self.builder.build(detail, trace=trace)
            metric_input = self.registry.contribute(metric_input)
            contributor_hash = document_hash(
                dict(metric_input.contributor_versions)
            )
            input_hashes["contributors"] = contributor_hash
            input_fingerprint = document_hash(
                {
                    "input_hashes": input_hashes,
                    "definition_hash": calculator.metric_set.definition_hash,
                }
            )
            values = calculator.calculate(metric_input)
            evaluation = RunMetricEvaluation(
                run_id=metric_input.run_id,
                scenario_id=metric_input.scenario_id,
                metric_set=calculator.metric_set,
                input_fingerprint=input_fingerprint,
                input_level=metric_input.input_level,
                recomputable=trace is not None,
                status=MetricEvaluationStatus.SUCCEEDED,
                values=values,
                input_hashes=input_hashes,
            )
        except MetricInputError as exc:
            input_fingerprint = document_hash(
                {
                    "input_hashes": input_hashes,
                    "definition_hash": calculator.metric_set.definition_hash,
                }
            )
            evaluation = RunMetricEvaluation(
                run_id=str(detail["run_id"]),
                scenario_id=str(detail["scenario_id"]),
                metric_set=calculator.metric_set,
                input_fingerprint=input_fingerprint,
                input_level=(
                    MetricInputLevel.TRACE
                    if trace is not None
                    else MetricInputLevel.SUMMARY
                ),
                recomputable=trace is not None,
                status=MetricEvaluationStatus.INVALID,
                values=(),
                input_hashes=input_hashes,
                issues=(str(exc),),
            )
        saved = self.store.save_run_evaluation(
            calculator.metric_set.to_document(),
            evaluation.to_document(),
            evaluator_revisions=self.evaluator_revisions,
            evaluated_at=datetime.now(timezone.utc),
            replace_existing=recompute,
        )
        stored = self.store.run_evaluation(run_id, metric_set_id, version)
        assert stored is not None
        return stored, saved

    def evaluate_experiment(
        self,
        *,
        metric_set_id: str,
        version: str,
        recompute: bool = False,
        aggregate: bool = True,
    ) -> EvaluationBatchOutcome:
        rows = self.reader.query_runs(
            RunQuery(statuses=("SUCCEEDED",), limit=None)
        ).rows
        evaluated = 0
        skipped = 0
        invalid = 0
        for row in rows:
            document, saved = self.evaluate_run(
                str(row["run_id"]),
                metric_set_id=metric_set_id,
                version=version,
                recompute=recompute,
            )
            if saved:
                evaluated += 1
            else:
                skipped += 1
            if document.get("status") == "INVALID":
                invalid += 1
        aggregate_count = 0
        if aggregate:
            aggregate_count = self.aggregate_experiment(
                metric_set_id=metric_set_id,
                version=version,
            )
        return EvaluationBatchOutcome(
            metric_set_id=metric_set_id,
            metric_set_version=version,
            run_count=len(rows),
            evaluated_count=evaluated,
            skipped_count=skipped,
            invalid_count=invalid,
            aggregate_count=aggregate_count,
        )

    def aggregate_experiment(
        self,
        *,
        metric_set_id: str,
        version: str,
    ) -> int:
        calculator = self.registry.calculator(metric_set_id, version)
        all_rows = self.reader.query_runs(RunQuery(limit=None)).rows
        if not all_rows:
            return 0
        experiment_id = str(all_rows[0]["experiment_id"])
        by_scenario: dict[str, list[Mapping[str, object]]] = {}
        for row in all_rows:
            by_scenario.setdefault(str(row["scenario_id"]), []).append(row)
        saved = 0
        for scenario_id, rows in sorted(by_scenario.items()):
            evaluations = [
                evaluation
                for row in rows
                for evaluation in [
                    self.store.run_evaluation(
                        str(row["run_id"]),
                        metric_set_id,
                        version,
                    )
                ]
                if evaluation is not None
            ]
            aggregate = aggregate_scenario(
                experiment_id=experiment_id,
                scenario_id=scenario_id,
                metric_set=calculator.metric_set,
                run_rows=rows,
                evaluations=evaluations,
            )
            self.store.save_aggregate(
                calculator.metric_set.to_document(),
                aggregate,
                evaluated_at=datetime.now(timezone.utc),
            )
            saved += 1
        return saved
