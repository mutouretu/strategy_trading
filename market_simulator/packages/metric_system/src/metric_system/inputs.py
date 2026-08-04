"""Normalize immutable experiment facts for metric calculators."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Mapping, Protocol

from .errors import MetricInputError
from .models import MetricInputLevel


_INTERVAL = re.compile(r"^(?P<count>[1-9][0-9]*)(?P<unit>[mhdw])$")
_INTERVAL_UNIT_MS = {
    "m": 60_000,
    "h": 3_600_000,
    "d": 86_400_000,
    "w": 604_800_000,
}


def decimal_value(value: object, *, name: str) -> Decimal:
    if isinstance(value, bool):
        raise MetricInputError(f"{name} must be a decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MetricInputError(f"{name} must be a decimal") from exc
    if not result.is_finite():
        raise MetricInputError(f"{name} must be finite")
    return result


def integer_value(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MetricInputError(f"{name} must be an integer")
    return value


def interval_milliseconds(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    match = _INTERVAL.fullmatch(value.strip().lower())
    if match is None:
        return None
    return int(match.group("count")) * _INTERVAL_UNIT_MS[match.group("unit")]


@dataclass(frozen=True, slots=True)
class EquityPoint:
    timestamp: int
    value: Decimal


@dataclass(frozen=True, slots=True)
class EquitySeries:
    series_key: str
    valuation_asset: str
    initial_value: Decimal
    final_value: Decimal
    points: tuple[EquityPoint, ...] = ()
    source_level: MetricInputLevel = MetricInputLevel.SUMMARY

    def __post_init__(self) -> None:
        if not self.series_key.strip() or not self.valuation_asset.strip():
            raise MetricInputError("equity series identity must not be empty")
        if not self.initial_value.is_finite() or not self.final_value.is_finite():
            raise MetricInputError("equity series values must be finite")
        previous: int | None = None
        for point in self.points:
            if previous is not None and point.timestamp <= previous:
                raise MetricInputError(
                    f"equity series {self.series_key!r} timestamps "
                    "must be strictly increasing"
                )
            if not point.value.is_finite():
                raise MetricInputError("equity point must be finite")
            previous = point.timestamp
        if self.points:
            if self.points[0].value != self.initial_value:
                raise MetricInputError(
                    f"equity series {self.series_key!r} first value "
                    "must equal initial_value"
                )
            if self.points[-1].value != self.final_value:
                raise MetricInputError(
                    f"equity series {self.series_key!r} final value "
                    "must equal final_value"
                )


@dataclass(frozen=True, slots=True)
class PositionPoint:
    timestamp: int
    positions: Mapping[str, Decimal]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "positions",
            MappingProxyType(dict(self.positions)),
        )


@dataclass(frozen=True, slots=True)
class MetricInput:
    run_id: str
    scenario_id: str
    run_provider: str
    run_spec: Mapping[str, object]
    summary: Mapping[str, object]
    result_summary: Mapping[str, object]
    provider_summary: Mapping[str, object]
    trace: Mapping[str, object] | None
    trace_state: str | None
    interval_ms: int | None
    equity_series: tuple[EquitySeries, ...]
    position_points: tuple[PositionPoint, ...]
    position_units: Mapping[str, str] = field(default_factory=dict)
    contributor_versions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("run_id", "scenario_id", "run_provider"):
            if not str(getattr(self, name)).strip():
                raise MetricInputError(f"{name} must not be empty")
        for name in (
            "run_spec",
            "summary",
            "result_summary",
            "provider_summary",
            "position_units",
            "contributor_versions",
        ):
            object.__setattr__(
                self,
                name,
                MappingProxyType(dict(getattr(self, name))),
            )
        identities = {
            (series.series_key, series.valuation_asset)
            for series in self.equity_series
        }
        if len(identities) != len(self.equity_series):
            raise MetricInputError("equity series identities must be unique")

    @property
    def input_level(self) -> MetricInputLevel:
        return (
            MetricInputLevel.TRACE
            if self.trace is not None
            else MetricInputLevel.SUMMARY
        )

    def with_contribution(
        self,
        *,
        equity_series: tuple[EquitySeries, ...] = (),
        position_units: Mapping[str, str] | None = None,
        contributor_name: str,
        contributor_version: str,
    ) -> "MetricInput":
        versions = dict(self.contributor_versions)
        versions[contributor_name] = contributor_version
        units = dict(self.position_units)
        units.update(position_units or {})
        return replace(
            self,
            equity_series=(*self.equity_series, *equity_series),
            position_units=units,
            contributor_versions=versions,
        )


class MetricInputContributor(Protocol):
    contributor_name: str
    provider_id: str
    version: str

    def contribute(self, metric_input: MetricInput) -> MetricInput: ...


def _mapping(value: object, *, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise MetricInputError(f"{name} must be an object")
    return value


def _trace_list(
    trace: Mapping[str, object],
    key: str,
) -> tuple[Mapping[str, object], ...]:
    raw = trace.get(key, [])
    if not isinstance(raw, list):
        raise MetricInputError(f"Trace {key!r} must be an array")
    values = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise MetricInputError(
                f"Trace {key}[{index}] must be an object"
            )
        values.append(item)
    return tuple(values)


class MetricInputBuilder:
    """Build a domain-neutral input from one ExperimentReader Run detail."""

    def build(
        self,
        run_detail: Mapping[str, object],
        *,
        trace: Mapping[str, object] | None,
    ) -> MetricInput:
        if run_detail.get("status") != "SUCCEEDED":
            raise MetricInputError("metrics require a SUCCEEDED experiment Run")
        run_spec = _mapping(run_detail.get("run_spec"), name="run_spec")
        summary = _mapping(run_detail.get("summary"), name="summary")
        result = _mapping(summary.get("result"), name="summary.result")
        provider_all = _mapping(
            summary.get("provider_summary", {}),
            name="summary.provider_summary",
        )
        provider_id = str(run_spec.get("run_provider", ""))
        provider_summary = _mapping(
            provider_all.get(provider_id, {}),
            name=f"provider_summary.{provider_id}",
        )
        initial = decimal_value(
            result.get("initial_equity"),
            name="result.initial_equity",
        )
        final = decimal_value(
            result.get("final_equity"),
            name="result.final_equity",
        )
        equity_asset = str(result.get("equity_asset", "")).upper()
        if not equity_asset:
            raise MetricInputError("result.equity_asset must not be empty")
        market = _mapping(run_spec.get("market"), name="run_spec.market")
        market_parameters = _mapping(
            market.get("parameters", {}),
            name="run_spec.market.parameters",
        )
        interval_ms = interval_milliseconds(
            market_parameters.get("interval")
        )
        equity_points: tuple[EquityPoint, ...] = ()
        position_points: tuple[PositionPoint, ...] = ()
        position_units: dict[str, str] = {}
        if trace is not None:
            equity_rows = _trace_list(trace, "equity")
            if not equity_rows:
                raise MetricInputError("stored Trace has no equity snapshots")
            first_timestamp = integer_value(
                equity_rows[0].get("timestamp"),
                name="equity[0].timestamp",
            )
            synthetic_start = first_timestamp - (interval_ms or 1)
            points = [EquityPoint(synthetic_start, initial)]
            positions = [PositionPoint(synthetic_start, {})]
            for index, row in enumerate(equity_rows):
                timestamp = integer_value(
                    row.get("timestamp"),
                    name=f"equity[{index}].timestamp",
                )
                points.append(
                    EquityPoint(
                        timestamp,
                        decimal_value(
                            row.get("equity"),
                            name=f"equity[{index}].equity",
                        ),
                    )
                )
                raw_positions = _mapping(
                    row.get("positions", {}),
                    name=f"equity[{index}].positions",
                )
                positions.append(
                    PositionPoint(
                        timestamp,
                        {
                            str(instrument): decimal_value(
                                quantity,
                                name=(
                                    f"equity[{index}].positions."
                                    f"{instrument}"
                                ),
                            )
                            for instrument, quantity in raw_positions.items()
                        },
                    )
                )
            equity_points = tuple(points)
            position_points = tuple(positions)
            if equity_points[-1].value != final:
                raise MetricInputError(
                    "last equity snapshot does not equal final_equity"
                )
            for fill in _trace_list(trace, "fills"):
                instrument = str(fill.get("instrument", ""))
                tags = fill.get("tags", {})
                if isinstance(tags, Mapping):
                    unit = tags.get("quantity_unit")
                    if instrument and isinstance(unit, str) and unit:
                        previous = position_units.get(instrument)
                        position_units[instrument] = (
                            unit if previous in {None, unit} else "quantity"
                        )
        self._validate_accounting(result)
        return MetricInput(
            run_id=str(run_detail.get("run_id", "")),
            scenario_id=str(run_detail.get("scenario_id", "")),
            run_provider=provider_id,
            run_spec=run_spec,
            summary=summary,
            result_summary=result,
            provider_summary=provider_summary,
            trace=trace,
            trace_state=(
                str(run_detail.get("trace_state"))
                if run_detail.get("trace_state") is not None
                else None
            ),
            interval_ms=interval_ms,
            equity_series=(
                EquitySeries(
                    series_key="account.total_equity",
                    valuation_asset=equity_asset,
                    initial_value=initial,
                    final_value=final,
                    points=equity_points,
                    source_level=(
                        MetricInputLevel.TRACE
                        if equity_points
                        else MetricInputLevel.SUMMARY
                    ),
                ),
            ),
            position_points=position_points,
            position_units=position_units,
        )

    @staticmethod
    def _validate_accounting(result: Mapping[str, object]) -> None:
        required = (
            "gross_realized_pnl",
            "total_fees",
            "net_realized_pnl",
            "total_funding",
            "net_pnl_after_fees_and_funding",
        )
        if any(name not in result for name in required):
            raise MetricInputError("result is missing accounting fields")
        gross, fees, net, funding, after = (
            decimal_value(result[name], name=f"result.{name}")
            for name in required
        )
        if gross - fees != net:
            raise MetricInputError(
                "ACCOUNTING_MISMATCH: gross - fees != net realized"
            )
        if net + funding != after:
            raise MetricInputError(
                "ACCOUNTING_MISMATCH: net + funding != net after funding"
            )
