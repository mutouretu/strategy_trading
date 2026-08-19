"""Explicit CSV and Viewer JSON exports from read-only experiment data."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
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
    metric_scalars = run.get("metric_scalars", {})
    error = run["error"] or {}
    assert isinstance(components, dict)
    assert isinstance(parameters, dict)
    assert isinstance(scalars, dict)
    assert isinstance(metric_scalars, dict)
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
    row.update(
        {
            f"metric:{path}": value
            for path, value in metric_scalars.items()
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


def _decimal(value: object, *, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExperimentRepositoryError(
            f"{name} is not a decimal value"
        ) from exc
    if not result.is_finite():
        raise ExperimentRepositoryError(
            f"{name} must be finite"
        )
    return result


def _asset_equities(
    document: Mapping[str, object],
    *,
    value_name: str,
) -> dict[str, Decimal]:
    values: dict[str, Decimal] = {}
    metrics = document.get("account_metrics", {})
    if isinstance(metrics, Mapping):
        for key, value in metrics.items():
            name = str(key)
            prefix = "total_equity_"
            if name.startswith(prefix) and name[len(prefix):]:
                asset = name[len(prefix):].upper()
                values[asset] = _decimal(
                    value,
                    name=f"{value_name}.account_metrics.{name}",
                )
    asset = str(document.get("equity_asset", "")).strip().upper()
    if asset and document.get("equity") is not None:
        values.setdefault(
            asset,
            _decimal(
                document["equity"],
                name=f"{value_name}.equity",
            ),
        )
    return values


def _initial_asset_equities(
    result: Mapping[str, object],
) -> dict[str, Decimal]:
    synthetic = {
        "equity_asset": result.get("equity_asset"),
        "equity": result.get("initial_equity"),
        "account_metrics": result.get("initial_account_metrics", {}),
    }
    return _asset_equities(synthetic, value_name="summary.result")


def _daily_equity_rows(
    rows: list[Mapping[str, object]],
) -> list[Mapping[str, object]]:
    ordered = sorted(
        rows,
        key=lambda row: (
            int(row.get("timestamp", 0)),
            int(row.get("sequence", 0)),
        ),
    )
    last_by_date: dict[str, Mapping[str, object]] = {}
    for row in ordered:
        timestamp = int(row.get("timestamp", 0))
        date = str(row.get("date", "")) or datetime.fromtimestamp(
            timestamp / 1_000,
            tz=timezone.utc,
        ).date().isoformat()
        last_by_date[date] = row
    return list(last_by_date.values())


def _row_date(row: Mapping[str, object]) -> str:
    timestamp = int(row.get("timestamp", 0))
    return str(row.get("date", "")) or datetime.fromtimestamp(
        timestamp / 1_000,
        tz=timezone.utc,
    ).date().isoformat()


def _margin_document(
    row: Mapping[str, object],
    *,
    value_name: str,
) -> dict[str, object] | None:
    margin_balance = _decimal(
        row.get("margin_balance"),
        name=f"{value_name}.margin_balance",
    )
    maintenance_margin = _decimal(
        row.get("maintenance_margin"),
        name=f"{value_name}.maintenance_margin",
    )
    raw_risk_rate = row.get("maintenance_margin_utilization")
    liquidation_triggered = bool(row.get("liquidation_triggered", False))
    if raw_risk_rate is not None:
        risk_rate = _decimal(
            raw_risk_rate,
            name=f"{value_name}.maintenance_margin_utilization",
        )
    elif margin_balance > 0:
        risk_rate = maintenance_margin / margin_balance
    elif liquidation_triggered:
        risk_rate = Decimal("1")
    else:
        return None
    mark_price = (
        _decimal(
            row["mark_price"],
            name=f"{value_name}.mark_price",
        )
        if row.get("mark_price") is not None
        else None
    )
    estimated_liquidation_price = (
        _decimal(
            row["estimated_liquidation_price"],
            name=f"{value_name}.estimated_liquidation_price",
        )
        if row.get("estimated_liquidation_price") is not None
        else None
    )
    position_quantity = _decimal(
        row.get("position_quantity", "0"),
        name=f"{value_name}.position_quantity",
    )
    liquidation_distance_rate = None
    if (
        mark_price is not None
        and estimated_liquidation_price is not None
        and position_quantity != 0
    ):
        if position_quantity > 0:
            liquidation_distance_rate = (
                mark_price - estimated_liquidation_price
            ) / mark_price
        else:
            liquidation_distance_rate = (
                estimated_liquidation_price - mark_price
            ) / mark_price
    return {
        "settlement_asset": str(row.get("settlement_asset", "")).upper(),
        "position_quantity": str(position_quantity),
        "mark_price": str(mark_price) if mark_price is not None else None,
        "estimated_liquidation_price": (
            str(estimated_liquidation_price)
            if estimated_liquidation_price is not None
            else None
        ),
        "liquidation_distance_rate": (
            str(liquidation_distance_rate)
            if liquidation_distance_rate is not None
            else None
        ),
        "margin_balance": str(margin_balance),
        "maintenance_margin": str(maintenance_margin),
        "risk_rate": str(risk_rate),
        "risk_threshold_rate": "1",
        "liquidation_triggered": liquidation_triggered,
    }


def performance_document(
    reader: ExperimentReader,
    run_id: str,
) -> dict[str, object]:
    """Build a compact daily equity/return/drawdown document for one Run."""

    detail = reader.run_detail(run_id)
    if detail["status"] != "SUCCEEDED":
        raise ExperimentRepositoryError(
            f"Performance data is unavailable for non-successful Run {run_id!r}"
        )
    if detail["trace_state"] != "STORED":
        raise ExperimentRepositoryError(
            f"Performance Trace for Run {run_id!r} is not stored"
        )
    trace = reader.load_trace(run_id)
    raw_rows = trace.get("equity", [])
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ExperimentRepositoryError(
            f"Run {run_id!r} Trace has no equity snapshots"
        )
    if not all(isinstance(row, Mapping) for row in raw_rows):
        raise ExperimentRepositoryError(
            f"Run {run_id!r} Trace equity snapshots are malformed"
        )
    rows = [row for row in raw_rows if isinstance(row, Mapping)]
    summary = detail.get("summary", {})
    result = summary.get("result", {}) if isinstance(summary, Mapping) else {}
    if not isinstance(result, Mapping):
        raise ExperimentRepositoryError(
            f"Run {run_id!r} has no runtime Summary"
        )

    first_timestamp = min(int(row.get("timestamp", 0)) for row in rows)
    timestamps = sorted({int(row.get("timestamp", 0)) for row in rows})
    interval = (
        timestamps[1] - timestamps[0]
        if len(timestamps) > 1 and timestamps[1] > timestamps[0]
        else 1
    )
    initial_timestamp = max(0, first_timestamp - interval)
    initial_values = _initial_asset_equities(result)
    if not initial_values:
        initial_values = _asset_equities(rows[0], value_name="equity[0]")
    if not initial_values:
        raise ExperimentRepositoryError(
            f"Run {run_id!r} has no readable equity assets"
        )

    sampled = _daily_equity_rows(rows)
    raw_margin_rows = trace.get("margin", [])
    if not isinstance(raw_margin_rows, list):
        raise ExperimentRepositoryError(
            f"Run {run_id!r} Trace margin snapshots are malformed"
        )
    if not all(isinstance(row, Mapping) for row in raw_margin_rows):
        raise ExperimentRepositoryError(
            f"Run {run_id!r} Trace margin snapshots are malformed"
        )
    margin_rows = [
        row for row in raw_margin_rows if isinstance(row, Mapping)
    ]
    margin_documents: list[dict[str, object]] = []
    minimum_distance_by_date: dict[str, Decimal] = {}
    minimum_distance_row_by_date: dict[str, Mapping[str, object]] = {}
    for index, row in enumerate(margin_rows):
        document = _margin_document(
            row,
            value_name=f"margin.source[{index}]",
        )
        if document is None:
            continue
        margin_documents.append(document)
        raw_distance = document.get("liquidation_distance_rate")
        if raw_distance is None:
            continue
        distance = _decimal(
            raw_distance,
            name=f"margin.source[{index}].liquidation_distance_rate",
        )
        date = _row_date(row)
        previous_distance = minimum_distance_by_date.get(date)
        if previous_distance is None or distance < previous_distance:
            minimum_distance_by_date[date] = distance
            minimum_distance_row_by_date[date] = row
    daily_last_margin = (
        _daily_equity_rows(margin_rows) if margin_rows else []
    )
    sampled_margin = [
        minimum_distance_row_by_date.get(_row_date(row), row)
        for row in daily_last_margin
    ]
    margin_by_date: dict[str, dict[str, object] | None] = {}
    for index, row in enumerate(sampled_margin):
        date = _row_date(row)
        document = _margin_document(
            row,
            value_name=f"margin.daily[{index}]",
        )
        if document is not None:
            minimum_distance = minimum_distance_by_date.get(date)
            document["minimum_liquidation_distance_rate"] = (
                str(minimum_distance)
                if minimum_distance is not None
                else None
            )
        margin_by_date[date] = document
    assets = set(initial_values)
    sampled_values: list[tuple[Mapping[str, object], dict[str, Decimal]]] = []
    for index, row in enumerate(sampled):
        values = _asset_equities(row, value_name=f"equity.daily[{index}]")
        assets.update(values)
        sampled_values.append((row, values))

    default_asset = str(result.get("equity_asset", "")).strip().upper()
    ordered_assets = sorted(assets)
    if default_asset not in assets:
        default_asset = ordered_assets[0]
    ordered_assets.remove(default_asset)
    ordered_assets.insert(0, default_asset)

    baselines: dict[str, Decimal] = dict(initial_values)
    peaks: dict[str, Decimal] = dict(initial_values)
    maximum_drawdowns: dict[str, Decimal] = {
        asset: Decimal("0") for asset in initial_values
    }

    def point_assets(values: Mapping[str, Decimal]) -> dict[str, object]:
        document: dict[str, object] = {}
        for asset in ordered_assets:
            if asset not in values:
                continue
            equity = values[asset]
            baseline = baselines.setdefault(asset, equity)
            if baseline <= 0:
                raise ExperimentRepositoryError(
                    f"Run {run_id!r} initial {asset} equity must be positive"
                )
            peak = max(peaks.get(asset, equity), equity)
            peaks[asset] = peak
            return_rate = equity / baseline - Decimal("1")
            drawdown_rate = equity / peak - Decimal("1")
            maximum_drawdowns[asset] = min(
                maximum_drawdowns.get(asset, Decimal("0")),
                drawdown_rate,
            )
            document[asset] = {
                "equity": str(equity),
                "return_rate": str(return_rate),
                "drawdown_rate": str(drawdown_rate),
            }
        return document

    initial_date = datetime.fromtimestamp(
        initial_timestamp / 1_000,
        tz=timezone.utc,
    ).date().isoformat()
    points: list[dict[str, object]] = [
        {
            "timestamp": initial_timestamp,
            "date": initial_date,
            "assets": point_assets(initial_values),
            **(
                {
                    "margin": {
                        "settlement_asset": str(
                            sampled_margin[0].get("settlement_asset", "")
                        ).upper(),
                        "mark_price": None,
                        "estimated_liquidation_price": None,
                        "position_quantity": "0",
                        "liquidation_distance_rate": None,
                        "minimum_liquidation_distance_rate": None,
                        "margin_balance": str(
                            initial_values.get(
                                str(
                                    sampled_margin[0].get(
                                        "settlement_asset",
                                        "",
                                    )
                                ).upper(),
                                Decimal("0"),
                            )
                        ),
                        "maintenance_margin": "0",
                        "risk_rate": "0",
                        "risk_threshold_rate": "1",
                        "liquidation_triggered": False,
                    }
                }
                if sampled_margin
                else {}
            ),
        }
    ]
    for row, values in sampled_values:
        timestamp = int(row.get("timestamp", 0))
        date = _row_date(row)
        margin = margin_by_date.get(date)
        points.append(
            {
                "timestamp": timestamp,
                "date": date,
                "assets": point_assets(values),
                **({"margin": margin} if margin is not None else {}),
            }
        )

    final_values = {
        asset: next(
            (
                Decimal(str(point["assets"][asset]["equity"]))
                for point in reversed(points)
                if asset in point["assets"]
            ),
            baselines[asset],
        )
        for asset in ordered_assets
        if asset in baselines
    }
    statistics = {
        asset: {
            "initial_equity": str(baselines[asset]),
            "final_equity": str(final_values[asset]),
            "total_return_rate": str(
                final_values[asset] / baselines[asset] - Decimal("1")
            ),
            "max_drawdown_rate": str(maximum_drawdowns[asset]),
        }
        for asset in final_values
    }
    margin_points = margin_documents
    margin_statistics = None
    if margin_points:
        risk_rates = [
            _decimal(
                point["risk_rate"],
                name="performance.margin.risk_rate",
            )
            for point in margin_points
        ]
        balances = [
            _decimal(
                point["margin_balance"],
                name="performance.margin.margin_balance",
            )
            for point in margin_points
        ]
        liquidation_distances = [
            _decimal(
                point["liquidation_distance_rate"],
                name="performance.margin.liquidation_distance_rate",
            )
            for point in margin_points
            if point.get("liquidation_distance_rate") is not None
        ]
        margin_statistics = {
            "settlement_asset": margin_points[-1]["settlement_asset"],
            "maximum_risk_rate": str(max(risk_rates)),
            "minimum_margin_balance": str(min(balances)),
            "minimum_liquidation_distance_rate": (
                str(min(liquidation_distances))
                if liquidation_distances
                else None
            ),
            "risk_threshold_rate": "1",
            "liquidation_triggered": any(
                bool(point["liquidation_triggered"])
                for point in margin_points
            ),
        }
    return {
        "schema_version": "run-performance/v1",
        "run_id": run_id,
        "sampling": "daily-close-with-initial",
        "source_point_count": len(rows),
        "margin_source_point_count": len(margin_rows),
        "sampled_point_count": len(points),
        "assets": ordered_assets,
        "default_asset": default_asset,
        "statistics": statistics,
        "margin_statistics": margin_statistics,
        "points": points,
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
    "performance_document",
    "viewer_document",
]
