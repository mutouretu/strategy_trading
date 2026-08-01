"""Injectable command-line skeleton for experiment-system hosts."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from .errors import ExperimentError, ExperimentValidationError
from .execution import execute_experiment
from .comparison import ExperimentReader
from .exports import export_comparison_csv, export_viewer_json
from .market_data import ParquetMarketStore
from .models import CodeRevision
from .provenance import collect_code_revisions
from .read_api import serve_results
from .registry import ProviderRegistry
from .schema import load_experiment_spec
from .service import (
    plan_experiment,
    plan_to_document,
    validate_experiment,
)
from .sqlite_repository import SQLiteExperimentRepository


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="experiment-system")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("validate", "plan", "run"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("spec", type=Path)
        if command in {"plan", "run"}:
            subparser.add_argument(
                "--repo",
                action="append",
                default=[],
                metavar="NAME=PATH",
                help="participating Git repository; repeat as needed",
            )
        if command == "run":
            subparser.add_argument("--database", type=Path)
            subparser.add_argument("--market-root", type=Path)
            subparser.add_argument(
                "--export-viewer",
                type=Path,
                help=(
                    "explicitly export the sole successful Run as Viewer "
                    "JSON after execution"
                ),
            )
            subparser.add_argument(
                "--allow-dirty",
                action="store_true",
                help=(
                    "allow an exploratory non-reproducible Run from "
                    "dirty repositories"
                ),
            )
            subparser.add_argument(
                "--rerun-failed",
                action="store_true",
                help="explicitly retry stored FAILED Runs",
            )
            subparser.add_argument(
                "--resume-interrupted",
                action="store_true",
                help="recover stale RUNNING Runs after interruption",
            )
    archive = subparsers.add_parser("archive-run")
    archive.add_argument("database", type=Path)
    archive.add_argument("--run-id", required=True)
    archive.add_argument("--reason")

    purge = subparsers.add_parser("purge-traces")
    purge.add_argument("database", type=Path)
    purge.add_argument(
        "--confirm",
        action="store_true",
        help="perform the displayed purge instead of previewing it",
    )

    compare = subparsers.add_parser("compare")
    compare.add_argument("database", type=Path)
    compare.add_argument("--output", type=Path, required=True)

    export = subparsers.add_parser("export-run")
    export.add_argument("database", type=Path)
    export.add_argument("--run-id", required=True)
    export.add_argument("--output", type=Path, required=True)

    serve = subparsers.add_parser("serve-results")
    serve.add_argument("result_root", type=Path)
    serve.add_argument("--viewer-root", type=Path)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8088)
    return parser


def _repository_paths(values: Sequence[str]) -> dict[str, Path]:
    repositories: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or not name or not raw_path:
            raise ExperimentValidationError(
                f"invalid --repo {value!r}; expected NAME=PATH"
            )
        if name in repositories:
            raise ExperimentValidationError(
                f"duplicate --repo name {name!r}"
            )
        repositories[name] = Path(raw_path)
    return repositories


def _revisions(
    arguments: argparse.Namespace,
    injected: Mapping[str, CodeRevision] | None,
) -> Mapping[str, CodeRevision]:
    if injected is not None:
        return injected
    repositories = _repository_paths(arguments.repo)
    if not repositories:
        raise ExperimentValidationError(
            "plan and run require at least one --repo NAME=PATH"
        )
    return collect_code_revisions(repositories)


def main(
    argv: Sequence[str] | None = None,
    *,
    registry: ProviderRegistry | None = None,
    code_revisions: Mapping[str, CodeRevision] | None = None,
) -> int:
    arguments = _parser().parse_args(argv)
    providers = registry or ProviderRegistry()
    try:
        if arguments.command == "archive-run":
            if not arguments.database.is_file():
                raise ExperimentValidationError(
                    f"experiment database does not exist: "
                    f"{arguments.database}"
                )
            repository = SQLiteExperimentRepository(arguments.database)
            record = repository.archive_run(
                arguments.run_id,
                archived_at=datetime.now(timezone.utc),
                reason=arguments.reason,
            )
            print(
                json.dumps(
                    {
                        "run_id": record.run_id,
                        "retention_class": record.retention_class.value,
                        "trace_state": record.trace_state.value,
                        "archived_at": record.archived_at.isoformat(),
                        "archive_reason": record.archive_reason,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if arguments.command == "purge-traces":
            if not arguments.database.is_file():
                raise ExperimentValidationError(
                    f"experiment database does not exist: "
                    f"{arguments.database}"
                )
            repository = SQLiteExperimentRepository(arguments.database)
            report = (
                repository.purge_standard_traces()
                if arguments.confirm
                else repository.preview_standard_trace_purge()
            )
            print(
                json.dumps(
                    {
                        "mode": (
                            "PURGED" if arguments.confirm else "PREVIEW"
                        ),
                        "run_count": report.run_count,
                        "payload_bytes": report.payload_bytes,
                        "run_ids": report.run_ids,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if arguments.command == "compare":
            if not arguments.database.is_file():
                raise ExperimentValidationError(
                    f"experiment database does not exist: "
                    f"{arguments.database}"
                )
            path = export_comparison_csv(
                ExperimentReader(arguments.database),
                arguments.output,
            )
            print(
                json.dumps(
                    {
                        "database": str(arguments.database.resolve()),
                        "output": str(path),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if arguments.command == "export-run":
            if not arguments.database.is_file():
                raise ExperimentValidationError(
                    f"experiment database does not exist: "
                    f"{arguments.database}"
                )
            path = export_viewer_json(
                ExperimentReader(arguments.database),
                arguments.run_id,
                arguments.output,
            )
            print(
                json.dumps(
                    {
                        "database": str(arguments.database.resolve()),
                        "run_id": arguments.run_id,
                        "output": str(path),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if arguments.command == "serve-results":
            if not arguments.result_root.is_dir():
                raise ExperimentValidationError(
                    f"result root does not exist: "
                    f"{arguments.result_root}"
                )
            if not 0 <= arguments.port <= 65535:
                raise ExperimentValidationError(
                    "serve-results port must be between 0 and 65535"
                )
            print(
                (
                    "Serving read-only experiment results at "
                    f"http://{arguments.host}:{arguments.port}/"
                ),
                flush=True,
            )
            serve_results(
                arguments.result_root,
                viewer_root=arguments.viewer_root,
                host=arguments.host,
                port=arguments.port,
            )
            return 0

        spec = load_experiment_spec(arguments.spec)
        if arguments.command == "validate":
            report = validate_experiment(spec, providers)
            print(
                json.dumps(
                    {
                        "experiment_id": report.experiment_id,
                        "scenario_count": report.scenario_count,
                        "run_count": report.run_count,
                        "provider_ids": report.provider_ids,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        revisions = _revisions(arguments, code_revisions)
        plan = plan_experiment(
            spec,
            providers,
            code_revisions=revisions,
        )
        if arguments.command == "plan":
            print(
                json.dumps(
                    plan_to_document(plan),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        if arguments.export_viewer is not None and plan.run_count != 1:
            raise ExperimentValidationError(
                "--export-viewer requires an experiment with exactly one Run"
            )
        dirty_repositories = sorted(
            name
            for name, revision in revisions.items()
            if revision.dirty
        )
        if dirty_repositories and not arguments.allow_dirty:
            raise ExperimentValidationError(
                "formal experiment execution requires clean repositories; "
                f"dirty: {dirty_repositories}. Use --allow-dirty only for "
                "an exploratory, non-reproducible Run"
            )
        spec_directory = arguments.spec.resolve().parent
        output_root = Path(spec.output.root)
        if not output_root.is_absolute():
            output_root = spec_directory / output_root
        database = arguments.database or (
            output_root / f"{spec.experiment_id}.sqlite3"
        )
        market_root = arguments.market_root or (
            spec_directory / "market_data"
        )
        outcome = execute_experiment(
            plan,
            registry=providers,
            repository=SQLiteExperimentRepository(database),
            market_store=ParquetMarketStore(market_root),
            allow_dirty=arguments.allow_dirty,
            rerun_failed=arguments.rerun_failed,
            resume_interrupted=arguments.resume_interrupted,
        )
        viewer_export: Path | None = None
        if (
            arguments.export_viewer is not None
            and outcome.failed_count == 0
        ):
            viewer_export = export_viewer_json(
                ExperimentReader(database),
                outcome.records[0].run_id,
                arguments.export_viewer,
            )
        if outcome.run_count == 1:
            run = outcome.runs[0]
            document = {
                "run_id": run.record.run_id,
                "status": run.record.status.value,
                "reproducible": run.record.reproducible,
                "database": str(database.resolve()),
                "market_path_id": (
                    run.market_reference.market_path_id
                ),
                "viewer_export": (
                    None
                    if viewer_export is None
                    else str(viewer_export)
                ),
            }
        else:
            document = {
                "experiment_id": outcome.experiment_id,
                "status": outcome.status.value,
                "run_count": outcome.run_count,
                "succeeded_count": outcome.succeeded_count,
                "failed_count": outcome.failed_count,
                "planned_count": outcome.planned_count,
                "executed_count": outcome.executed_count,
                "skipped_count": outcome.skipped_count,
                "recovered_count": len(
                    outcome.recovered_run_ids
                ),
                "retried_count": len(outcome.retried_run_ids),
                "reproducible": plan.reproducible,
                "database": str(database.resolve()),
                "runs": [
                    {
                        "run_id": record.run_id,
                        "status": record.status.value,
                        "market_path_id": record.market_path_id,
                    }
                    for record in outcome.records
                ],
            }
        print(
            json.dumps(
                document,
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if outcome.failed_count == 0 else 1
    except ExperimentError as exc:
        print(str(exc), file=sys.stderr)
        return 2
