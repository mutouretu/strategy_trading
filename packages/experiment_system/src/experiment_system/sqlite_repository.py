"""SQLite implementation of the experiment result repository."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from .documents import (
    code_revisions_to_document,
    experiment_spec_to_document,
    manifest_to_document,
    run_spec_to_document,
)
from .errors import (
    ExperimentRepositoryConflictError,
    ExperimentRepositoryError,
    ExperimentRepositoryIntegrityError,
)
from .hashing import canonical_json
from .json_values import JsonValue
from .market_data import MarketReference
from .models import (
    CodeRevision,
    ExperimentManifest,
    ExperimentPlan,
    ExperimentStatus,
    RetentionClass,
    RunRecord,
    RunSpec,
    RunStatus,
    TracePurgeReport,
    TraceState,
)
from .payloads import EncodedPayload, decode_trace, encode_trace


SQLITE_SCHEMA_VERSION = 3


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
    experiment_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('PLANNED', 'RUNNING', 'SUCCEEDED', 'FAILED')
    ),
    spec_json TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    code_revisions_json TEXT NOT NULL,
    planned_run_count INTEGER NOT NULL CHECK (planned_run_count > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_references (
    market_path_id TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL UNIQUE,
    file_sha256 TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    frame_count INTEGER NOT NULL CHECK (frame_count > 0),
    instrument TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
    scenario_id TEXT NOT NULL,
    configuration_hash TEXT NOT NULL,
    run_fingerprint TEXT NOT NULL UNIQUE,
    seed INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('PLANNED', 'RUNNING', 'SUCCEEDED', 'FAILED')
    ),
    run_spec_json TEXT NOT NULL,
    summary_json TEXT,
    error_json TEXT,
    trace_state TEXT CHECK (
        trace_state IS NULL OR trace_state IN ('STORED', 'PURGED')
    ),
    retention_class TEXT NOT NULL CHECK (
        retention_class IN ('STANDARD', 'ARCHIVED')
    ),
    market_path_id TEXT REFERENCES market_references(market_path_id),
    started_at TEXT,
    finished_at TEXT,
    duration_seconds REAL CHECK (
        duration_seconds IS NULL OR duration_seconds >= 0
    )
);

CREATE INDEX IF NOT EXISTS idx_runs_experiment
ON runs(experiment_id);

CREATE INDEX IF NOT EXISTS idx_runs_scenario
ON runs(scenario_id);

CREATE INDEX IF NOT EXISTS idx_runs_status
ON runs(status);

CREATE TABLE IF NOT EXISTS run_payloads (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    payload_type TEXT NOT NULL,
    compression TEXT NOT NULL,
    payload_blob BLOB NOT NULL,
    uncompressed_size INTEGER NOT NULL CHECK (uncompressed_size >= 0),
    payload_sha256 TEXT NOT NULL,
    PRIMARY KEY (run_id, payload_type)
);
"""


_METRIC_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS metric_sets (
    metric_set_id TEXT NOT NULL,
    metric_set_version TEXT NOT NULL,
    definition_hash TEXT NOT NULL,
    definition_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (metric_set_id, metric_set_version)
);

CREATE TABLE IF NOT EXISTS run_metric_evaluations (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    metric_set_id TEXT NOT NULL,
    metric_set_version TEXT NOT NULL,
    definition_hash TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    input_level TEXT NOT NULL CHECK (
        input_level IN ('SUMMARY', 'TRACE', 'MARKET')
    ),
    recomputable INTEGER NOT NULL CHECK (recomputable IN (0, 1)),
    status TEXT NOT NULL CHECK (status IN ('SUCCEEDED', 'INVALID')),
    input_hashes_json TEXT NOT NULL,
    evaluator_revisions_json TEXT NOT NULL,
    issues_json TEXT NOT NULL,
    evaluated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, metric_set_id, metric_set_version),
    FOREIGN KEY (metric_set_id, metric_set_version)
        REFERENCES metric_sets(metric_set_id, metric_set_version)
);

CREATE TABLE IF NOT EXISTS run_metric_values (
    run_id TEXT NOT NULL,
    metric_set_id TEXT NOT NULL,
    metric_set_version TEXT NOT NULL,
    metric_key TEXT NOT NULL,
    dimensions_json TEXT NOT NULL,
    value_type TEXT NOT NULL,
    unit TEXT NOT NULL,
    source_level TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('AVAILABLE', 'UNAVAILABLE', 'INVALID')
    ),
    value_json TEXT,
    reason_code TEXT,
    PRIMARY KEY (
        run_id,
        metric_set_id,
        metric_set_version,
        metric_key,
        dimensions_json
    ),
    FOREIGN KEY (run_id, metric_set_id, metric_set_version)
        REFERENCES run_metric_evaluations(
            run_id,
            metric_set_id,
            metric_set_version
        ) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_run_metric_values_key
ON run_metric_values(metric_set_id, metric_set_version, metric_key);

CREATE TABLE IF NOT EXISTS aggregate_metric_evaluations (
    aggregation_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
    group_key TEXT NOT NULL,
    scenario_id TEXT,
    metric_set_id TEXT NOT NULL,
    metric_set_version TEXT NOT NULL,
    definition_hash TEXT NOT NULL,
    member_fingerprint TEXT NOT NULL,
    aggregation_spec_json TEXT NOT NULL,
    counts_json TEXT NOT NULL,
    issues_json TEXT NOT NULL,
    evaluated_at TEXT NOT NULL,
    FOREIGN KEY (metric_set_id, metric_set_version)
        REFERENCES metric_sets(metric_set_id, metric_set_version)
);

CREATE TABLE IF NOT EXISTS aggregate_metric_values (
    aggregation_id TEXT NOT NULL REFERENCES aggregate_metric_evaluations(
        aggregation_id
    ) ON DELETE CASCADE,
    metric_key TEXT NOT NULL,
    dimensions_json TEXT NOT NULL,
    value_type TEXT NOT NULL,
    unit TEXT NOT NULL,
    statistics_json TEXT NOT NULL,
    PRIMARY KEY (aggregation_id, metric_key, dimensions_json)
);

CREATE INDEX IF NOT EXISTS idx_aggregate_metric_group
ON aggregate_metric_evaluations(
    metric_set_id,
    metric_set_version,
    group_key
);
"""


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ExperimentRepositoryIntegrityError(
            "repository timestamps must be timezone-aware"
        )
    return value.isoformat()


def _json_object(value: str | None, *, name: str) -> dict[str, object]:
    if value is None:
        raise ExperimentRepositoryIntegrityError(f"{name} is not stored")
    try:
        document = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ExperimentRepositoryIntegrityError(
            f"{name} contains invalid JSON"
        ) from exc
    if not isinstance(document, dict):
        raise ExperimentRepositoryIntegrityError(
            f"{name} must contain a JSON object"
        )
    return document


def _code_revisions(
    document: Mapping[str, Any],
) -> dict[str, CodeRevision]:
    revisions: dict[str, CodeRevision] = {}
    for name, raw in document.items():
        if not isinstance(raw, Mapping):
            raise ExperimentRepositoryIntegrityError(
                f"code revision {name!r} must be an object"
            )
        dirty = raw.get("dirty", False)
        if not isinstance(dirty, bool):
            raise ExperimentRepositoryIntegrityError(
                f"code revision {name!r} dirty must be boolean"
            )
        try:
            revisions[name] = CodeRevision(
                commit=str(raw["commit"]),
                dirty=dirty,
                dirty_fingerprint=raw.get("dirty_fingerprint"),
                tag=raw.get("tag"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ExperimentRepositoryIntegrityError(
                f"code revision {name!r} is invalid"
            ) from exc
    return revisions


class SQLiteExperimentRepository:
    """One SQLite database per Experiment."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @contextmanager
    def _reader(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _migrate(self) -> None:
        connection = self._connect()
        try:
            current = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            if current > SQLITE_SCHEMA_VERSION:
                raise ExperimentRepositoryIntegrityError(
                    f"database schema version {current} is newer than "
                    f"supported version {SQLITE_SCHEMA_VERSION}"
                )
            if current < 1:
                with connection:
                    connection.executescript(_SCHEMA_SQL)
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO schema_migrations(
                            version,
                            applied_at
                        ) VALUES (?, ?)
                        """,
                        (1, datetime.now().astimezone().isoformat()),
                    )
                    connection.execute("PRAGMA user_version = 1")
                current = 1
            if current < 2:
                with connection:
                    connection.execute(
                        "ALTER TABLE runs ADD COLUMN archived_at TEXT"
                    )
                    connection.execute(
                        "ALTER TABLE runs ADD COLUMN archive_reason TEXT"
                    )
                    connection.execute(
                        """
                        INSERT INTO schema_migrations(
                            version,
                            applied_at
                        ) VALUES (?, ?)
                        """,
                        (2, datetime.now().astimezone().isoformat()),
                    )
                    connection.execute("PRAGMA user_version = 2")
                current = 2
            if current < 3:
                with connection:
                    connection.executescript(_METRIC_SCHEMA_SQL)
                    connection.execute(
                        """
                        INSERT INTO schema_migrations(
                            version,
                            applied_at
                        ) VALUES (?, ?)
                        """,
                        (3, datetime.now().astimezone().isoformat()),
                    )
                    connection.execute("PRAGMA user_version = 3")
        finally:
            connection.close()

    @staticmethod
    def _revision_fingerprints(
        revisions: Mapping[str, CodeRevision],
    ) -> dict[str, object]:
        return {
            name: revision.fingerprint_document()
            for name, revision in revisions.items()
        }

    @staticmethod
    def _stored_revision_fingerprints(
        document: Mapping[str, Any],
    ) -> dict[str, object]:
        return {
            name: {
                "commit": raw.get("commit"),
                "dirty": raw.get("dirty", False),
                "dirty_fingerprint": raw.get("dirty_fingerprint"),
            }
            for name, raw in document.items()
            if isinstance(raw, Mapping)
        }

    def create_experiment(
        self,
        plan: ExperimentPlan,
        manifest: ExperimentManifest,
    ) -> None:
        if manifest.experiment != plan.experiment:
            raise ExperimentRepositoryIntegrityError(
                "manifest experiment does not match plan"
            )
        if manifest.planned_run_count != plan.run_count:
            raise ExperimentRepositoryIntegrityError(
                "manifest run count does not match plan"
            )
        created_at = _timestamp(manifest.created_at)
        try:
            with self._transaction() as connection:
                existing_count = connection.execute(
                    "SELECT COUNT(*) FROM experiments"
                ).fetchone()[0]
                if existing_count:
                    raise ExperimentRepositoryConflictError(
                        "one SQLite result database may contain only one "
                        "Experiment"
                    )
                connection.execute(
                    """
                    INSERT INTO experiments(
                        experiment_id,
                        schema_version,
                        status,
                        spec_json,
                        manifest_json,
                        code_revisions_json,
                        planned_run_count,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan.experiment.experiment_id,
                        plan.experiment.schema_version,
                        ExperimentStatus.PLANNED.value,
                        canonical_json(
                            experiment_spec_to_document(plan.experiment)
                        ),
                        canonical_json(manifest_to_document(manifest)),
                        canonical_json(
                            code_revisions_to_document(
                                plan.code_revisions
                            )
                        ),
                        plan.run_count,
                        created_at,
                        created_at,
                    ),
                )
                for run in plan.runs:
                    connection.execute(
                        """
                        INSERT INTO runs(
                            run_id,
                            experiment_id,
                            scenario_id,
                            configuration_hash,
                            run_fingerprint,
                            seed,
                            status,
                            run_spec_json,
                            retention_class
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run.run_id,
                            run.experiment_id,
                            run.scenario.scenario_id,
                            run.configuration_hash,
                            run.run_fingerprint,
                            run.seed,
                            RunStatus.PLANNED.value,
                            canonical_json(run_spec_to_document(run)),
                            (
                                plan.experiment.output
                                .default_retention_class.value
                            ),
                        ),
                    )
        except sqlite3.IntegrityError as exc:
            raise ExperimentRepositoryConflictError(
                f"experiment {plan.experiment.experiment_id!r} "
                "already exists or conflicts with stored state"
            ) from exc

    def create_or_resume_experiment(
        self,
        plan: ExperimentPlan,
        manifest: ExperimentManifest,
    ) -> bool:
        if manifest.experiment != plan.experiment:
            raise ExperimentRepositoryIntegrityError(
                "manifest experiment does not match plan"
            )
        if manifest.planned_run_count != plan.run_count:
            raise ExperimentRepositoryIntegrityError(
                "manifest run count does not match plan"
            )
        with self._reader() as connection:
            experiments = connection.execute(
                """
                SELECT experiment_id, schema_version, spec_json,
                       code_revisions_json, planned_run_count
                FROM experiments
                """
            ).fetchall()
            stored_runs = connection.execute(
                """
                SELECT run_id, run_spec_json
                FROM runs
                ORDER BY rowid
                """
            ).fetchall()
        if not experiments:
            self.create_experiment(plan, manifest)
            return True
        if len(experiments) != 1:
            raise ExperimentRepositoryIntegrityError(
                "one experiment database must contain exactly one Experiment"
            )
        row = experiments[0]
        stored_revisions = _json_object(
            row["code_revisions_json"],
            name="code_revisions_json",
        )
        expected_runs = [
            (
                run.run_id,
                canonical_json(run_spec_to_document(run)),
            )
            for run in plan.runs
        ]
        actual_runs = [
            (stored["run_id"], stored["run_spec_json"])
            for stored in stored_runs
        ]
        matches = (
            row["experiment_id"] == plan.experiment.experiment_id
            and row["schema_version"] == plan.experiment.schema_version
            and row["spec_json"]
            == canonical_json(
                experiment_spec_to_document(plan.experiment)
            )
            and self._stored_revision_fingerprints(stored_revisions)
            == self._revision_fingerprints(plan.code_revisions)
            and row["planned_run_count"] == plan.run_count
            and actual_runs == expected_runs
        )
        if not matches:
            raise ExperimentRepositoryConflictError(
                "existing experiment database does not match the "
                "requested ExperimentSpec, Run plan, or code revisions"
            )
        return False

    def start_run(
        self,
        run_spec: RunSpec,
        *,
        started_at: datetime,
    ) -> None:
        timestamp = _timestamp(started_at)
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE runs
                SET status = ?, started_at = ?, finished_at = NULL,
                    duration_seconds = NULL, error_json = NULL
                WHERE run_id = ? AND status = ?
                """,
                (
                    RunStatus.RUNNING.value,
                    timestamp,
                    run_spec.run_id,
                    RunStatus.PLANNED.value,
                ),
            )
            if cursor.rowcount != 1:
                raise ExperimentRepositoryConflictError(
                    f"Run {run_spec.run_id!r} is not PLANNED"
                )
            connection.execute(
                """
                UPDATE experiments
                SET status = ?, updated_at = ?
                WHERE experiment_id = ?
                """,
                (
                    ExperimentStatus.RUNNING.value,
                    timestamp,
                    run_spec.experiment_id,
                ),
            )

    def complete_run(
        self,
        run_spec: RunSpec,
        *,
        summary: Mapping[str, JsonValue],
        trace: Mapping[str, JsonValue],
        market_reference: MarketReference,
        finished_at: datetime,
        duration_seconds: float,
    ) -> None:
        if duration_seconds < 0:
            raise ExperimentRepositoryIntegrityError(
                "duration_seconds must be >= 0"
            )
        timestamp = _timestamp(finished_at)
        summary_json = canonical_json(summary)
        encoded = encode_trace(trace)
        with self._transaction() as connection:
            self._store_market_reference(connection, market_reference)
            connection.execute(
                """
                INSERT INTO run_payloads(
                    run_id,
                    payload_type,
                    compression,
                    payload_blob,
                    uncompressed_size,
                    payload_sha256
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_spec.run_id,
                    encoded.payload_type,
                    encoded.compression,
                    encoded.data,
                    encoded.uncompressed_size,
                    encoded.payload_sha256,
                ),
            )
            cursor = connection.execute(
                """
                UPDATE runs
                SET status = ?, summary_json = ?, error_json = NULL,
                    trace_state = ?, market_path_id = ?,
                    finished_at = ?, duration_seconds = ?
                WHERE run_id = ? AND status = ?
                """,
                (
                    RunStatus.SUCCEEDED.value,
                    summary_json,
                    TraceState.STORED.value,
                    market_reference.market_path_id,
                    timestamp,
                    duration_seconds,
                    run_spec.run_id,
                    RunStatus.RUNNING.value,
                ),
            )
            if cursor.rowcount != 1:
                raise ExperimentRepositoryConflictError(
                    f"Run {run_spec.run_id!r} is not RUNNING"
                )
            connection.execute(
                """
                UPDATE experiments
                SET status = CASE
                        WHEN EXISTS (
                            SELECT 1
                            FROM runs
                            WHERE experiment_id = ?
                              AND status = 'FAILED'
                        )
                        THEN 'FAILED'
                        WHEN EXISTS (
                            SELECT 1
                            FROM runs
                            WHERE experiment_id = ?
                              AND status IN ('PLANNED', 'RUNNING')
                        )
                        THEN 'RUNNING'
                        ELSE 'SUCCEEDED'
                    END,
                    updated_at = ?
                WHERE experiment_id = ?
                """,
                (
                    run_spec.experiment_id,
                    run_spec.experiment_id,
                    timestamp,
                    run_spec.experiment_id,
                ),
            )

    @staticmethod
    def _store_market_reference(
        connection: sqlite3.Connection,
        reference: MarketReference,
    ) -> None:
        existing = connection.execute(
            """
            SELECT content_hash, file_sha256, storage_path,
                   schema_version, frame_count, instrument
            FROM market_references
            WHERE market_path_id = ?
            """,
            (reference.market_path_id,),
        ).fetchone()
        expected = (
            reference.content_hash,
            reference.file_sha256,
            reference.storage_path,
            reference.schema_version,
            reference.frame_count,
            reference.instrument,
        )
        if existing is not None:
            if tuple(existing) != expected:
                raise ExperimentRepositoryIntegrityError(
                    "stored market reference conflicts with new reference"
                )
            return
        connection.execute(
            """
            INSERT INTO market_references(
                market_path_id,
                content_hash,
                file_sha256,
                storage_path,
                schema_version,
                frame_count,
                instrument
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (reference.market_path_id, *expected),
        )

    def fail_run(
        self,
        run_spec: RunSpec,
        *,
        error: Mapping[str, JsonValue],
        finished_at: datetime,
        duration_seconds: float,
    ) -> None:
        if duration_seconds < 0:
            raise ExperimentRepositoryIntegrityError(
                "duration_seconds must be >= 0"
            )
        timestamp = _timestamp(finished_at)
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM run_payloads WHERE run_id = ?",
                (run_spec.run_id,),
            )
            cursor = connection.execute(
                """
                UPDATE runs
                SET status = ?, summary_json = NULL, error_json = ?,
                    trace_state = NULL, market_path_id = NULL,
                    finished_at = ?, duration_seconds = ?
                WHERE run_id = ? AND status = ?
                """,
                (
                    RunStatus.FAILED.value,
                    canonical_json(error),
                    timestamp,
                    duration_seconds,
                    run_spec.run_id,
                    RunStatus.RUNNING.value,
                ),
            )
            if cursor.rowcount != 1:
                raise ExperimentRepositoryConflictError(
                    f"Run {run_spec.run_id!r} is not RUNNING"
                )
            connection.execute(
                """
                UPDATE experiments
                SET status = ?, updated_at = ?
                WHERE experiment_id = ?
                """,
                (
                    ExperimentStatus.FAILED.value,
                    timestamp,
                    run_spec.experiment_id,
                ),
            )

    def _reset_run(
        self,
        run_spec: RunSpec,
        *,
        expected_status: RunStatus,
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM run_payloads WHERE run_id = ?",
                (run_spec.run_id,),
            )
            cursor = connection.execute(
                """
                UPDATE runs
                SET status = ?, summary_json = NULL, error_json = NULL,
                    trace_state = NULL, market_path_id = NULL,
                    started_at = NULL, finished_at = NULL,
                    duration_seconds = NULL
                WHERE run_id = ? AND status = ?
                """,
                (
                    RunStatus.PLANNED.value,
                    run_spec.run_id,
                    expected_status.value,
                ),
            )
            if cursor.rowcount != 1:
                raise ExperimentRepositoryConflictError(
                    f"Run {run_spec.run_id!r} is not "
                    f"{expected_status.value}"
                )

    def reset_failed_run(self, run_spec: RunSpec) -> None:
        self._reset_run(
            run_spec,
            expected_status=RunStatus.FAILED,
        )

    def recover_interrupted_run(self, run_spec: RunSpec) -> None:
        self._reset_run(
            run_spec,
            expected_status=RunStatus.RUNNING,
        )

    def get_run_record(self, run_id: str) -> RunRecord:
        with self._reader() as connection:
            row = connection.execute(
                """
                SELECT r.*, e.code_revisions_json
                FROM runs AS r
                JOIN experiments AS e
                  ON e.experiment_id = r.experiment_id
                WHERE r.run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise ExperimentRepositoryError(f"Run {run_id!r} not found")
        revision_document = _json_object(
            row["code_revisions_json"],
            name="code_revisions_json",
        )
        error = (
            _json_object(row["error_json"], name="error_json")
            if row["error_json"] is not None
            else None
        )
        return RunRecord(
            run_id=row["run_id"],
            experiment_id=row["experiment_id"],
            scenario_id=row["scenario_id"],
            configuration_hash=row["configuration_hash"],
            run_fingerprint=row["run_fingerprint"],
            seed=row["seed"],
            status=RunStatus(row["status"]),
            code_revisions=_code_revisions(revision_document),
            retention_class=RetentionClass(row["retention_class"]),
            trace_state=(
                TraceState(row["trace_state"])
                if row["trace_state"] is not None
                else None
            ),
            started_at=(
                datetime.fromisoformat(row["started_at"])
                if row["started_at"] is not None
                else None
            ),
            finished_at=(
                datetime.fromisoformat(row["finished_at"])
                if row["finished_at"] is not None
                else None
            ),
            duration_seconds=row["duration_seconds"],
            error=error,
            market_path_id=row["market_path_id"],
            archived_at=(
                datetime.fromisoformat(row["archived_at"])
                if row["archived_at"] is not None
                else None
            ),
            archive_reason=row["archive_reason"],
        )

    def get_summary(self, run_id: str) -> dict[str, object]:
        with self._reader() as connection:
            row = connection.execute(
                "SELECT summary_json FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise ExperimentRepositoryError(f"Run {run_id!r} not found")
        return _json_object(row["summary_json"], name="summary_json")

    def get_market_reference(self, run_id: str) -> MarketReference:
        with self._reader() as connection:
            row = connection.execute(
                """
                SELECT m.*
                FROM runs AS r
                JOIN market_references AS m
                  ON m.market_path_id = r.market_path_id
                WHERE r.run_id = ?
                  AND r.status = 'SUCCEEDED'
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise ExperimentRepositoryError(
                f"market reference for Run {run_id!r} not found"
            )
        return MarketReference(
            market_path_id=row["market_path_id"],
            content_hash=row["content_hash"],
            file_sha256=row["file_sha256"],
            storage_path=row["storage_path"],
            schema_version=row["schema_version"],
            frame_count=row["frame_count"],
            instrument=row["instrument"],
        )

    def load_trace(self, run_id: str) -> dict[str, object]:
        with self._reader() as connection:
            row = connection.execute(
                """
                SELECT payload_type, compression, payload_blob,
                       uncompressed_size, payload_sha256
                FROM run_payloads
                WHERE run_id = ? AND payload_type = 'TRACE'
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise ExperimentRepositoryError(
                f"Trace for Run {run_id!r} not found"
            )
        return decode_trace(
            EncodedPayload(
                payload_type=row["payload_type"],
                compression=row["compression"],
                data=row["payload_blob"],
                uncompressed_size=row["uncompressed_size"],
                payload_sha256=row["payload_sha256"],
            )
        )

    def get_manifest_document(self) -> dict[str, object]:
        with self._reader() as connection:
            rows = connection.execute(
                "SELECT manifest_json FROM experiments"
            ).fetchall()
        if len(rows) != 1:
            raise ExperimentRepositoryIntegrityError(
                "one experiment database must contain exactly one manifest"
            )
        return _json_object(
            rows[0]["manifest_json"],
            name="manifest_json",
        )

    def archive_run(
        self,
        run_id: str,
        *,
        archived_at: datetime,
        reason: str | None = None,
    ) -> RunRecord:
        timestamp = _timestamp(archived_at)
        if reason is not None and not reason.strip():
            raise ExperimentRepositoryIntegrityError(
                "archive reason must not be empty"
            )
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT status, trace_state, retention_class, archived_at
                FROM runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            payload_exists = connection.execute(
                """
                SELECT 1
                FROM run_payloads
                WHERE run_id = ? AND payload_type = 'TRACE'
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise ExperimentRepositoryError(
                    f"Run {run_id!r} not found"
                )
            if (
                row["status"] != RunStatus.SUCCEEDED.value
                or row["trace_state"] != TraceState.STORED.value
                or payload_exists is None
            ):
                raise ExperimentRepositoryConflictError(
                    "only a successful Run with a stored Trace "
                    "can be archived"
                )
            if (
                row["retention_class"]
                != RetentionClass.ARCHIVED.value
                or row["archived_at"] is None
            ):
                connection.execute(
                    """
                    UPDATE runs
                    SET retention_class = ?, archived_at = ?,
                        archive_reason = ?
                    WHERE run_id = ?
                    """,
                    (
                        RetentionClass.ARCHIVED.value,
                        timestamp,
                        reason,
                        run_id,
                    ),
                )
        return self.get_run_record(run_id)

    @staticmethod
    def _purge_candidates(
        connection: sqlite3.Connection,
    ) -> list[sqlite3.Row]:
        return connection.execute(
            """
            SELECT r.run_id, length(p.payload_blob) AS payload_bytes
            FROM runs AS r
            JOIN run_payloads AS p
              ON p.run_id = r.run_id
             AND p.payload_type = 'TRACE'
            WHERE r.status = 'SUCCEEDED'
              AND r.trace_state = 'STORED'
              AND r.retention_class = 'STANDARD'
            ORDER BY r.rowid
            """
        ).fetchall()

    @staticmethod
    def _purge_report(rows: list[sqlite3.Row]) -> TracePurgeReport:
        return TracePurgeReport(
            run_ids=tuple(row["run_id"] for row in rows),
            payload_bytes=sum(row["payload_bytes"] for row in rows),
        )

    def preview_standard_trace_purge(self) -> TracePurgeReport:
        with self._reader() as connection:
            rows = self._purge_candidates(connection)
        return self._purge_report(rows)

    def purge_standard_traces(self) -> TracePurgeReport:
        with self._transaction() as connection:
            rows = self._purge_candidates(connection)
            report = self._purge_report(rows)
            for run_id in report.run_ids:
                deleted = connection.execute(
                    """
                    DELETE FROM run_payloads
                    WHERE run_id = ? AND payload_type = 'TRACE'
                    """,
                    (run_id,),
                )
                updated = connection.execute(
                    """
                    UPDATE runs
                    SET trace_state = ?
                    WHERE run_id = ?
                      AND trace_state = ?
                      AND retention_class = ?
                    """,
                    (
                        TraceState.PURGED.value,
                        run_id,
                        TraceState.STORED.value,
                        RetentionClass.STANDARD.value,
                    ),
                )
                if deleted.rowcount != 1 or updated.rowcount != 1:
                    raise ExperimentRepositoryConflictError(
                        f"Trace purge state changed for Run {run_id!r}"
                    )
                connection.execute(
                    """
                    UPDATE run_metric_evaluations
                    SET recomputable = 0
                    WHERE run_id = ?
                      AND input_level IN ('TRACE', 'MARKET')
                    """,
                    (run_id,),
                )
        return report
