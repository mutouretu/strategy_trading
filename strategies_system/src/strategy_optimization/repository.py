"""Study metadata stored beside Experiment facts in the same SQLite file."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from experiment_system import canonical_json, experiment_spec_to_document

from .errors import (
    StudyRepositoryConflictError,
    StudyRepositoryError,
)
from .models import StoredStudy, StudyPlan, StudyStatus


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS optimization_studies (
    study_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL UNIQUE REFERENCES experiments(experiment_id),
    schema_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'DRAFT', 'PLANNED', 'RUNNING', 'EXECUTED',
            'EVALUATED', 'SELECTED', 'INVALIDATED'
        )
    ),
    study_fingerprint TEXT NOT NULL UNIQUE,
    protocol_fingerprint TEXT NOT NULL,
    formal_ready INTEGER NOT NULL CHECK (formal_ready IN (0, 1)),
    bundle_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS optimization_study_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    study_id TEXT NOT NULL REFERENCES optimization_studies(study_id)
        ON DELETE CASCADE,
    previous_status TEXT,
    new_status TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_optimization_study_status
ON optimization_studies(status);

CREATE TABLE IF NOT EXISTS optimization_baseline_reports (
    study_id TEXT PRIMARY KEY REFERENCES optimization_studies(study_id)
        ON DELETE CASCADE,
    schema_version TEXT NOT NULL,
    report_fingerprint TEXT NOT NULL UNIQUE,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


_TRANSITIONS = {
    StudyStatus.DRAFT: {StudyStatus.PLANNED, StudyStatus.INVALIDATED},
    StudyStatus.PLANNED: {StudyStatus.RUNNING, StudyStatus.INVALIDATED},
    StudyStatus.RUNNING: {StudyStatus.EXECUTED, StudyStatus.INVALIDATED},
    StudyStatus.EXECUTED: {StudyStatus.EVALUATED, StudyStatus.INVALIDATED},
    StudyStatus.EVALUATED: {StudyStatus.SELECTED, StudyStatus.INVALIDATED},
    StudyStatus.SELECTED: {StudyStatus.INVALIDATED},
    StudyStatus.INVALIDATED: set(),
}


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise StudyRepositoryError("Study timestamps must be timezone-aware")
    return value.isoformat()


def _bundle_document(plan: StudyPlan) -> dict[str, object]:
    bundle = plan.compiled.bundle
    return {
        "study": bundle.study.to_document(),
        "objective_profile": bundle.objective_profile.to_document(),
        "dataset_split": bundle.dataset_split.to_document(),
        "compiled_experiment": experiment_spec_to_document(
            plan.compiled.experiment
        ),
    }


class SQLiteStudyRepository:
    """Own only optimization_* tables in an existing Experiment database."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        if not self.database_path.is_file():
            raise StudyRepositoryError(
                f"experiment database does not exist: {self.database_path}"
            )
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

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
            table = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name = 'experiments'
                """
            ).fetchone()
            if table is None:
                raise StudyRepositoryError(
                    "Study storage requires an experiment-system SQLite database"
                )
            with connection:
                connection.executescript(_SCHEMA_SQL)
        finally:
            connection.close()

    def create_or_validate(
        self,
        plan: StudyPlan,
        *,
        created_at: datetime,
    ) -> bool:
        """Create a PLANNED Study or confirm an exact existing identity."""

        timestamp = _timestamp(created_at)
        compiled = plan.compiled
        study = compiled.bundle.study
        bundle_json = canonical_json(_bundle_document(plan))
        with self._transaction() as connection:
            experiments = connection.execute(
                "SELECT experiment_id, spec_json FROM experiments"
            ).fetchall()
            if len(experiments) != 1:
                raise StudyRepositoryError(
                    "Study database must contain exactly one Experiment"
                )
            experiment = experiments[0]
            if experiment["experiment_id"] != compiled.experiment.experiment_id:
                raise StudyRepositoryConflictError(
                    "Study experiment_id does not match stored Experiment"
                )
            expected_spec = canonical_json(
                experiment_spec_to_document(compiled.experiment)
            )
            if experiment["spec_json"] != expected_spec:
                raise StudyRepositoryConflictError(
                    "Study compiled ExperimentSpec does not match stored Experiment"
                )
            existing = connection.execute(
                "SELECT * FROM optimization_studies"
            ).fetchone()
            if existing is not None:
                matches = (
                    existing["study_id"] == study.study_id
                    and existing["experiment_id"]
                    == compiled.experiment.experiment_id
                    and existing["schema_version"] == study.schema_version
                    and existing["study_fingerprint"]
                    == compiled.study_fingerprint
                    and existing["protocol_fingerprint"]
                    == compiled.protocol_fingerprint
                    and existing["formal_ready"]
                    == int(compiled.formal_ready)
                    and existing["bundle_json"] == bundle_json
                )
                if not matches:
                    raise StudyRepositoryConflictError(
                        "stored Study does not match requested Study bundle"
                    )
                return False
            connection.execute(
                """
                INSERT INTO optimization_studies(
                    study_id, experiment_id, schema_version, status,
                    study_fingerprint, protocol_fingerprint, formal_ready,
                    bundle_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    study.study_id,
                    compiled.experiment.experiment_id,
                    study.schema_version,
                    StudyStatus.PLANNED.value,
                    compiled.study_fingerprint,
                    compiled.protocol_fingerprint,
                    int(compiled.formal_ready),
                    bundle_json,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT INTO optimization_study_events(
                    study_id, previous_status, new_status, reason, created_at
                ) VALUES (?, NULL, ?, ?, ?)
                """,
                (
                    study.study_id,
                    StudyStatus.PLANNED.value,
                    "Study registered after deterministic planning",
                    timestamp,
                ),
            )
        return True

    def transition(
        self,
        study_id: str,
        new_status: StudyStatus,
        *,
        changed_at: datetime,
        reason: str | None = None,
    ) -> StoredStudy:
        timestamp = _timestamp(changed_at)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT status FROM optimization_studies WHERE study_id = ?",
                (study_id,),
            ).fetchone()
            if row is None:
                raise StudyRepositoryError(f"unknown Study {study_id!r}")
            current = StudyStatus(row["status"])
            if new_status not in _TRANSITIONS[current]:
                raise StudyRepositoryConflictError(
                    f"Study cannot transition from {current.value} to "
                    f"{new_status.value}"
                )
            connection.execute(
                """
                UPDATE optimization_studies
                SET status = ?, updated_at = ?
                WHERE study_id = ?
                """,
                (new_status.value, timestamp, study_id),
            )
            connection.execute(
                """
                INSERT INTO optimization_study_events(
                    study_id, previous_status, new_status, reason, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    study_id,
                    current.value,
                    new_status.value,
                    reason,
                    timestamp,
                ),
            )
        return self.get(study_id)

    def get(self, study_id: str) -> StoredStudy:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM optimization_studies WHERE study_id = ?",
                (study_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise StudyRepositoryError(f"unknown Study {study_id!r}")
        try:
            json.loads(row["bundle_json"])
            return StoredStudy(
                study_id=row["study_id"],
                experiment_id=row["experiment_id"],
                status=StudyStatus(row["status"]),
                study_fingerprint=row["study_fingerprint"],
                protocol_fingerprint=row["protocol_fingerprint"],
                formal_ready=bool(row["formal_ready"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise StudyRepositoryError(
                f"stored Study {study_id!r} is corrupt"
            ) from exc

    def save_baseline_report(
        self,
        study_id: str,
        report: dict[str, object],
        *,
        created_at: datetime,
    ) -> bool:
        """Persist one immutable baseline report under the Study namespace."""

        timestamp = _timestamp(created_at)
        report_json = canonical_json(report)
        schema_version = str(report.get("schema_version", ""))
        fingerprint = str(report.get("report_fingerprint", ""))
        if schema_version != "baseline-report/v1" or len(fingerprint) != 64:
            raise StudyRepositoryError("baseline report identity is invalid")
        with self._transaction() as connection:
            study = connection.execute(
                "SELECT study_id FROM optimization_studies WHERE study_id = ?",
                (study_id,),
            ).fetchone()
            if study is None:
                raise StudyRepositoryError(f"unknown Study {study_id!r}")
            existing = connection.execute(
                "SELECT * FROM optimization_baseline_reports WHERE study_id = ?",
                (study_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["schema_version"] == schema_version
                    and existing["report_fingerprint"] == fingerprint
                    and existing["report_json"] == report_json
                ):
                    return False
                raise StudyRepositoryConflictError(
                    "stored baseline report does not match requested report"
                )
            connection.execute(
                """
                INSERT INTO optimization_baseline_reports(
                    study_id, schema_version, report_fingerprint,
                    report_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    study_id,
                    schema_version,
                    fingerprint,
                    report_json,
                    timestamp,
                ),
            )
        return True

    def baseline_report(self, study_id: str) -> dict[str, object] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT report_json FROM optimization_baseline_reports "
                "WHERE study_id = ?",
                (study_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        try:
            document = json.loads(row["report_json"])
        except json.JSONDecodeError as exc:
            raise StudyRepositoryError(
                f"stored baseline report for {study_id!r} is corrupt"
            ) from exc
        if not isinstance(document, dict):
            raise StudyRepositoryError("stored baseline report must be an object")
        return document
