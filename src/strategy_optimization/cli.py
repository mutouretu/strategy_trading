"""Command-line entry point for Study validation, planning and execution."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from experiment_system import (
    CodeRevision,
    ExperimentError,
    ParquetMarketStore,
    SQLiteExperimentRepository,
    execute_experiment,
)

from strategy_simulation.cli import participating_code_revisions
from strategy_simulation.experiment_provider import build_provider_registry
from strategy_simulation.metrics.registry import build_metric_registry

from .errors import StudyError, StudyRepositoryError
from .baseline import build_baseline_report
from .models import StudyStatus
from .repository import SQLiteStudyRepository
from .schema import load_study_bundle
from .service import plan_study, study_plan_to_document, validate_study


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="strategy-optimization")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "plan", "run"):
        item = subparsers.add_parser(command)
        item.add_argument("study", type=Path)
        if command == "run":
            item.add_argument("--database", type=Path)
            item.add_argument("--market-root", type=Path)
            item.add_argument("--allow-dirty", action="store_true")
            item.add_argument(
                "--allow-development-data",
                action="store_true",
                help="allow an exploratory Run before dataset content is locked",
            )
    baseline = subparsers.add_parser("baseline-report")
    baseline.add_argument("study", type=Path)
    baseline.add_argument("--database", type=Path, required=True)
    return parser


def _revisions(
    injected: Mapping[str, CodeRevision] | None,
) -> Mapping[str, CodeRevision]:
    return injected or participating_code_revisions()


def _validation_document(report) -> dict[str, object]:
    return {
        "study_id": report.study_id,
        "experiment_id": report.experiment_id,
        "candidate_count": report.candidate_count,
        "run_count": report.run_count,
        "study_fingerprint": report.study_fingerprint,
        "protocol_fingerprint": report.protocol_fingerprint,
        "dataset_status": report.dataset_status,
        "formal_ready": report.formal_ready,
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    code_revisions: Mapping[str, CodeRevision] | None = None,
) -> int:
    arguments = _parser().parse_args(argv)
    try:
        bundle = load_study_bundle(arguments.study)
        if arguments.command == "baseline-report":
            report = build_baseline_report(arguments.database, bundle)
            now = datetime.now(timezone.utc)
            studies = SQLiteStudyRepository(arguments.database)
            saved = studies.save_baseline_report(
                bundle.study.study_id,
                report,
                created_at=now,
            )
            stored = studies.get(bundle.study.study_id)
            if stored.status is StudyStatus.EXECUTED:
                stored = studies.transition(
                    stored.study_id,
                    StudyStatus.EVALUATED,
                    changed_at=now,
                    reason="6B baseline metrics and HODL comparison evaluated",
                )
            print(
                json.dumps(
                    {
                        "saved": saved,
                        "study_status": stored.status.value,
                        "report": report,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        providers = build_provider_registry()
        metrics = build_metric_registry()
        if arguments.command == "validate":
            report = validate_study(
                bundle,
                provider_registry=providers,
                metric_registry=metrics,
            )
            print(json.dumps(_validation_document(report), ensure_ascii=False, indent=2))
            return 0

        revisions = _revisions(code_revisions)
        plan = plan_study(
            bundle,
            provider_registry=providers,
            metric_registry=metrics,
            code_revisions=revisions,
        )
        if arguments.command == "plan":
            print(
                json.dumps(
                    study_plan_to_document(plan),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        dirty = sorted(
            name for name, revision in revisions.items() if revision.dirty
        )
        if dirty and not arguments.allow_dirty:
            raise StudyError(
                "formal Study execution requires clean repositories; "
                f"dirty: {dirty}"
            )
        if not plan.compiled.formal_ready and not arguments.allow_development_data:
            raise StudyError(
                "dataset content is not locked; use --allow-development-data "
                "only for an exploratory 6A scaffold Run"
            )
        study_source = arguments.study.resolve()
        experiment_source = (
            study_source.parent / bundle.study.experiment_spec_path
        ).resolve()
        output_root = Path(plan.compiled.experiment.output.root)
        if not output_root.is_absolute():
            output_root = experiment_source.parent / output_root
        database = arguments.database or (
            output_root / f"{plan.compiled.experiment.experiment_id}.sqlite3"
        )
        market_root = arguments.market_root or (
            experiment_source.parent / "market_data"
        )
        outcome = execute_experiment(
            plan.experiment_plan,
            registry=providers,
            repository=SQLiteExperimentRepository(database),
            market_store=ParquetMarketStore(market_root),
            allow_dirty=arguments.allow_dirty,
        )
        now = datetime.now(timezone.utc)
        studies = SQLiteStudyRepository(database)
        studies.create_or_validate(plan, created_at=now)
        stored = studies.get(bundle.study.study_id)
        if stored.status is StudyStatus.PLANNED:
            studies.transition(
                bundle.study.study_id,
                StudyStatus.RUNNING,
                changed_at=now,
                reason="Experiment execution was started by Study CLI",
            )
            stored = studies.transition(
                bundle.study.study_id,
                StudyStatus.EXECUTED,
                changed_at=now,
                reason="Experiment Runs finished; metrics not yet evaluated",
            )
        print(
            json.dumps(
                {
                    "study_id": stored.study_id,
                    "study_status": stored.status.value,
                    "experiment_id": outcome.experiment_id,
                    "experiment_status": outcome.status.value,
                    "run_count": outcome.run_count,
                    "succeeded_count": outcome.succeeded_count,
                    "failed_count": outcome.failed_count,
                    "formal_ready": stored.formal_ready,
                    "database": str(database.resolve()),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if outcome.failed_count == 0 else 1
    except (StudyError, ExperimentError, StudyRepositoryError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
