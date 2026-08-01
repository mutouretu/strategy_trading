from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from experiment_system import (
    CodeRevision,
    RetentionClass,
    SQLiteExperimentRepository,
    TraceState,
)
from experiment_system.cli import main

from experiment_test_support import (
    executable_registry,
    experiment_document,
    single_experiment_document,
)


class ExperimentCliTests(unittest.TestCase):
    def test_validate_plan_and_single_run_commands_are_injectable(
        self,
    ) -> None:
        registry, provider = executable_registry()
        revisions = {
            "market_simulator": CodeRevision(commit="a" * 40),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_path = root / "experiment.json"
            spec_path.write_text(
                json.dumps(single_experiment_document()),
                encoding="utf-8",
            )

            validate_output = io.StringIO()
            with redirect_stdout(validate_output):
                validate_code = main(
                    ["validate", str(spec_path)],
                    registry=registry,
                )
            self.assertEqual(validate_code, 0)
            self.assertEqual(
                json.loads(validate_output.getvalue())["run_count"],
                1,
            )

            plan_output = io.StringIO()
            with redirect_stdout(plan_output):
                plan_code = main(
                    ["plan", str(spec_path)],
                    registry=registry,
                    code_revisions=revisions,
                )
            self.assertEqual(plan_code, 0)
            self.assertEqual(
                json.loads(plan_output.getvalue())["run_count"],
                1,
            )

            database = root / "results" / "probe.sqlite3"
            market_root = root / "market_data"
            automatic_viewer = root / "exports" / "automatic.json"
            run_output = io.StringIO()
            with redirect_stdout(run_output):
                run_code = main(
                    [
                        "run",
                        str(spec_path),
                        "--database",
                        str(database),
                        "--market-root",
                        str(market_root),
                        "--export-viewer",
                        str(automatic_viewer),
                    ],
                    registry=registry,
                    code_revisions=revisions,
                )
            run_document = json.loads(run_output.getvalue())
            self.assertEqual(run_code, 0)
            self.assertEqual(run_document["status"], "SUCCEEDED")
            self.assertTrue(database.exists())
            self.assertTrue(automatic_viewer.is_file())
            self.assertEqual(
                run_document["viewer_export"],
                str(automatic_viewer.resolve()),
            )
            self.assertEqual(len(list(market_root.glob("*.parquet"))), 1)
            self.assertEqual(provider.prepare_calls, 1)
            self.assertEqual(
                SQLiteExperimentRepository(database)
                .get_run_record(run_document["run_id"])
                .status.value,
                "SUCCEEDED",
            )

    def test_run_viewer_export_rejects_a_batch_before_execution(
        self,
    ) -> None:
        registry, provider = executable_registry()
        revisions = {
            "market_simulator": CodeRevision(commit="a" * 40),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_path = root / "batch.json"
            spec_path.write_text(
                json.dumps(experiment_document()),
                encoding="utf-8",
            )
            errors = io.StringIO()
            with redirect_stderr(errors):
                code = main(
                    [
                        "run",
                        str(spec_path),
                        "--database",
                        str(root / "batch.sqlite3"),
                        "--market-root",
                        str(root / "market_data"),
                        "--export-viewer",
                        str(root / "viewer.json"),
                    ],
                    registry=registry,
                    code_revisions=revisions,
                )
            self.assertEqual(code, 2)
            self.assertIn("exactly one Run", errors.getvalue())
            self.assertEqual(provider.prepare_calls, 0)
            self.assertFalse((root / "batch.sqlite3").exists())

    def test_cli_reports_configuration_errors_without_traceback(self) -> None:
        registry, _ = executable_registry()
        errors = io.StringIO()
        with redirect_stderr(errors):
            code = main(
                ["plan", "missing.json"],
                registry=registry,
                code_revisions={
                    "market_simulator": CodeRevision(commit="a" * 40),
                },
            )
        self.assertEqual(code, 2)
        self.assertIn("cannot read experiment spec", errors.getvalue())

    def test_run_command_reports_batch_summary(self) -> None:
        registry, provider = executable_registry()
        revisions = {
            "market_simulator": CodeRevision(commit="a" * 40),
        }
        document = experiment_document()
        document["scenario_groups"] = [
            document["scenario_groups"][0]
        ]
        document["scenario_groups"][0]["markets"] = [
            document["scenario_groups"][0]["markets"][0]
        ]
        document["scenario_groups"][0]["parameter_axes"] = []

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_path = root / "batch.json"
            spec_path.write_text(
                json.dumps(document),
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "run",
                        str(spec_path),
                        "--database",
                        str(root / "batch.sqlite3"),
                        "--market-root",
                        str(root / "market_data"),
                    ],
                    registry=registry,
                    code_revisions=revisions,
                )

            result = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "SUCCEEDED")
            self.assertEqual(result["run_count"], 2)
            self.assertEqual(result["succeeded_count"], 2)
            self.assertEqual(len(result["runs"]), 2)
            self.assertEqual(provider.prepare_calls, 2)

    def test_failed_batch_can_be_retried_from_cli(self) -> None:
        failing_registry, failing_provider = executable_registry(
            fail_on_seeds={42}
        )
        revisions = {
            "market_simulator": CodeRevision(commit="a" * 40),
        }
        document = single_experiment_document()
        document["experiment_id"] = "cli-retry"
        document["seeds"] = [42, 43]
        document["controls"]["max_runs"] = 2

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_path = root / "retry.json"
            database = root / "retry.sqlite3"
            market_root = root / "market_data"
            spec_path.write_text(json.dumps(document), encoding="utf-8")
            first_output = io.StringIO()
            with redirect_stdout(first_output):
                first_code = main(
                    [
                        "run",
                        str(spec_path),
                        "--database",
                        str(database),
                        "--market-root",
                        str(market_root),
                    ],
                    registry=failing_registry,
                    code_revisions=revisions,
                )
            first = json.loads(first_output.getvalue())
            self.assertEqual(first_code, 1)
            self.assertEqual(first["status"], "FAILED")
            self.assertEqual(first["succeeded_count"], 1)
            self.assertEqual(first["failed_count"], 1)
            self.assertEqual(failing_provider.prepare_calls, 2)

            recovery_registry, recovery_provider = executable_registry()
            retry_output = io.StringIO()
            with redirect_stdout(retry_output):
                retry_code = main(
                    [
                        "run",
                        str(spec_path),
                        "--database",
                        str(database),
                        "--market-root",
                        str(market_root),
                        "--rerun-failed",
                    ],
                    registry=recovery_registry,
                    code_revisions=revisions,
                )
            retried = json.loads(retry_output.getvalue())
            self.assertEqual(retry_code, 0)
            self.assertEqual(retried["status"], "SUCCEEDED")
            self.assertEqual(retried["executed_count"], 1)
            self.assertEqual(retried["skipped_count"], 1)
            self.assertEqual(retried["retried_count"], 1)
            self.assertEqual(recovery_provider.prepare_calls, 1)

    def test_archive_and_purge_commands_require_explicit_confirm(
        self,
    ) -> None:
        registry, _ = executable_registry()
        revisions = {
            "market_simulator": CodeRevision(commit="a" * 40),
        }
        document = single_experiment_document()
        document["experiment_id"] = "cli-retention"
        document["seeds"] = [42, 43]
        document["controls"]["max_runs"] = 2

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_path = root / "retention.json"
            database = root / "retention.sqlite3"
            spec_path.write_text(json.dumps(document), encoding="utf-8")
            run_output = io.StringIO()
            with redirect_stdout(run_output):
                self.assertEqual(
                    main(
                        [
                            "run",
                            str(spec_path),
                            "--database",
                            str(database),
                            "--market-root",
                            str(root / "market_data"),
                        ],
                        registry=registry,
                        code_revisions=revisions,
                    ),
                    0,
                )
            run_ids = [
                item["run_id"]
                for item in json.loads(
                    run_output.getvalue()
                )["runs"]
            ]

            archive_output = io.StringIO()
            with redirect_stdout(archive_output):
                archive_code = main(
                    [
                        "archive-run",
                        str(database),
                        "--run-id",
                        run_ids[0],
                        "--reason",
                        "keep baseline",
                    ]
                )
            archived = json.loads(archive_output.getvalue())
            self.assertEqual(archive_code, 0)
            self.assertEqual(
                archived["retention_class"],
                "ARCHIVED",
            )

            preview_output = io.StringIO()
            with redirect_stdout(preview_output):
                preview_code = main(
                    ["purge-traces", str(database)]
                )
            preview = json.loads(preview_output.getvalue())
            self.assertEqual(preview_code, 0)
            self.assertEqual(preview["mode"], "PREVIEW")
            self.assertEqual(preview["run_ids"], [run_ids[1]])

            repository = SQLiteExperimentRepository(database)
            self.assertEqual(
                repository.get_run_record(
                    run_ids[1]
                ).trace_state,
                TraceState.STORED,
            )
            purge_output = io.StringIO()
            with redirect_stdout(purge_output):
                purge_code = main(
                    [
                        "purge-traces",
                        str(database),
                        "--confirm",
                    ]
                )
            self.assertEqual(purge_code, 0)
            self.assertEqual(
                json.loads(purge_output.getvalue())["mode"],
                "PURGED",
            )
            self.assertEqual(
                repository.get_run_record(
                    run_ids[0]
                ).retention_class,
                RetentionClass.ARCHIVED,
            )
            self.assertEqual(
                repository.get_run_record(
                    run_ids[1]
                ).trace_state,
                TraceState.PURGED,
            )

    def test_compare_and_export_run_commands_write_only_on_request(
        self,
    ) -> None:
        registry, _ = executable_registry()
        revisions = {
            "market_simulator": CodeRevision(commit="a" * 40),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec_path = root / "experiment.json"
            database = root / "results" / "probe.sqlite3"
            spec_path.write_text(
                json.dumps(single_experiment_document()),
                encoding="utf-8",
            )
            run_output = io.StringIO()
            with redirect_stdout(run_output):
                self.assertEqual(
                    main(
                        [
                            "run",
                            str(spec_path),
                            "--database",
                            str(database),
                            "--market-root",
                            str(root / "market_data"),
                        ],
                        registry=registry,
                        code_revisions=revisions,
                    ),
                    0,
                )
            run_id = json.loads(run_output.getvalue())["run_id"]

            csv_path = root / "exports" / "comparison.csv"
            csv_output = io.StringIO()
            with redirect_stdout(csv_output):
                self.assertEqual(
                    main(
                        [
                            "compare",
                            str(database),
                            "--output",
                            str(csv_path),
                        ]
                    ),
                    0,
                )
            self.assertTrue(csv_path.is_file())
            self.assertEqual(
                json.loads(csv_output.getvalue())["output"],
                str(csv_path.resolve()),
            )

            viewer_path = root / "exports" / "run.json"
            viewer_output = io.StringIO()
            with redirect_stdout(viewer_output):
                self.assertEqual(
                    main(
                        [
                            "export-run",
                            str(database),
                            "--run-id",
                            run_id,
                            "--output",
                            str(viewer_path),
                        ]
                    ),
                    0,
                )
            self.assertTrue(viewer_path.is_file())
            self.assertEqual(
                json.loads(viewer_path.read_text(encoding="utf-8"))[
                    "schema_version"
                ],
                2,
            )


if __name__ == "__main__":
    unittest.main()
