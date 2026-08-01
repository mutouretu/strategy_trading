"""Evaluate both generic and grid-specific metrics."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from metric_system import MetricError, MetricEvaluationService

from grid_experiments.cli import participating_code_revisions

from .registry import build_metric_registry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="grid-metrics")
    subparsers = parser.add_subparsers(dest="command", required=True)
    experiment = subparsers.add_parser("evaluate-experiment")
    experiment.add_argument("database", type=Path)
    experiment.add_argument("--recompute", action="store_true")
    run = subparsers.add_parser("evaluate-run")
    run.add_argument("database", type=Path)
    run.add_argument("run_id")
    run.add_argument("--recompute", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if not arguments.database.is_file():
        print(f"experiment database does not exist: {arguments.database}", file=sys.stderr)
        return 2
    try:
        revisions = {
            key: value.to_document()
            for key, value in participating_code_revisions().items()
        }
        service = MetricEvaluationService(
            arguments.database,
            registry=build_metric_registry(),
            evaluator_revisions=revisions,
        )
        results = []
        for metric_set_id in ("core", "grid"):
            if arguments.command == "evaluate-run":
                evaluation, saved = service.evaluate_run(
                    arguments.run_id,
                    metric_set_id=metric_set_id,
                    version="v1",
                    recompute=arguments.recompute,
                )
                results.append({"metric_set_id": metric_set_id, "saved": saved, "status": evaluation["status"]})
            else:
                outcome = service.evaluate_experiment(
                    metric_set_id=metric_set_id,
                    version="v1",
                    recompute=arguments.recompute,
                )
                results.append({
                    "metric_set_id": metric_set_id,
                    "run_count": outcome.run_count,
                    "evaluated_count": outcome.evaluated_count,
                    "skipped_count": outcome.skipped_count,
                    "invalid_count": outcome.invalid_count,
                    "aggregate_count": outcome.aggregate_count,
                })
        print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
        return 0
    except MetricError as exc:
        print(str(exc), file=sys.stderr)
        return 2
