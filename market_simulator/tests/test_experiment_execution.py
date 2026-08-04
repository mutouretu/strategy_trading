from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from examples.deterministic_probe import run_probe
from experiment_system import (
    CodeRevision,
    ExperimentRepositoryError,
    ExperimentRepositoryConflictError,
    ExperimentValidationError,
    ParquetMarketStore,
    RunStatus,
    SQLiteExperimentRepository,
    SingleRunExecutionError,
    TraceState,
    execute_single_run,
)
from simulation_runtime import simulation_result_to_document

from experiment_test_support import (
    executable_registry,
    single_run_plan,
)


class SingleRunExecutionTests(unittest.TestCase):
    def test_deterministic_probe_runs_and_persists_end_to_end(self) -> None:
        registry, provider = executable_registry()
        plan = single_run_plan(registry=registry)
        base_time = datetime(2026, 7, 30, tzinfo=timezone.utc)
        times = iter(
            (
                base_time,
                base_time + timedelta(seconds=1),
                base_time + timedelta(seconds=3),
            )
        )
        ticks = iter((10.0, 12.5))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result_root = root / "results"
            market_root = root / "market_data"
            repository = SQLiteExperimentRepository(
                result_root / "single-probe.sqlite3"
            )

            outcome = execute_single_run(
                plan,
                registry=registry,
                repository=repository,
                market_store=ParquetMarketStore(market_root),
                clock=lambda: next(times),
                timer=lambda: next(ticks),
            )

            self.assertEqual(provider.prepare_calls, 1)
            self.assertEqual(outcome.record.status, RunStatus.SUCCEEDED)
            self.assertEqual(
                outcome.record.trace_state,
                TraceState.STORED,
            )
            self.assertEqual(outcome.record.duration_seconds, 2.5)
            self.assertEqual(
                [path.name for path in result_root.iterdir()],
                ["single-probe.sqlite3"],
            )
            self.assertEqual(
                [
                    path.resolve()
                    for path in market_root.glob("*.parquet")
                ],
                [Path(outcome.market_reference.storage_path)],
            )

            summary = repository.get_summary(outcome.record.run_id)
            self.assertTrue(summary["reproducible"])
            self.assertEqual(
                summary["result"]["final_equity"],
                "1006",
            )
            provider_summary = summary["provider_summary"][
                "test-simulation/v1"
            ]
            self.assertEqual(provider_summary["fill_count"], 3)
            self.assertEqual(provider_summary["final_equity"], "1006")

            trace = repository.load_trace(outcome.record.run_id)
            self.assertEqual(
                trace["market_path_id"],
                outcome.market_reference.market_path_id,
            )
            self.assertNotIn("market", trace)
            self.assertNotIn("summary", trace)
            self.assertEqual(len(trace["fills"]), 3)
            self.assertEqual(len(trace["equity"]), 6)
            expected = simulation_result_to_document(
                run_probe(),
                run_id=plan.runs[0].run_id,
                interval="1d",
                source=plan.runs[0].configuration.market.type,
                seed=plan.runs[0].seed,
                manifest={
                    "experiment_id": plan.runs[0].experiment_id,
                    "scenario_id": plan.runs[0].scenario.scenario_id,
                    "configuration_hash": (
                        plan.runs[0].configuration_hash
                    ),
                    "run_fingerprint": plan.runs[0].run_fingerprint,
                    "market_path_id": (
                        outcome.market_reference.market_path_id
                    ),
                },
            )
            expected.pop("market")
            expected.pop("summary")
            expected_schema = expected.pop("schema_version")
            self.assertEqual(
                trace,
                {
                    "schema_version": "simulation-trace/v1",
                    "viewer_schema_version": expected_schema,
                    "market_path_id": (
                        outcome.market_reference.market_path_id
                    ),
                    **expected,
                },
            )

    def test_provider_failure_is_recorded_without_summary_or_trace(
        self,
    ) -> None:
        registry, provider = executable_registry(
            fail_on_execute=True,
            failure_message=(
                "deterministic provider failure "
                "api_key=top-secret Authorization: Bearer abc123"
            ),
        )
        plan = single_run_plan(registry=registry)
        run_spec = plan.runs[0]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = SQLiteExperimentRepository(
                root / "failed.sqlite3"
            )

            with self.assertRaisesRegex(
                SingleRunExecutionError,
                "deterministic provider failure",
            ):
                execute_single_run(
                    plan,
                    registry=registry,
                    repository=repository,
                    market_store=ParquetMarketStore(
                        root / "market_data"
                    ),
                )

            self.assertEqual(provider.prepare_calls, 1)
            record = repository.get_run_record(run_spec.run_id)
            self.assertEqual(record.status, RunStatus.FAILED)
            self.assertIsNone(record.trace_state)
            self.assertEqual(record.error["error_type"], "RuntimeError")
            self.assertNotIn("top-secret", record.error["message"])
            self.assertNotIn("abc123", record.error["message"])
            self.assertIn("[REDACTED]", record.error["message"])
            self.assertFalse((root / "market_data").exists())
            with self.assertRaisesRegex(
                ExperimentRepositoryError,
                "not stored",
            ):
                repository.get_summary(run_spec.run_id)
            with self.assertRaisesRegex(
                ExperimentRepositoryError,
                "not found",
            ):
                repository.load_trace(run_spec.run_id)

    def test_dirty_code_requires_explicit_exploratory_opt_in(self) -> None:
        registry, _ = executable_registry()
        dirty_plan = single_run_plan(
            registry=registry,
            code_revisions={
                "market_simulator": CodeRevision(
                    commit="a" * 40,
                    dirty=True,
                    dirty_fingerprint="b" * 64,
                )
            },
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = SQLiteExperimentRepository(
                root / "exploratory.sqlite3"
            )
            with self.assertRaisesRegex(
                ExperimentValidationError,
                "requires clean repositories",
            ):
                execute_single_run(
                    dirty_plan,
                    registry=registry,
                    repository=repository,
                    market_store=ParquetMarketStore(
                        root / "market_data"
                    ),
                )

            outcome = execute_single_run(
                dirty_plan,
                registry=registry,
                repository=repository,
                market_store=ParquetMarketStore(root / "market_data"),
                allow_dirty=True,
            )
            self.assertFalse(outcome.record.reproducible)
            self.assertFalse(
                repository.get_manifest_document()["reproducible"]
            )
            self.assertFalse(outcome.summary["reproducible"])

            with self.assertRaisesRegex(
                ExperimentRepositoryConflictError,
                "only one Experiment",
            ):
                execute_single_run(
                    dirty_plan,
                    registry=registry,
                    repository=repository,
                    market_store=ParquetMarketStore(
                        root / "market_data"
                    ),
                    allow_dirty=True,
                )


if __name__ == "__main__":
    unittest.main()
