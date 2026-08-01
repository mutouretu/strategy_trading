"""Deterministic Scenario and Seed aggregation for metric evaluations."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Mapping, Sequence

from .core import decimal_quantile
from .models import (
    AdverseDirection,
    MetricSet,
    canonical_document,
    document_hash,
)


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _mean(values: Sequence[Decimal]) -> Decimal:
    return sum(values, Decimal("0")) / Decimal(len(values))


def _sample_std(values: Sequence[Decimal]) -> Decimal | None:
    if len(values) < 2:
        return None
    average = _mean(values)
    return (
        sum((value - average) ** 2 for value in values)
        / Decimal(len(values) - 1)
    ).sqrt()


def _numeric(value: object, value_type: str) -> Decimal | None:
    if value_type not in {"DECIMAL", "INTEGER"} or value is None:
        return None
    return Decimal(str(value))


def aggregate_scenario(
    *,
    experiment_id: str,
    scenario_id: str,
    metric_set: MetricSet,
    run_rows: Sequence[Mapping[str, object]],
    evaluations: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Aggregate one exact Scenario; only Seed may vary inside the group."""

    group_key = f"scenario:{scenario_id}"
    evaluation_by_run = {
        str(evaluation["run_id"]): evaluation
        for evaluation in evaluations
    }
    member_document = [
        {
            "run_id": row["run_id"],
            "status": row["status"],
            "input_fingerprint": (
                evaluation_by_run[str(row["run_id"])].get(
                    "input_fingerprint"
                )
                if str(row["run_id"]) in evaluation_by_run
                else None
            ),
        }
        for row in sorted(run_rows, key=lambda item: str(item["run_id"]))
    ]
    member_fingerprint = document_hash(member_document)
    aggregation_spec = {
        "schema_version": "aggregation-spec/v1",
        "group_by": ["scenario_id"],
        "scenario_id": scenario_id,
        "quantile_method": "Hyndman-Fan-Type-7",
        "quantiles": ["0.05", "0.25", "0.50", "0.75", "0.95"],
    }
    aggregation_id = document_hash(
        {
            "experiment_id": experiment_id,
            "group_key": group_key,
            "metric_set_id": metric_set.metric_set_id,
            "metric_set_version": metric_set.version,
            "definition_hash": metric_set.definition_hash,
            "member_fingerprint": member_fingerprint,
            "aggregation_spec": aggregation_spec,
        }
    )[:32]
    total = len(run_rows)
    failed = sum(1 for row in run_rows if row.get("status") == "FAILED")
    succeeded = [row for row in run_rows if row.get("status") == "SUCCEEDED"]
    evaluated = [
        evaluation_by_run[str(row["run_id"])]
        for row in succeeded
        if str(row["run_id"]) in evaluation_by_run
    ]
    invalid = sum(
        1 for evaluation in evaluated if evaluation.get("status") != "SUCCEEDED"
    )

    def summary_bool(row: Mapping[str, object], key: str) -> bool:
        scalars = row.get("summary_scalars", {})
        return bool(scalars.get(f"result.{key}")) if isinstance(scalars, Mapping) else False

    liquidated = sum(1 for row in succeeded if summary_bool(row, "liquidated"))
    bankrupt = sum(1 for row in succeeded if summary_bool(row, "bankrupt"))
    completed = sum(1 for row in succeeded if summary_bool(row, "completed"))
    denominator = len(succeeded)
    counts = {
        "run_count": total,
        "experiment_succeeded_count": len(succeeded),
        "execution_failed_count": failed,
        "evaluated_count": len(evaluated),
        "invalid_evaluation_count": invalid,
        "completed_count": completed,
        "liquidated_count": liquidated,
        "bankrupt_count": bankrupt,
        "completion_rate": _decimal_text(
            Decimal(completed) / Decimal(denominator)
            if denominator
            else Decimal("0")
        ),
        "liquidation_rate": _decimal_text(
            Decimal(liquidated) / Decimal(denominator)
            if denominator
            else Decimal("0")
        ),
        "bankruptcy_rate": _decimal_text(
            Decimal(bankrupt) / Decimal(denominator)
            if denominator
            else Decimal("0")
        ),
    }

    observations: dict[
        tuple[str, str, str, str],
        list[Mapping[str, object]],
    ] = defaultdict(list)
    for evaluation in evaluated:
        raw_values = evaluation.get("values", [])
        if not isinstance(raw_values, list):
            continue
        for value in raw_values:
            if not isinstance(value, Mapping):
                continue
            identity = (
                str(value.get("metric_key")),
                canonical_document(value.get("dimensions", {})),
                str(value.get("unit")),
                str(value.get("value_type")),
            )
            observations[identity].append(value)

    aggregate_values = []
    for identity, items in sorted(observations.items()):
        metric_key, dimensions_json, unit, value_type = identity
        numbers = [
            number
            for item in items
            if item.get("status") == "AVAILABLE"
            for number in [_numeric(item.get("value"), value_type)]
            if number is not None
        ]
        if not numbers:
            continue
        definition = metric_set.definition(metric_key)
        worst: Decimal | None
        if definition.adverse_direction is AdverseDirection.HIGHER:
            worst = max(numbers)
        elif definition.adverse_direction is AdverseDirection.LOWER:
            worst = min(numbers)
        else:
            worst = None
        statistics = {
            "n_available": len(numbers),
            "n_unavailable": sum(
                1 for item in items if item.get("status") == "UNAVAILABLE"
            ),
            "n_invalid": sum(
                1 for item in items if item.get("status") == "INVALID"
            ),
            "mean": _decimal_text(_mean(numbers)),
            "median": _decimal_text(
                decimal_quantile(numbers, Decimal("0.5"))
            ),
            "sample_std": _decimal_text(_sample_std(numbers)),
            "minimum": _decimal_text(min(numbers)),
            "maximum": _decimal_text(max(numbers)),
            "p05": _decimal_text(
                decimal_quantile(numbers, Decimal("0.05"))
            ),
            "p25": _decimal_text(
                decimal_quantile(numbers, Decimal("0.25"))
            ),
            "p75": _decimal_text(
                decimal_quantile(numbers, Decimal("0.75"))
            ),
            "p95": _decimal_text(
                decimal_quantile(numbers, Decimal("0.95"))
            ),
            "adverse_worst": _decimal_text(worst),
        }
        aggregate_values.append(
            {
                "metric_key": metric_key,
                "dimensions": __import__("json").loads(dimensions_json),
                "value_type": value_type,
                "unit": unit,
                "statistics": statistics,
            }
        )
    return {
        "schema_version": "aggregate-metric-evaluation/v1",
        "aggregation_id": aggregation_id,
        "experiment_id": experiment_id,
        "group_key": group_key,
        "scenario_id": scenario_id,
        "metric_set_id": metric_set.metric_set_id,
        "metric_set_version": metric_set.version,
        "definition_hash": metric_set.definition_hash,
        "member_fingerprint": member_fingerprint,
        "aggregation_spec": aggregation_spec,
        "counts": counts,
        "issues": [],
        "values": aggregate_values,
    }
