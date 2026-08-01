from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from experiment_system import (
    CodeRevision,
    ExperimentRepositoryConflictError,
    ExperimentRepositoryError,
    ParquetMarketStore,
    RetentionClass,
    SQLiteExperimentRepository,
    TraceState,
    execute_experiment,
    parse_experiment_spec,
    plan_experiment,
)

from experiment_test_support import (
    executable_registry,
    single_experiment_document,
)


class ExperimentTraceRetentionTests(unittest.TestCase):
    @staticmethod
    def _plan(registry):
        document = single_experiment_document()
        document["experiment_id"] = "trace-retention"
        document["seeds"] = [42, 43]
        document["controls"]["max_runs"] = 2
        return plan_experiment(
            parse_experiment_spec(document),
            registry,
            code_revisions={
                "market_simulator": CodeRevision(commit="a" * 40),
            },
        )

    def test_archive_protects_trace_and_standard_purge_is_atomic(
        self,
    ) -> None:
        registry, _ = executable_registry()
        plan = self._plan(registry)
        archived_at = datetime(2026, 7, 30, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = SQLiteExperimentRepository(
                root / "retention.sqlite3"
            )
            outcome = execute_experiment(
                plan,
                registry=registry,
                repository=repository,
                market_store=ParquetMarketStore(root / "market_data"),
            )
            archived_run = outcome.records[0]
            standard_run = outcome.records[1]

            archived = repository.archive_run(
                archived_run.run_id,
                archived_at=archived_at,
                reason="baseline worth keeping",
            )
            self.assertEqual(
                archived.retention_class,
                RetentionClass.ARCHIVED,
            )
            self.assertEqual(archived.trace_state, TraceState.STORED)
            self.assertEqual(archived.archived_at, archived_at)
            self.assertEqual(
                archived.archive_reason,
                "baseline worth keeping",
            )

            preview = repository.preview_standard_trace_purge()
            self.assertEqual(preview.run_ids, (standard_run.run_id,))
            self.assertGreater(preview.payload_bytes, 0)
            self.assertEqual(
                repository.get_run_record(
                    standard_run.run_id
                ).trace_state,
                TraceState.STORED,
            )

            purged = repository.purge_standard_traces()
            self.assertEqual(purged, preview)
            self.assertEqual(
                repository.get_run_record(
                    standard_run.run_id
                ).trace_state,
                TraceState.PURGED,
            )
            self.assertEqual(
                repository.get_run_record(
                    archived_run.run_id
                ).trace_state,
                TraceState.STORED,
            )
            self.assertTrue(
                repository.get_summary(standard_run.run_id)
            )
            self.assertTrue(repository.load_trace(archived_run.run_id))
            with self.assertRaisesRegex(
                ExperimentRepositoryError,
                "not found",
            ):
                repository.load_trace(standard_run.run_id)
            with self.assertRaises(
                ExperimentRepositoryConflictError
            ):
                repository.archive_run(
                    standard_run.run_id,
                    archived_at=archived_at,
                )
            self.assertEqual(
                repository.purge_standard_traces().run_count,
                0,
            )


if __name__ == "__main__":
    unittest.main()
