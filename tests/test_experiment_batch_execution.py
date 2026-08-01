from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from experiment_system import (
    CodeRevision,
    ExperimentManifest,
    ExperimentRepositoryConflictError,
    ExperimentStatus,
    ExperimentValidationError,
    MarketReference,
    ParquetMarketStore,
    RunStatus,
    SQLiteExperimentRepository,
    SingleRunExecutionError,
    execute_experiment,
    parse_experiment_spec,
    plan_experiment,
)

from experiment_test_support import (
    executable_registry,
    experiment_document,
)


class ExperimentBatchExecutionTests(unittest.TestCase):
    @staticmethod
    def _plan(registry, document=None):
        return plan_experiment(
            parse_experiment_spec(document or experiment_document()),
            registry,
            code_revisions={
                "market_simulator": CodeRevision(commit="a" * 40),
            },
        )

    def test_all_runs_execute_in_stable_plan_order(self) -> None:
        registry, provider = executable_registry()
        plan = self._plan(registry)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "batch.sqlite3"
            repository = SQLiteExperimentRepository(database)
            outcome = execute_experiment(
                plan,
                registry=registry,
                repository=repository,
                market_store=ParquetMarketStore(root / "market_data"),
            )

            self.assertEqual(outcome.experiment_id, "grid-research")
            self.assertEqual(outcome.run_count, 10)
            self.assertEqual(outcome.succeeded_count, 10)
            self.assertEqual(provider.prepare_calls, 10)
            self.assertEqual(
                [run.record.run_id for run in outcome.runs],
                [run.run_id for run in plan.runs],
            )
            self.assertTrue(
                all(
                    run.record.status is RunStatus.SUCCEEDED
                    for run in outcome.runs
                )
            )
            self.assertEqual(
                len(
                    {
                        run.market_reference.market_path_id
                        for run in outcome.runs
                    }
                ),
                1,
            )
            self.assertEqual(
                len(list((root / "market_data").glob("*.parquet"))),
                1,
            )

            with sqlite3.connect(database) as connection:
                experiment_status = connection.execute(
                    "SELECT status FROM experiments"
                ).fetchone()[0]
                run_statuses = connection.execute(
                    """
                    SELECT status, COUNT(*)
                    FROM runs
                    GROUP BY status
                    """
                ).fetchall()
            self.assertEqual(experiment_status, "SUCCEEDED")
            self.assertEqual(run_statuses, [("SUCCEEDED", 10)])

    def test_failure_stops_and_leaves_later_runs_planned(self) -> None:
        document = experiment_document()
        document["controls"]["continue_on_error"] = False
        registry, provider = executable_registry(fail_on_execute=True)
        plan = self._plan(registry, document)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "failed-batch.sqlite3"
            repository = SQLiteExperimentRepository(database)

            with self.assertRaisesRegex(
                SingleRunExecutionError,
                "deterministic provider failure",
            ):
                execute_experiment(
                    plan,
                    registry=registry,
                    repository=repository,
                    market_store=ParquetMarketStore(
                        root / "market_data"
                    ),
                )

            self.assertEqual(provider.prepare_calls, 1)
            with sqlite3.connect(database) as connection:
                experiment_status = connection.execute(
                    "SELECT status FROM experiments"
                ).fetchone()[0]
                ordered_statuses = [
                    row[0]
                    for row in connection.execute(
                        "SELECT status FROM runs ORDER BY rowid"
                    )
                ]
            self.assertEqual(experiment_status, "FAILED")
            self.assertEqual(
                ordered_statuses,
                ["FAILED", *(["PLANNED"] * 9)],
            )

    def test_continue_on_error_isolates_failures_and_finishes_batch(
        self,
    ) -> None:
        registry, provider = executable_registry(
            fail_on_seeds={42}
        )
        plan = self._plan(registry)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outcome = execute_experiment(
                plan,
                registry=registry,
                repository=SQLiteExperimentRepository(
                    root / "continued.sqlite3"
                ),
                market_store=ParquetMarketStore(root / "market_data"),
            )

            self.assertEqual(provider.prepare_calls, 10)
            self.assertEqual(outcome.executed_count, 10)
            self.assertEqual(outcome.succeeded_count, 5)
            self.assertEqual(outcome.failed_count, 5)
            self.assertEqual(outcome.planned_count, 0)
            self.assertEqual(outcome.status, ExperimentStatus.FAILED)
            self.assertEqual(
                [record.status for record in outcome.records],
                [
                    RunStatus.FAILED,
                    RunStatus.SUCCEEDED,
                ]
                * 5,
            )

    def test_resume_skips_success_and_explicitly_retries_failure(
        self,
    ) -> None:
        failing_registry, failing_provider = executable_registry(
            fail_on_seeds={42}
        )
        plan = self._plan(failing_registry)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = SQLiteExperimentRepository(
                root / "resume.sqlite3"
            )
            market_store = ParquetMarketStore(root / "market_data")
            first = execute_experiment(
                plan,
                registry=failing_registry,
                repository=repository,
                market_store=market_store,
            )
            self.assertEqual(first.failed_count, 5)
            self.assertEqual(failing_provider.prepare_calls, 10)

            recovery_registry, recovery_provider = executable_registry()
            recovery_plan = self._plan(recovery_registry)
            skipped = execute_experiment(
                recovery_plan,
                registry=recovery_registry,
                repository=repository,
                market_store=market_store,
            )
            self.assertEqual(skipped.executed_count, 0)
            self.assertEqual(skipped.skipped_count, 10)
            self.assertEqual(skipped.failed_count, 5)
            self.assertEqual(recovery_provider.prepare_calls, 0)

            recovered = execute_experiment(
                recovery_plan,
                registry=recovery_registry,
                repository=repository,
                market_store=market_store,
                rerun_failed=True,
            )
            self.assertEqual(recovered.executed_count, 5)
            self.assertEqual(recovered.skipped_count, 5)
            self.assertEqual(len(recovered.retried_run_ids), 5)
            self.assertEqual(recovered.succeeded_count, 10)
            self.assertEqual(recovered.failed_count, 0)
            self.assertEqual(
                recovered.status,
                ExperimentStatus.SUCCEEDED,
            )
            self.assertEqual(recovery_provider.prepare_calls, 5)

    def test_interrupted_run_requires_explicit_recovery(self) -> None:
        registry, provider = executable_registry()
        plan = self._plan(registry)
        timestamp = datetime(2026, 7, 30, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = SQLiteExperimentRepository(
                root / "interrupted.sqlite3"
            )
            repository.create_experiment(
                plan,
                ExperimentManifest(
                    experiment=plan.experiment,
                    code_revisions=plan.code_revisions,
                    created_at=timestamp,
                    planned_run_count=plan.run_count,
                ),
            )
            repository.start_run(
                plan.runs[0],
                started_at=timestamp,
            )

            with self.assertRaisesRegex(
                ExperimentValidationError,
                "interrupted RUNNING Runs",
            ):
                execute_experiment(
                    plan,
                    registry=registry,
                    repository=repository,
                    market_store=ParquetMarketStore(
                        root / "market_data"
                    ),
                )
            self.assertEqual(provider.prepare_calls, 0)

            outcome = execute_experiment(
                plan,
                registry=registry,
                repository=repository,
                market_store=ParquetMarketStore(root / "market_data"),
                resume_interrupted=True,
            )
            self.assertEqual(
                outcome.recovered_run_ids,
                (plan.runs[0].run_id,),
            )
            self.assertEqual(outcome.succeeded_count, 10)
            self.assertEqual(provider.prepare_calls, 10)

    def test_existing_database_rejects_changed_experiment_spec(
        self,
    ) -> None:
        registry, _ = executable_registry()
        plan = self._plan(registry)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = SQLiteExperimentRepository(
                root / "immutable.sqlite3"
            )
            market_store = ParquetMarketStore(root / "market_data")
            execute_experiment(
                plan,
                registry=registry,
                repository=repository,
                market_store=market_store,
            )

            changed = experiment_document()
            changed["description"] = "changed after initial execution"
            changed_registry, changed_provider = executable_registry()
            changed_plan = self._plan(changed_registry, changed)
            with self.assertRaisesRegex(
                ExperimentRepositoryConflictError,
                "does not match",
            ):
                execute_experiment(
                    changed_plan,
                    registry=changed_registry,
                    repository=repository,
                    market_store=market_store,
                )
            self.assertEqual(changed_provider.prepare_calls, 0)

    def test_experiment_stays_running_after_an_intermediate_success(
        self,
    ) -> None:
        registry, _ = executable_registry()
        plan = self._plan(registry)
        timestamp = datetime(2026, 7, 30, tzinfo=timezone.utc)
        reference = MarketReference(
            market_path_id="1" * 20,
            content_hash="1" * 64,
            file_sha256="2" * 64,
            storage_path="/market-data/probe.parquet",
            schema_version="market-path/v1",
            frame_count=6,
            instrument="BTCUSD",
        )

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "intermediate.sqlite3"
            repository = SQLiteExperimentRepository(database)
            repository.create_experiment(
                plan,
                ExperimentManifest(
                    experiment=plan.experiment,
                    code_revisions=plan.code_revisions,
                    created_at=timestamp,
                    planned_run_count=plan.run_count,
                ),
            )
            first = plan.runs[0]
            repository.start_run(first, started_at=timestamp)
            repository.complete_run(
                first,
                summary={"run_id": first.run_id},
                trace={"fills": []},
                market_reference=reference,
                finished_at=timestamp,
                duration_seconds=0.0,
            )

            with sqlite3.connect(database) as connection:
                experiment_status = connection.execute(
                    "SELECT status FROM experiments"
                ).fetchone()[0]
            self.assertEqual(experiment_status, "RUNNING")


if __name__ == "__main__":
    unittest.main()
