"""Declare COIN-M account valuation series without generic name guessing."""

from __future__ import annotations

from decimal import Decimal
from typing import Mapping

from metric_system import (
    EquityPoint,
    EquitySeries,
    MetricInput,
    MetricInputLevel,
    decimal_value,
)

from grid_experiments.provider import GRID_SIMULATION_PROVIDER_V1


class GridMetricInputContributor:
    contributor_name = "grid-coinm-account-series"
    provider_id = GRID_SIMULATION_PROVIDER_V1
    version = "v1"

    def contribute(self, metric_input: MetricInput) -> MetricInput:
        account = metric_input.run_spec.get("account", {})
        if not isinstance(account, Mapping):
            return metric_input.with_contribution(
                contributor_name=self.contributor_name,
                contributor_version=self.version,
            )
        parameters = account.get("parameters", {})
        if not isinstance(parameters, Mapping):
            parameters = {}
        base = str(parameters.get("base_asset", "BTC")).upper()
        quote = str(parameters.get("quote_asset", "USDT")).upper()
        instrument = str(parameters.get("instrument", ""))
        result = metric_input.result_summary
        initial_metrics_raw = result.get("initial_account_metrics", {})
        final_metrics_raw = result.get("final_account_metrics", {})
        initial_metrics = (
            dict(initial_metrics_raw)
            if isinstance(initial_metrics_raw, Mapping)
            else {}
        )
        final_metrics = (
            dict(final_metrics_raw)
            if isinstance(final_metrics_raw, Mapping)
            else {}
        )
        trace_equity = []
        if metric_input.trace is not None:
            raw = metric_input.trace.get("equity", [])
            if isinstance(raw, list):
                trace_equity = [row for row in raw if isinstance(row, Mapping)]
        if not initial_metrics:
            initial_base = decimal_value(
                parameters.get("spot_btc", "0"),
                name="account.spot_btc",
            ) + decimal_value(
                parameters.get("futures_wallet_btc", "0"),
                name="account.futures_wallet_btc",
            )
            initial_futures = decimal_value(
                parameters.get("futures_wallet_btc", "0"),
                name="account.futures_wallet_btc",
            )
            first_mark = self._first_mark(trace_equity, instrument)
            initial_metrics = {
                f"total_equity_{base.lower()}": initial_base,
                f"futures_equity_{base.lower()}": initial_futures,
            }
            if first_mark is not None:
                initial_metrics[f"total_equity_{quote.lower()}"] = (
                    initial_base * first_mark
                )
        series: list[EquitySeries] = []
        candidates = (
            (
                "account.total_equity",
                quote,
                f"total_equity_{quote.lower()}",
            ),
            (
                "account.futures_equity",
                base,
                f"futures_equity_{base.lower()}",
            ),
        )
        for series_key, asset, field in candidates:
            if field not in initial_metrics or field not in final_metrics:
                continue
            initial = decimal_value(
                initial_metrics[field],
                name=f"initial_account_metrics.{field}",
            )
            final = decimal_value(
                final_metrics[field],
                name=f"final_account_metrics.{field}",
            )
            points: tuple[EquityPoint, ...] = ()
            if trace_equity:
                first_timestamp = int(trace_equity[0]["timestamp"])
                values = [
                    EquityPoint(
                        first_timestamp - (metric_input.interval_ms or 1),
                        initial,
                    )
                ]
                complete = True
                for row in trace_equity:
                    account_metrics = row.get("account_metrics", {})
                    if not isinstance(account_metrics, Mapping) or field not in account_metrics:
                        complete = False
                        break
                    values.append(
                        EquityPoint(
                            int(row["timestamp"]),
                            decimal_value(
                                account_metrics[field],
                                name=f"equity.account_metrics.{field}",
                            ),
                        )
                    )
                if complete and values[-1].value == final:
                    points = tuple(values)
            series.append(
                EquitySeries(
                    series_key=series_key,
                    valuation_asset=asset,
                    initial_value=initial,
                    final_value=final,
                    points=points,
                    source_level=(
                        MetricInputLevel.TRACE
                        if points
                        else MetricInputLevel.SUMMARY
                    ),
                )
            )
        units = {instrument: "contracts"} if instrument else {}
        return metric_input.with_contribution(
            equity_series=tuple(series),
            position_units=units,
            contributor_name=self.contributor_name,
            contributor_version=self.version,
        )

    @staticmethod
    def _first_mark(
        trace_equity: list[Mapping[str, object]],
        instrument: str,
    ) -> Decimal | None:
        if not trace_equity or not instrument:
            return None
        marks = trace_equity[0].get("marks", {})
        if not isinstance(marks, Mapping) or instrument not in marks:
            return None
        return decimal_value(
            marks[instrument],
            name=f"equity.marks.{instrument}",
        )
