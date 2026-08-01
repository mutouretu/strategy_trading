"""Explicit CSV and Viewer JSON exports from read-only experiment data."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .comparison import ExperimentReader, RunQuery
from .errors import ExperimentRepositoryError
from .market_data import ParquetMarketStore


_FIXED_COMPARISON_COLUMNS = (
    "run_id",
    "scenario_id",
    "seed",
    "status",
    "market_key",
    "strategy_key",
    "execution_key",
    "account_key",
    "configuration_hash",
    "trace_state",
    "retention_class",
    "started_at",
    "finished_at",
    "duration_seconds",
    "market_path_id",
    "error_type",
    "error_message",
)


@dataclass(frozen=True, slots=True)
class ComparisonTable:
    columns: tuple[str, ...]
    rows: tuple[dict[str, object], ...]


def _comparison_row(run: dict[str, object]) -> dict[str, object]:
    components = run["components"]
    parameters = run["parameter_values"]
    scalars = run["summary_scalars"]
    error = run["error"] or {}
    assert isinstance(components, dict)
    assert isinstance(parameters, dict)
    assert isinstance(scalars, dict)
    assert isinstance(error, dict)
    row: dict[str, object] = {
        "run_id": run["run_id"],
        "scenario_id": run["scenario_id"],
        "seed": run["seed"],
        "status": run["status"],
        "market_key": components.get("market"),
        "strategy_key": components.get("strategy"),
        "execution_key": components.get("execution"),
        "account_key": components.get("account"),
        "configuration_hash": run["configuration_hash"],
        "trace_state": run["trace_state"],
        "retention_class": run["retention_class"],
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
        "duration_seconds": run["duration_seconds"],
        "market_path_id": run["market_path_id"],
        "error_type": error.get("error_type"),
        "error_message": error.get("message"),
    }
    row.update(
        {
            f"parameter:{path}": value
            for path, value in parameters.items()
        }
    )
    row.update(
        {
            f"summary:{path}": value
            for path, value in scalars.items()
        }
    )
    return row


def comparison_table(
    reader: ExperimentReader,
    *,
    query: RunQuery | None = None,
) -> ComparisonTable:
    result = reader.query_runs(query or RunQuery(limit=None))
    rows = tuple(_comparison_row(row) for row in result.rows)
    dynamic_columns = sorted(
        {
            key
            for row in rows
            for key in row
            if key not in _FIXED_COMPARISON_COLUMNS
        }
    )
    return ComparisonTable(
        columns=(
            *_FIXED_COMPARISON_COLUMNS,
            *dynamic_columns,
        ),
        rows=rows,
    )


def _csv_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return value


def comparison_csv_text(table: ComparisonTable) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=table.columns,
        extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()
    for row in table.rows:
        writer.writerow(
            {
                column: _csv_value(row.get(column))
                for column in table.columns
            }
        )
    return output.getvalue()


def export_comparison_csv(
    reader: ExperimentReader,
    output_path: str | Path,
    *,
    query: RunQuery | None = None,
) -> Path:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        comparison_csv_text(comparison_table(reader, query=query)),
        encoding="utf-8",
        newline="",
    )
    return destination.resolve()


def _market_document(frame) -> dict[str, object]:
    return {
        "sequence": frame.sequence,
        "timestamp": frame.timestamp,
        "date": datetime.fromtimestamp(
            frame.timestamp / 1_000,
            tz=timezone.utc,
        ).date().isoformat(),
        "instrument": frame.instrument,
        "open": str(frame.open),
        "high": str(frame.high),
        "low": str(frame.low),
        "close": str(frame.close),
    }


def viewer_document(
    reader: ExperimentReader,
    run_id: str,
) -> dict[str, object]:
    detail = reader.run_detail(run_id)
    if detail["status"] != "SUCCEEDED":
        raise ExperimentRepositoryError(
            f"Viewer data is unavailable for non-successful Run {run_id!r}"
        )
    if detail["trace_state"] != "STORED":
        raise ExperimentRepositoryError(
            f"Viewer Trace for Run {run_id!r} is not stored"
        )
    trace = dict(reader.load_trace(run_id))
    reference = reader.market_reference(run_id)
    if trace.get("market_path_id") != reference.market_path_id:
        raise ExperimentRepositoryError(
            f"Run {run_id!r} Trace and market reference do not match"
        )
    trace.pop("schema_version", None)
    trace.pop("market_path_id", None)
    viewer_schema_version = trace.pop(
        "viewer_schema_version",
        None,
    )
    if viewer_schema_version is None:
        raise ExperimentRepositoryError(
            f"Run {run_id!r} Trace has no Viewer schema version"
        )
    summary = detail["summary"]
    if not isinstance(summary, dict) or not isinstance(
        summary.get("result"),
        dict,
    ):
        raise ExperimentRepositoryError(
            f"Run {run_id!r} has no runtime Summary"
        )
    frames = ParquetMarketStore(
        Path(reference.storage_path).parent
    ).load(reference)
    return {
        "schema_version": viewer_schema_version,
        **trace,
        "market": [_market_document(frame) for frame in frames],
        "summary": summary["result"],
    }


def export_viewer_json(
    reader: ExperimentReader,
    run_id: str,
    output_path: str | Path,
) -> Path:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            viewer_document(reader, run_id),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return destination.resolve()


__all__ = [
    "ComparisonTable",
    "comparison_csv_text",
    "comparison_table",
    "export_comparison_csv",
    "export_viewer_json",
    "viewer_document",
]
