from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import experiment_system.sqlite_repository as sqlite_repository_module
from experiment_system import (
    SQLITE_SCHEMA_VERSION,
    ExperimentManifest,
    ExperimentRepositoryError,
    ExperimentValidationError,
    MarketReference,
    RunStatus,
    SQLiteExperimentRepository,
    TraceState,
)

from experiment_test_support import single_run_plan


STARTED_AT = datetime(2026, 7, 30, 1, 0, tzinfo=timezone.utc)
FINISHED_AT = STARTED_AT + timedelta(seconds=2)


def _market_reference() -> MarketReference:
    return MarketReference(
        market_path_id="1" * 20,
        content_hash="1" * 64,
        file_sha256="2" * 64,
        storage_path="/market-data/11111111111111111111.parquet",
        schema_version="market-path/v1",
        frame_count=6,
        instrument="BTCUSD",
    )


class SQLiteExperimentRepositoryTests(unittest.TestCase):
    def _create_started_repository(
        self,
        directory: str,
    ):
        plan = single_run_plan()
        repository = SQLiteExperimentRepository(
            Path(directory) / "experiment.sqlite3"
        )
        manifest = ExperimentManifest(
            experiment=plan.experiment,
            code_revisions=plan.code_revisions,
            created_at=STARTED_AT,
            planned_run_count=plan.run_count,
        )
        repository.create_experiment(plan, manifest)
        repository.start_run(plan.runs[0], started_at=STARTED_AT)
        return plan, repository

    def test_success_round_trip_uses_compact_sqlite_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan, repository = self._create_started_repository(directory)
            run_spec = plan.runs[0]
            summary = {
                "schema_version": "run-summary/v1",
                "final_equity": "1006",
            }
            trace = {
                "schema_version": "simulation-trace/v1",
                "fills": [{"price": "99"}],
            }

            repository.complete_run(
                run_spec,
                summary=summary,
                trace=trace,
                market_reference=_market_reference(),
                finished_at=FINISHED_AT,
                duration_seconds=2.0,
            )

            record = repository.get_run_record(run_spec.run_id)
            self.assertEqual(record.status, RunStatus.SUCCEEDED)
            self.assertEqual(record.trace_state, TraceState.STORED)
            self.assertEqual(record.duration_seconds, 2.0)
            self.assertEqual(repository.get_summary(run_spec.run_id), summary)
            self.assertEqual(repository.load_trace(run_spec.run_id), trace)
            self.assertEqual(
                repository.get_manifest_document()["planned_run_count"],
                1,
            )

            with sqlite3.connect(repository.database_path) as connection:
                version = connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
                migration_versions = [
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT version
                        FROM schema_migrations
                        ORDER BY version
                        """
                    )
                ]
                tables = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE type = 'table'
                        """
                    )
                }
                payload = connection.execute(
                    """
                    SELECT compression, payload_blob, uncompressed_size
                    FROM run_payloads
                    """
                ).fetchone()
            self.assertEqual(version, SQLITE_SCHEMA_VERSION)
            self.assertEqual(
                migration_versions,
                list(range(1, SQLITE_SCHEMA_VERSION + 1)),
            )
            self.assertNotIn("market_frames", tables)
            self.assertIn("metric_sets", tables)
            self.assertIn("run_metric_values", tables)
            self.assertIn("aggregate_metric_values", tables)
            self.assertEqual(payload[0], "zlib")
            self.assertIsInstance(payload[1], bytes)
            self.assertGreater(payload[2], 0)
            self.assertNotEqual(payload[1][:1], b"{")

    def test_summary_query_does_not_read_or_decompress_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan, repository = self._create_started_repository(directory)
            run_spec = plan.runs[0]
            summary = {"final_equity": "1006"}
            repository.complete_run(
                run_spec,
                summary=summary,
                trace={"fills": [{"price": "99"}] * 20},
                market_reference=_market_reference(),
                finished_at=FINISHED_AT,
                duration_seconds=2.0,
            )
            with sqlite3.connect(repository.database_path) as connection:
                connection.execute(
                    """
                    UPDATE run_payloads
                    SET payload_blob = ?
                    WHERE run_id = ?
                    """,
                    (b"not-zlib", run_spec.run_id),
                )

            self.assertEqual(
                repository.get_summary(run_spec.run_id),
                summary,
            )
            with self.assertRaisesRegex(
                ExperimentValidationError,
                "decompression",
            ):
                repository.load_trace(run_spec.run_id)

    def test_success_write_rolls_back_as_one_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan, repository = self._create_started_repository(directory)
            run_spec = plan.runs[0]
            with sqlite3.connect(repository.database_path) as connection:
                connection.execute(
                    """
                    CREATE TRIGGER force_payload_failure
                    BEFORE INSERT ON run_payloads
                    BEGIN
                        SELECT RAISE(ABORT, 'forced payload failure');
                    END
                    """
                )

            with self.assertRaises(sqlite3.IntegrityError):
                repository.complete_run(
                    run_spec,
                    summary={"final_equity": "1006"},
                    trace={"fills": []},
                    market_reference=_market_reference(),
                    finished_at=FINISHED_AT,
                    duration_seconds=2.0,
                )

            self.assertEqual(
                repository.get_run_record(run_spec.run_id).status,
                RunStatus.RUNNING,
            )
            with self.assertRaises(ExperimentRepositoryError):
                repository.get_summary(run_spec.run_id)
            with sqlite3.connect(repository.database_path) as connection:
                market_count = connection.execute(
                    "SELECT COUNT(*) FROM market_references"
                ).fetchone()[0]
                payload_count = connection.execute(
                    "SELECT COUNT(*) FROM run_payloads"
                ).fetchone()[0]
            self.assertEqual(market_count, 0)
            self.assertEqual(payload_count, 0)

    def test_failed_run_has_no_false_success_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan, repository = self._create_started_repository(directory)
            run_spec = plan.runs[0]

            repository.fail_run(
                run_spec,
                error={
                    "error_type": "RuntimeError",
                    "message": "probe failed",
                },
                finished_at=FINISHED_AT,
                duration_seconds=2.0,
            )

            record = repository.get_run_record(run_spec.run_id)
            self.assertEqual(record.status, RunStatus.FAILED)
            self.assertIsNone(record.trace_state)
            self.assertEqual(record.error["message"], "probe failed")
            with self.assertRaises(ExperimentRepositoryError):
                repository.get_summary(run_spec.run_id)
            with self.assertRaises(ExperimentRepositoryError):
                repository.load_trace(run_spec.run_id)

    def test_version_one_database_migrates_archive_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "version-one.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.executescript(
                    sqlite_repository_module._SCHEMA_SQL
                )
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, applied_at)
                    VALUES (1, ?)
                    """,
                    (STARTED_AT.isoformat(),),
                )
                connection.execute("PRAGMA user_version = 1")

            SQLiteExperimentRepository(database)

            with sqlite3.connect(database) as connection:
                version = connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(runs)"
                    )
                }
                migrations = [
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT version
                        FROM schema_migrations
                        ORDER BY version
                        """
                    )
                ]
            self.assertEqual(version, SQLITE_SCHEMA_VERSION)
            self.assertIn("archived_at", columns)
            self.assertIn("archive_reason", columns)
            self.assertEqual(migrations, [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
