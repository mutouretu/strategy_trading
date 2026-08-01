"""Read-only experiment catalog, Run filtering, and scalar comparison."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import (
    ExperimentRepositoryError,
    ExperimentValidationError,
)
from .json_values import JsonValue
from .market_data import MarketReference
from .payloads import EncodedPayload, decode_trace


_DATABASE_SUFFIXES = frozenset({".sqlite3", ".sqlite", ".db"})
_SORT_FIELDS = frozenset(
    {
        "run_id",
        "scenario_id",
        "seed",
        "status",
        "duration_seconds",
        "started_at",
        "finished_at",
        "retention_class",
        "run_order",
        "trace_state",
    }
)


def _json_object(
    value: str | None,
    *,
    name: str,
    required: bool = True,
) -> dict[str, Any] | None:
    if value is None:
        if required:
            raise ExperimentRepositoryError(f"{name} is not stored")
        return None
    try:
        document = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ExperimentRepositoryError(
            f"{name} contains invalid JSON"
        ) from exc
    if not isinstance(document, dict):
        raise ExperimentRepositoryError(
            f"{name} must contain a JSON object"
        )
    return document


def flatten_scalars(
    document: Mapping[str, Any],
    *,
    prefix: str = "",
) -> dict[str, JsonValue]:
    """Flatten only scalar leaves; arrays remain raw detail, not columns."""

    flattened: dict[str, JsonValue] = {}
    for key in sorted(document):
        value = document[key]
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, Mapping):
            flattened.update(
                flatten_scalars(value, prefix=path)
            )
        elif (
            value is None
            or isinstance(value, (str, int, bool))
            and not isinstance(value, float)
        ):
            flattened[path] = value
    return flattened


@dataclass(frozen=True, slots=True)
class RunQuery:
    statuses: tuple[str, ...] = ()
    scenario_id: str | None = None
    seed: int | None = None
    retention_class: str | None = None
    trace_state: str | None = None
    search: str | None = None
    sort_by: str = "run_order"
    descending: bool = False
    offset: int = 0
    limit: int | None = None

    def __post_init__(self) -> None:
        if self.sort_by not in _SORT_FIELDS:
            raise ExperimentValidationError(
                f"unsupported Run sort field {self.sort_by!r}"
            )
        if self.offset < 0:
            raise ExperimentValidationError(
                "Run query offset must be >= 0"
            )
        if self.limit is not None and not 1 <= self.limit <= 10_000:
            raise ExperimentValidationError(
                "Run query limit must be between 1 and 10000"
            )


@dataclass(frozen=True, slots=True)
class RunQueryResult:
    rows: tuple[dict[str, object], ...]
    total: int
    offset: int
    limit: int | None


class ExperimentReader:
    """Read one Experiment SQLite database without migration or writes."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).resolve()
        if not self.database_path.is_file():
            raise ExperimentRepositoryError(
                f"experiment database does not exist: {self.database_path}"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"{self.database_path.as_uri()}?mode=ro",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection

    def _experiment_row(self, connection: sqlite3.Connection) -> sqlite3.Row:
        try:
            rows = connection.execute(
                "SELECT * FROM experiments"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise ExperimentRepositoryError(
                f"{self.database_path} is not an experiment database"
            ) from exc
        if len(rows) != 1:
            raise ExperimentRepositoryError(
                "an experiment database must contain exactly one Experiment"
            )
        return rows[0]

    def overview(self, *, database_name: str | None = None) -> dict[str, object]:
        with self._connect() as connection:
            experiment = self._experiment_row(connection)
            status_counts = {
                row["status"]: row["count"]
                for row in connection.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM runs
                    GROUP BY status
                    """
                )
            }
        spec = _json_object(
            experiment["spec_json"],
            name="spec_json",
        )
        assert spec is not None
        return {
            "experiment_id": experiment["experiment_id"],
            "description": spec.get("description", ""),
            "status": experiment["status"],
            "planned_run_count": experiment["planned_run_count"],
            "status_counts": status_counts,
            "created_at": experiment["created_at"],
            "updated_at": experiment["updated_at"],
            "reproducible": bool(
                _json_object(
                    experiment["manifest_json"],
                    name="manifest_json",
                ).get("reproducible")
            ),
            "database_name": database_name or self.database_path.name,
        }

    def experiment_detail(self) -> dict[str, object]:
        with self._connect() as connection:
            experiment = self._experiment_row(connection)
        overview = self.overview()
        return {
            **overview,
            "spec": _json_object(
                experiment["spec_json"],
                name="spec_json",
            ),
            "manifest": _json_object(
                experiment["manifest_json"],
                name="manifest_json",
            ),
            "code_revisions": _json_object(
                experiment["code_revisions_json"],
                name="code_revisions_json",
            ),
        }

    @staticmethod
    def _run_columns(
        connection: sqlite3.Connection,
    ) -> set[str]:
        return {
            row["name"]
            for row in connection.execute("PRAGMA table_info(runs)")
        }

    def _all_run_rows(self) -> list[dict[str, object]]:
        with self._connect() as connection:
            self._experiment_row(connection)
            columns = self._run_columns(connection)
            archived_at = (
                "archived_at"
                if "archived_at" in columns
                else "NULL AS archived_at"
            )
            archive_reason = (
                "archive_reason"
                if "archive_reason" in columns
                else "NULL AS archive_reason"
            )
            rows = connection.execute(
                f"""
                SELECT rowid AS run_order, run_id, experiment_id,
                       scenario_id, configuration_hash,
                       run_fingerprint, seed, status, run_spec_json,
                       summary_json, error_json, trace_state,
                       retention_class, market_path_id, started_at,
                       finished_at, duration_seconds,
                       {archived_at}, {archive_reason}
                FROM runs
                ORDER BY rowid
                """
            ).fetchall()
        return [self._run_document(row) for row in rows]

    @staticmethod
    def _run_document(row: sqlite3.Row) -> dict[str, object]:
        run_spec = _json_object(
            row["run_spec_json"],
            name=f"Run {row['run_id']} run_spec_json",
        )
        assert run_spec is not None
        summary = _json_object(
            row["summary_json"],
            name=f"Run {row['run_id']} summary_json",
            required=False,
        )
        error = _json_object(
            row["error_json"],
            name=f"Run {row['run_id']} error_json",
            required=False,
        )
        component_keys = {
            name: (
                run_spec.get(name, {}).get("key")
                if isinstance(run_spec.get(name), Mapping)
                else None
            )
            for name in ("market", "strategy", "execution", "account")
        }
        parameter_values = run_spec.get("parameter_values", {})
        if not isinstance(parameter_values, Mapping):
            raise ExperimentRepositoryError(
                f"Run {row['run_id']} parameter_values must be an object"
            )
        return {
            "run_order": row["run_order"],
            "run_id": row["run_id"],
            "experiment_id": row["experiment_id"],
            "scenario_id": row["scenario_id"],
            "configuration_hash": row["configuration_hash"],
            "run_fingerprint": row["run_fingerprint"],
            "seed": row["seed"],
            "status": row["status"],
            "run_provider": run_spec.get("run_provider"),
            "components": component_keys,
            "parameter_values": dict(parameter_values),
            "summary_scalars": (
                flatten_scalars(summary) if summary is not None else {}
            ),
            "error": error,
            "trace_state": row["trace_state"],
            "retention_class": row["retention_class"],
            "market_path_id": row["market_path_id"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "duration_seconds": row["duration_seconds"],
            "archived_at": row["archived_at"],
            "archive_reason": row["archive_reason"],
        }

    def query_runs(self, query: RunQuery | None = None) -> RunQueryResult:
        criteria = query or RunQuery()
        rows = self._all_run_rows()
        if criteria.statuses:
            allowed = set(criteria.statuses)
            rows = [row for row in rows if row["status"] in allowed]
        if criteria.scenario_id is not None:
            rows = [
                row
                for row in rows
                if row["scenario_id"] == criteria.scenario_id
            ]
        if criteria.seed is not None:
            rows = [row for row in rows if row["seed"] == criteria.seed]
        if criteria.retention_class is not None:
            rows = [
                row
                for row in rows
                if row["retention_class"] == criteria.retention_class
            ]
        if criteria.trace_state is not None:
            expected = (
                None
                if criteria.trace_state.upper() == "NONE"
                else criteria.trace_state
            )
            rows = [
                row
                for row in rows
                if row["trace_state"] == expected
            ]
        if criteria.search:
            needle = criteria.search.casefold()
            rows = [
                row
                for row in rows
                if needle
                in json.dumps(
                    {
                        "run_id": row["run_id"],
                        "scenario_id": row["scenario_id"],
                        "components": row["components"],
                        "parameter_values": row["parameter_values"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).casefold()
            ]

        def sort_key(row: dict[str, object]) -> tuple[bool, object]:
            value = row[criteria.sort_by]
            if criteria.sort_by in {"seed", "duration_seconds"}:
                return (value is None, value if value is not None else 0)
            return (value is None, str(value or ""))

        rows.sort(
            key=sort_key,
            reverse=criteria.descending,
        )
        total = len(rows)
        end = (
            None
            if criteria.limit is None
            else criteria.offset + criteria.limit
        )
        selected = rows[criteria.offset:end]
        return RunQueryResult(
            rows=tuple(selected),
            total=total,
            offset=criteria.offset,
            limit=criteria.limit,
        )

    def run_detail(self, run_id: str) -> dict[str, object]:
        with self._connect() as connection:
            columns = self._run_columns(connection)
            archived_at = (
                "archived_at"
                if "archived_at" in columns
                else "NULL AS archived_at"
            )
            archive_reason = (
                "archive_reason"
                if "archive_reason" in columns
                else "NULL AS archive_reason"
            )
            row = connection.execute(
                f"""
                SELECT rowid AS run_order, *,
                       {archived_at}, {archive_reason}
                FROM runs
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise ExperimentRepositoryError(f"Run {run_id!r} not found")
        compact = self._run_document(row)
        return {
            **compact,
            "run_spec": _json_object(
                row["run_spec_json"],
                name=f"Run {run_id} run_spec_json",
            ),
            "summary": _json_object(
                row["summary_json"],
                name=f"Run {run_id} summary_json",
                required=False,
            ),
        }

    def load_trace(self, run_id: str) -> dict[str, object]:
        with self._connect() as connection:
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

    def market_reference(self, run_id: str) -> MarketReference:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT m.*
                FROM runs AS r
                JOIN market_references AS m
                  ON m.market_path_id = r.market_path_id
                WHERE r.run_id = ?
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


class ExperimentCatalog:
    """Discover immutable Experiment SQLite files below one result root."""

    def __init__(self, result_root: str | Path) -> None:
        self.result_root = Path(result_root).resolve()
        if not self.result_root.is_dir():
            raise ExperimentValidationError(
                f"result root does not exist: {self.result_root}"
            )

    def _database_paths(self) -> tuple[Path, ...]:
        paths = []
        for path in self.result_root.rglob("*"):
            if (
                path.is_file()
                and path.suffix.lower() in _DATABASE_SUFFIXES
            ):
                resolved = path.resolve()
                if resolved.is_relative_to(self.result_root):
                    paths.append(resolved)
        return tuple(sorted(paths))

    def experiments(self) -> tuple[dict[str, object], ...]:
        experiments = []
        for path in self._database_paths():
            try:
                experiments.append(
                    ExperimentReader(path).overview(
                        database_name=str(
                            path.relative_to(self.result_root)
                        )
                    )
                )
            except ExperimentRepositoryError:
                continue
        experiments.sort(
            key=lambda item: (
                str(item["created_at"]),
                str(item["experiment_id"]),
            ),
            reverse=True,
        )
        return tuple(experiments)

    def reader(self, experiment_id: str) -> ExperimentReader:
        matches = [
            item
            for item in self.experiments()
            if item["experiment_id"] == experiment_id
        ]
        if not matches:
            raise ExperimentRepositoryError(
                f"Experiment {experiment_id!r} not found"
            )
        if len(matches) != 1:
            raise ExperimentValidationError(
                f"Experiment {experiment_id!r} is ambiguous below "
                f"{self.result_root}"
            )
        return ExperimentReader(
            self.result_root / str(matches[0]["database_name"])
        )


__all__ = [
    "ExperimentCatalog",
    "ExperimentReader",
    "RunQuery",
    "RunQueryResult",
    "flatten_scalars",
]
