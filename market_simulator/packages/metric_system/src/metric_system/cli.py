"""Command-line entry point for generic experiment metrics."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from experiment_system import collect_code_revisions

from .core import CoreMetricCalculator
from .errors import MetricError
from .registry import MetricRegistry
from .service import MetricEvaluationService


def build_registry() -> MetricRegistry:
    registry = MetricRegistry()
    registry.register_calculator(CoreMetricCalculator())
    return registry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="metric-system")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("evaluate-run")
    run.add_argument("database", type=Path)
    run.add_argument("run_id")
    run.add_argument("--metric-set", default="core")
    run.add_argument("--version", default="v1")
    run.add_argument("--recompute", action="store_true")
    experiment = subparsers.add_parser("evaluate-experiment")
    experiment.add_argument("database", type=Path)
    experiment.add_argument("--metric-set", default="core")
    experiment.add_argument("--version", default="v1")
    experiment.add_argument("--recompute", action="store_true")
    aggregate = subparsers.add_parser("aggregate")
    aggregate.add_argument("database", type=Path)
    aggregate.add_argument("--metric-set", default="core")
    aggregate.add_argument("--version", default="v1")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    registry: MetricRegistry | None = None,
    evaluator_revisions: dict[str, object] | None = None,
) -> int:
    arguments = _parser().parse_args(argv)
    if not arguments.database.is_file():
        print(
            f"experiment database does not exist: {arguments.database}",
            file=sys.stderr,
        )
        return 2
    try:
        service = MetricEvaluationService(
            arguments.database,
            registry=registry or build_registry(),
            evaluator_revisions=evaluator_revisions,
        )
        if arguments.command == "evaluate-run":
            result, saved = service.evaluate_run(
                arguments.run_id,
                metric_set_id=arguments.metric_set,
                version=arguments.version,
                recompute=arguments.recompute,
            )
            document = {"saved": saved, "evaluation": result}
        elif arguments.command == "evaluate-experiment":
            outcome = service.evaluate_experiment(
                metric_set_id=arguments.metric_set,
                version=arguments.version,
                recompute=arguments.recompute,
            )
            document = {
                "metric_set_id": outcome.metric_set_id,
                "metric_set_version": outcome.metric_set_version,
                "run_count": outcome.run_count,
                "evaluated_count": outcome.evaluated_count,
                "skipped_count": outcome.skipped_count,
                "invalid_count": outcome.invalid_count,
                "aggregate_count": outcome.aggregate_count,
            }
        else:
            document = {
                "aggregate_count": service.aggregate_experiment(
                    metric_set_id=arguments.metric_set,
                    version=arguments.version,
                )
            }
        print(json.dumps(document, ensure_ascii=False, indent=2))
        return 0
    except MetricError as exc:
        print(str(exc), file=sys.stderr)
        return 2
