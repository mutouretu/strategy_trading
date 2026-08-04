from __future__ import annotations

import sqlite3
import tempfile
import unittest
import inspect
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import strategy_optimization  # noqa: F401 - activates local checkout imports

from experiment_system import (
    CodeRevision,
    ExperimentMetricStore,
    ExperimentManifest,
    ParquetMarketStore,
    SQLiteExperimentRepository,
    execute_experiment,
)
from metric_system import MetricEvaluationService

from strategy_optimization import (
    DatasetRole,
    DatasetStatus,
    SQLiteStudyRepository,
    StudyStatus,
    StudyRepositoryConflictError,
    build_baseline_report,
    load_dataset_split,
    load_study_bundle,
    plan_study,
    validate_study,
)
from strategy_optimization.compiler import compile_study
from strategy_simulation.experiment_provider import build_provider_registry
from strategy_simulation.metrics.registry import build_metric_registry


PROJECT_ROOT = Path(__file__).parents[1]
STUDY_PATH = (
    PROJECT_ROOT
    / "research"
    / "scenario_studies"
    / "coinm_btc_baseline_scaffold_v1.json"
)
HISTORICAL_SPLIT_PATH = (
    PROJECT_ROOT
    / "research"
    / "protocols"
    / "btc_coinm_historical_split_v1.json"
)
FORMAL_STUDY_PATH = (
    PROJECT_ROOT
    / "research"
    / "scenario_studies"
    / "coinm_btc_formal_baseline_v1.json"
)
FORMAL_MARKET_PATHS = (
    PROJECT_ROOT / "experiments" / "market_data" / "dd600d70d192eed7e7b2.parquet",
    PROJECT_ROOT / "experiments" / "market_data" / "f03afa63d9c3bc9d400e.parquet",
)
REVISIONS = {
    "market_simulator": CodeRevision(commit="market-sim-6a"),
    "grid_trading": CodeRevision(commit="grid-trading-6a"),
    "strategies_system": CodeRevision(commit="strategies-system-6a"),
}


def build_plan():
    return plan_study(
        load_study_bundle(STUDY_PATH),
        provider_registry=build_provider_registry(),
        metric_registry=build_metric_registry(),
        code_revisions=REVISIONS,
    )


class StudySchemaAndPlanningTests(unittest.TestCase):
    def test_6a_scaffold_is_eight_runs_and_excludes_holdout(self) -> None:
        bundle = load_study_bundle(STUDY_PATH)
        report = validate_study(
            bundle,
            provider_registry=build_provider_registry(),
            metric_registry=build_metric_registry(),
        )

        self.assertEqual(report.candidate_count, 8)
        self.assertEqual(report.run_count, 8)
        self.assertFalse(report.formal_ready)
        self.assertEqual(report.dataset_status, DatasetStatus.DEVELOPMENT.value)
        market_keys = {
            component.key
            for group in bundle.experiment.scenario_groups
            for component in group.markets
        }
        holdout = bundle.dataset_split.window(DatasetRole.HOLDOUT)
        self.assertNotIn(holdout.market_key, market_keys)

    def test_same_study_produces_stable_plan_and_fingerprints(self) -> None:
        first = build_plan()
        second = build_plan()

        self.assertEqual(
            first.compiled.study_fingerprint,
            second.compiled.study_fingerprint,
        )
        self.assertEqual(
            first.compiled.protocol_fingerprint,
            second.compiled.protocol_fingerprint,
        )
        self.assertEqual(
            [run.run_id for run in first.experiment_plan.runs],
            [run.run_id for run in second.experiment_plan.runs],
        )
        metadata = first.compiled.experiment.metadata["strategy_study"]
        self.assertEqual(
            metadata["study_fingerprint"],
            first.compiled.study_fingerprint,
        )
        self.assertEqual(
            metadata["metric_definition_bindings"][0]["metric_set_id"],
            "core",
        )
        self.assertEqual(
            len(metadata["metric_definition_bindings"][0]["definition_hash"]),
            64,
        )

    def test_coinm_objective_is_btc_equity_relative_to_hodl(self) -> None:
        profile = load_study_bundle(STUDY_PATH).objective_profile

        self.assertEqual(profile.valuation_asset, "BTC")
        self.assertEqual(profile.baseline_strategy_type, "hold-btc/v1")
        primary = profile.objectives[0]
        self.assertEqual(primary.selector.metric_key, "return.final_equity")
        self.assertEqual(primary.selector.unit, "BTC")
        self.assertEqual(
            dict(primary.selector.dimensions),
            {
                "scope": "account.futures_equity",
                "valuation_asset": "BTC",
            },
        )
        self.assertEqual(primary.comparison.value, "DELTA_FROM_BASELINE")

    def test_historical_windows_are_content_locked(self) -> None:
        split = load_dataset_split(HISTORICAL_SPLIT_PATH)

        self.assertEqual(split.status, DatasetStatus.CONTENT_LOCKED)
        self.assertTrue(split.formal_ready)
        self.assertEqual(split.instrument, "BTCUSD_PERP")
        self.assertEqual(split.interval, "1m")
        self.assertEqual(
            split.window(DatasetRole.HOLDOUT).start.isoformat(),
            "2026-06-26",
        )
        self.assertEqual(
            split.window(DatasetRole.HOLDOUT).end_exclusive.isoformat(),
            "2026-07-20",
        )
        self.assertTrue(
            all(window.content_sha256 is not None for window in split.windows)
        )

    @unittest.skipUnless(
        all(path.is_file() for path in FORMAL_MARKET_PATHS),
        "materialize the ignored 6B historical Parquet dataset first",
    )
    def test_6b_formal_study_is_eight_runs_and_excludes_holdout(self) -> None:
        bundle = load_study_bundle(FORMAL_STUDY_PATH)
        report = validate_study(
            bundle,
            provider_registry=build_provider_registry(),
            metric_registry=build_metric_registry(),
        )

        self.assertEqual(report.run_count, 8)
        self.assertTrue(report.formal_ready)
        self.assertEqual(report.dataset_status, "CONTENT_LOCKED")
        market_keys = {
            component.key
            for group in bundle.experiment.scenario_groups
            for component in group.markets
        }
        self.assertEqual(
            market_keys,
            {"btc-coinm-train", "btc-coinm-validation"},
        )
        self.assertNotIn(
            bundle.dataset_split.window(DatasetRole.HOLDOUT).market_key,
            market_keys,
        )

    def test_formal_study_rejects_market_content_hash_mismatch(self) -> None:
        bundle = load_study_bundle(FORMAL_STUDY_PATH)
        group = bundle.experiment.scenario_groups[0]
        market = group.markets[0]
        bad_market = replace(
            market,
            parameters={**dict(market.parameters), "content_sha256": "0" * 64},
        )
        bad_group = replace(
            group,
            markets=(bad_market, *group.markets[1:]),
        )
        bad_bundle = replace(
            bundle,
            experiment=replace(
                bundle.experiment,
                scenario_groups=(bad_group,),
            ),
        )

        with self.assertRaisesRegex(
            ValueError,
            "does not match its CONTENT_LOCKED dataset window",
        ):
            compile_study(
                bad_bundle,
                metric_registry=build_metric_registry(),
            )

    def test_optimization_does_not_enter_strategy_or_execution_core(self) -> None:
        strategy_root = PROJECT_ROOT / "src" / "trading_strategies"
        strategy_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in strategy_root.rglob("*.py")
        )
        self.assertNotIn("strategy_optimization", strategy_source)
        compiler_source = inspect.getsource(compile_study)
        self.assertNotIn("TradeInstruction", compiler_source)
        self.assertNotIn("SimulationLedger", compiler_source)


class StudyRepositoryTests(unittest.TestCase):
    def test_study_uses_namespaced_tables_in_experiment_database(self) -> None:
        plan = build_plan()
        created_at = datetime(2026, 8, 2, tzinfo=timezone.utc)
        manifest = ExperimentManifest(
            experiment=plan.compiled.experiment,
            code_revisions=REVISIONS,
            created_at=created_at,
            planned_run_count=plan.run_count,
        )
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "study.sqlite3"
            SQLiteExperimentRepository(database).create_experiment(
                plan.experiment_plan,
                manifest,
            )
            studies = SQLiteStudyRepository(database)

            self.assertTrue(
                studies.create_or_validate(plan, created_at=created_at)
            )
            self.assertFalse(
                studies.create_or_validate(plan, created_at=created_at)
            )
            stored = studies.get(plan.compiled.bundle.study.study_id)
            self.assertEqual(stored.status, StudyStatus.PLANNED)
            self.assertFalse(stored.formal_ready)

            stored = studies.transition(
                stored.study_id,
                StudyStatus.RUNNING,
                changed_at=created_at,
            )
            self.assertEqual(stored.status, StudyStatus.RUNNING)
            with self.assertRaises(StudyRepositoryConflictError):
                studies.transition(
                    stored.study_id,
                    StudyStatus.SELECTED,
                    changed_at=created_at,
                )

            with sqlite3.connect(database) as connection:
                experiment_count = connection.execute(
                    "SELECT COUNT(*) FROM experiments"
                ).fetchone()[0]
                study_count = connection.execute(
                    "SELECT COUNT(*) FROM optimization_studies"
                ).fetchone()[0]
                event_count = connection.execute(
                    "SELECT COUNT(*) FROM optimization_study_events"
                ).fetchone()[0]
            self.assertEqual(experiment_count, 1)
            self.assertEqual(study_count, 1)
            self.assertEqual(event_count, 2)


class StudyExecutionIntegrationTests(unittest.TestCase):
    def test_compiled_study_executes_through_existing_experiment_system(self) -> None:
        plan = build_plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "study.sqlite3"
            outcome = execute_experiment(
                plan.experiment_plan,
                registry=build_provider_registry(),
                repository=SQLiteExperimentRepository(database),
                market_store=ParquetMarketStore(root / "market_data"),
            )

            self.assertEqual(outcome.run_count, 8)
            self.assertEqual(outcome.succeeded_count, 8)
            metrics = MetricEvaluationService(
                database,
                registry=build_metric_registry(),
                evaluator_revisions={"strategies_system": "test-6a"},
            )
            evaluated = metrics.evaluate_experiment(
                metric_set_id="core",
                version="v1",
            )
            self.assertEqual(evaluated.invalid_count, 0)
            hold_runs = [
                run
                for run in plan.experiment_plan.runs
                if run.configuration.strategy.type == "hold-btc/v1"
            ]
            self.assertEqual(len(hold_runs), 2)
            stored_metrics = ExperimentMetricStore(database)
            for run in hold_runs:
                evaluation = stored_metrics.run_evaluation(
                    run.run_id,
                    "core",
                    "v1",
                )
                assert evaluation is not None
                final_btc = next(
                    item
                    for item in evaluation["values"]
                    if item["metric_key"] == "return.final_equity"
                    and item["dimensions"]
                    == {
                        "scope": "account.futures_equity",
                        "valuation_asset": "BTC",
                    }
                )
                self.assertEqual(final_btc["value"], "1.1")
            studies = SQLiteStudyRepository(database)
            now = datetime(2026, 8, 2, tzinfo=timezone.utc)
            studies.create_or_validate(plan, created_at=now)
            studies.transition(
                plan.compiled.bundle.study.study_id,
                StudyStatus.RUNNING,
                changed_at=now,
            )
            stored = studies.transition(
                plan.compiled.bundle.study.study_id,
                StudyStatus.EXECUTED,
                changed_at=now,
            )
            self.assertEqual(stored.status, StudyStatus.EXECUTED)
            report = build_baseline_report(database, plan.compiled.bundle)
            self.assertEqual(len(report["rows"]), 8)
            hold_rows = [
                row
                for row in report["rows"]
                if row["strategy_type"] == "hold-btc/v1"
            ]
            self.assertTrue(
                all(row["excess_btc_vs_hodl"] in {"0", "0.0"} for row in hold_rows)
            )
            self.assertTrue(
                studies.save_baseline_report(
                    stored.study_id,
                    report,
                    created_at=now,
                )
            )
            self.assertFalse(
                studies.save_baseline_report(
                    stored.study_id,
                    report,
                    created_at=now,
                )
            )
            stored = studies.transition(
                stored.study_id,
                StudyStatus.EVALUATED,
                changed_at=now,
            )
            self.assertEqual(stored.status, StudyStatus.EVALUATED)


if __name__ == "__main__":
    unittest.main()
