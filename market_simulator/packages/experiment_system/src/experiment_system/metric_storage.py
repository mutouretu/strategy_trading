"""Formula-neutral SQLite storage for versioned metric results."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .errors import (
    ExperimentRepositoryConflictError,
    ExperimentRepositoryError,
    ExperimentRepositoryIntegrityError,
)
from .hashing import canonical_json, sha256_document
from .sqlite_repository import SQLiteExperimentRepository


def _required_text(document: Mapping[str, object], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ExperimentRepositoryIntegrityError(
            f"metric document {key!r} must not be empty"
        )
    return value


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ExperimentRepositoryIntegrityError(
            "metric timestamps must be timezone-aware"
        )
    return value.isoformat()


def _json(value: str, *, name: str) -> object:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ExperimentRepositoryIntegrityError(
            f"stored {name} is invalid JSON"
        ) from exc


class ExperimentMetricStore:
    """Store derived values without importing their formula package."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        SQLiteExperimentRepository(self.database_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
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

    def save_run_evaluation(
        self,
        metric_set: Mapping[str, object],
        evaluation: Mapping[str, object],
        *,
        evaluator_revisions: Mapping[str, object],
        evaluated_at: datetime,
        replace_existing: bool = False,
    ) -> bool:
        metric_set_id = _required_text(metric_set, "metric_set_id")
        version = _required_text(metric_set, "version")
        definition_hash = _required_text(
            evaluation,
            "definition_hash",
        )
        if definition_hash != sha256_document(metric_set):
            raise ExperimentRepositoryIntegrityError(
                "metric evaluation definition hash does not match MetricSet"
            )
        run_id = _required_text(evaluation, "run_id")
        values = evaluation.get("values")
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise ExperimentRepositoryIntegrityError(
                "metric evaluation values must be an array"
            )
        timestamp = _timestamp(evaluated_at)
        with self._transaction() as connection:
            stored_set = connection.execute(
                """
                SELECT definition_hash, definition_json
                FROM metric_sets
                WHERE metric_set_id = ? AND metric_set_version = ?
                """,
                (metric_set_id, version),
            ).fetchone()
            definition_json = canonical_json(metric_set)
            if stored_set is None:
                connection.execute(
                    """
                    INSERT INTO metric_sets(
                        metric_set_id,
                        metric_set_version,
                        definition_hash,
                        definition_json,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        metric_set_id,
                        version,
                        definition_hash,
                        definition_json,
                        timestamp,
                    ),
                )
            elif (
                stored_set["definition_hash"] != definition_hash
                or stored_set["definition_json"] != definition_json
            ):
                raise ExperimentRepositoryConflictError(
                    f"metric set {metric_set_id}/{version} conflicts "
                    "with its stored definition"
                )
            existing = connection.execute(
                """
                SELECT input_fingerprint
                FROM run_metric_evaluations
                WHERE run_id = ?
                  AND metric_set_id = ?
                  AND metric_set_version = ?
                """,
                (run_id, metric_set_id, version),
            ).fetchone()
            if existing is not None and not replace_existing:
                return False
            if existing is not None:
                connection.execute(
                    """
                    DELETE FROM run_metric_evaluations
                    WHERE run_id = ?
                      AND metric_set_id = ?
                      AND metric_set_version = ?
                    """,
                    (run_id, metric_set_id, version),
                )
            connection.execute(
                """
                INSERT INTO run_metric_evaluations(
                    run_id,
                    metric_set_id,
                    metric_set_version,
                    definition_hash,
                    input_fingerprint,
                    input_level,
                    recomputable,
                    status,
                    input_hashes_json,
                    evaluator_revisions_json,
                    issues_json,
                    evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    metric_set_id,
                    version,
                    definition_hash,
                    _required_text(evaluation, "input_fingerprint"),
                    _required_text(evaluation, "input_level"),
                    1 if evaluation.get("recomputable") is True else 0,
                    _required_text(evaluation, "status"),
                    canonical_json(evaluation.get("input_hashes", {})),
                    canonical_json(evaluator_revisions),
                    canonical_json(evaluation.get("issues", [])),
                    timestamp,
                ),
            )
            for raw_value in values:
                if not isinstance(raw_value, Mapping):
                    raise ExperimentRepositoryIntegrityError(
                        "metric value must be an object"
                    )
                dimensions = raw_value.get("dimensions", {})
                if not isinstance(dimensions, Mapping):
                    raise ExperimentRepositoryIntegrityError(
                        "metric dimensions must be an object"
                    )
                value = raw_value.get("value")
                connection.execute(
                    """
                    INSERT INTO run_metric_values(
                        run_id,
                        metric_set_id,
                        metric_set_version,
                        metric_key,
                        dimensions_json,
                        value_type,
                        unit,
                        source_level,
                        status,
                        value_json,
                        reason_code
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        metric_set_id,
                        version,
                        _required_text(raw_value, "metric_key"),
                        canonical_json(dimensions),
                        _required_text(raw_value, "value_type"),
                        _required_text(raw_value, "unit"),
                        _required_text(raw_value, "source_level"),
                        _required_text(raw_value, "status"),
                        None if value is None else canonical_json(value),
                        raw_value.get("reason_code"),
                    ),
                )
        return True

    def run_evaluation(
        self,
        run_id: str,
        metric_set_id: str,
        version: str,
    ) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM run_metric_evaluations
                WHERE run_id = ?
                  AND metric_set_id = ?
                  AND metric_set_version = ?
                """,
                (run_id, metric_set_id, version),
            ).fetchone()
            if row is None:
                return None
            values = connection.execute(
                """
                SELECT *
                FROM run_metric_values
                WHERE run_id = ?
                  AND metric_set_id = ?
                  AND metric_set_version = ?
                ORDER BY metric_key, dimensions_json
                """,
                (run_id, metric_set_id, version),
            ).fetchall()
        return self._run_document(row, values)

    @staticmethod
    def _run_document(
        row: sqlite3.Row,
        values: Sequence[sqlite3.Row],
    ) -> dict[str, object]:
        return {
            "run_id": row["run_id"],
            "metric_set_id": row["metric_set_id"],
            "metric_set_version": row["metric_set_version"],
            "definition_hash": row["definition_hash"],
            "input_fingerprint": row["input_fingerprint"],
            "input_level": row["input_level"],
            "recomputable": bool(row["recomputable"]),
            "status": row["status"],
            "input_hashes": _json(
                row["input_hashes_json"],
                name="input_hashes_json",
            ),
            "evaluator_revisions": _json(
                row["evaluator_revisions_json"],
                name="evaluator_revisions_json",
            ),
            "issues": _json(row["issues_json"], name="issues_json"),
            "evaluated_at": row["evaluated_at"],
            "values": [
                {
                    "metric_key": value["metric_key"],
                    "dimensions": _json(
                        value["dimensions_json"],
                        name="dimensions_json",
                    ),
                    "value_type": value["value_type"],
                    "unit": value["unit"],
                    "source_level": value["source_level"],
                    "status": value["status"],
                    "value": (
                        None
                        if value["value_json"] is None
                        else _json(value["value_json"], name="value_json")
                    ),
                    "reason_code": value["reason_code"],
                }
                for value in values
            ],
        }

    def save_aggregate(
        self,
        metric_set: Mapping[str, object],
        aggregate: Mapping[str, object],
        *,
        evaluated_at: datetime,
    ) -> None:
        metric_set_id = _required_text(metric_set, "metric_set_id")
        version = _required_text(metric_set, "version")
        aggregation_id = _required_text(aggregate, "aggregation_id")
        values = aggregate.get("values", [])
        if not isinstance(values, Sequence):
            raise ExperimentRepositoryIntegrityError(
                "aggregate values must be an array"
            )
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM aggregate_metric_evaluations WHERE aggregation_id = ?",
                (aggregation_id,),
            )
            connection.execute(
                """
                INSERT INTO aggregate_metric_evaluations(
                    aggregation_id,
                    experiment_id,
                    group_key,
                    scenario_id,
                    metric_set_id,
                    metric_set_version,
                    definition_hash,
                    member_fingerprint,
                    aggregation_spec_json,
                    counts_json,
                    issues_json,
                    evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    aggregation_id,
                    _required_text(aggregate, "experiment_id"),
                    _required_text(aggregate, "group_key"),
                    aggregate.get("scenario_id"),
                    metric_set_id,
                    version,
                    _required_text(aggregate, "definition_hash"),
                    _required_text(aggregate, "member_fingerprint"),
                    canonical_json(aggregate.get("aggregation_spec", {})),
                    canonical_json(aggregate.get("counts", {})),
                    canonical_json(aggregate.get("issues", [])),
                    _timestamp(evaluated_at),
                ),
            )
            for raw_value in values:
                if not isinstance(raw_value, Mapping):
                    raise ExperimentRepositoryIntegrityError(
                        "aggregate metric value must be an object"
                    )
                connection.execute(
                    """
                    INSERT INTO aggregate_metric_values(
                        aggregation_id,
                        metric_key,
                        dimensions_json,
                        value_type,
                        unit,
                        statistics_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        aggregation_id,
                        _required_text(raw_value, "metric_key"),
                        canonical_json(raw_value.get("dimensions", {})),
                        _required_text(raw_value, "value_type"),
                        _required_text(raw_value, "unit"),
                        canonical_json(raw_value.get("statistics", {})),
                    ),
                )

    def metric_sets(self) -> tuple[dict[str, object], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM metric_sets ORDER BY metric_set_id, metric_set_version"
            ).fetchall()
        return tuple(
            {
                **_json(row["definition_json"], name="definition_json"),
                "definition_hash": row["definition_hash"],
                "created_at": row["created_at"],
            }
            for row in rows
        )

    def aggregate_evaluations(self) -> tuple[dict[str, object], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM aggregate_metric_evaluations
                ORDER BY metric_set_id, metric_set_version, group_key
                """
            ).fetchall()
            documents = []
            for row in rows:
                values = connection.execute(
                    """
                    SELECT * FROM aggregate_metric_values
                    WHERE aggregation_id = ?
                    ORDER BY metric_key, dimensions_json
                    """,
                    (row["aggregation_id"],),
                ).fetchall()
                documents.append(
                    {
                        "aggregation_id": row["aggregation_id"],
                        "experiment_id": row["experiment_id"],
                        "group_key": row["group_key"],
                        "scenario_id": row["scenario_id"],
                        "metric_set_id": row["metric_set_id"],
                        "metric_set_version": row["metric_set_version"],
                        "definition_hash": row["definition_hash"],
                        "member_fingerprint": row["member_fingerprint"],
                        "aggregation_spec": _json(row["aggregation_spec_json"], name="aggregation_spec_json"),
                        "counts": _json(row["counts_json"], name="counts_json"),
                        "issues": _json(row["issues_json"], name="issues_json"),
                        "evaluated_at": row["evaluated_at"],
                        "values": [
                            {
                                "metric_key": value["metric_key"],
                                "dimensions": _json(value["dimensions_json"], name="dimensions_json"),
                                "value_type": value["value_type"],
                                "unit": value["unit"],
                                "statistics": _json(value["statistics_json"], name="statistics_json"),
                            }
                            for value in values
                        ],
                    }
                )
        return tuple(documents)
