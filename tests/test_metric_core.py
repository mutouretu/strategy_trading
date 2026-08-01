from __future__ import annotations

import unittest
from decimal import Decimal

from metric_system import (
    CoreMetricCalculator,
    EquityPoint,
    EquitySeries,
    MetricInput,
    MetricInputLevel,
    MetricValueStatus,
    PositionPoint,
    decimal_quantile,
)


DAY = 86_400_000


def metric_input(
    equity: tuple[str, ...],
    *,
    trace: bool = True,
) -> MetricInput:
    points = tuple(
        EquityPoint(index * DAY, Decimal(value))
        for index, value in enumerate(equity)
    )
    result = {
        "initial_equity": equity[0],
        "final_equity": equity[-1],
        "equity_asset": "USDT",
        "gross_realized_pnl": "0",
        "total_fees": "0",
        "net_realized_pnl": "0",
        "total_funding": "0",
        "net_pnl_after_fees_and_funding": "0",
        "completed": True,
        "liquidated": False,
        "bankrupt": False,
        "termination_reason": None,
        "termination_sequence": None,
        "final_positions": {"BTCUSD": "1"},
        "fill_count": 0,
        "funding_event_count": 0,
    }
    trace_document = (
        {
            "equity": [
                {"timestamp": point.timestamp, "equity": str(point.value)}
                for point in points[1:]
            ],
            "fills": [],
            "margin": [],
        }
        if trace
        else None
    )
    return MetricInput(
        run_id="run-1",
        scenario_id="scenario-1",
        run_provider="test/v1",
        run_spec={},
        summary={"result": result},
        result_summary=result,
        provider_summary={},
        trace=trace_document,
        trace_state="STORED" if trace else "PURGED",
        interval_ms=DAY,
        equity_series=(
            EquitySeries(
                series_key="account.total_equity",
                valuation_asset="USDT",
                initial_value=Decimal(equity[0]),
                final_value=Decimal(equity[-1]),
                points=points if trace else (),
                source_level=(
                    MetricInputLevel.TRACE
                    if trace
                    else MetricInputLevel.SUMMARY
                ),
            ),
        ),
        position_points=(
            tuple(
                PositionPoint(
                    point.timestamp,
                    {"BTCUSD": Decimal("1") if index else Decimal("0")},
                )
                for index, point in enumerate(points)
            )
            if trace
            else ()
        ),
        position_units={"BTCUSD": "base_asset"},
    )


def available_values(metric_input_value: MetricInput):
    return {
        (value.metric_key, tuple(sorted(value.dimensions.items()))): value
        for value in CoreMetricCalculator().calculate(metric_input_value)
        if value.status is MetricValueStatus.AVAILABLE
    }


class CoreMetricTests(unittest.TestCase):
    def test_hand_calculated_drawdown_and_return(self) -> None:
        values = available_values(metric_input(("100", "120", "90", "110")))
        dimensions = (
            ("scope", "account.total_equity"),
            ("valuation_asset", "USDT"),
        )
        self.assertEqual(
            values[("return.total_rate", dimensions)].value,
            Decimal("0.1"),
        )
        self.assertEqual(
            values[("risk.max_drawdown_rate", dimensions)].value,
            Decimal("0.25"),
        )
        self.assertEqual(
            values[("risk.max_drawdown_amount", dimensions)].value,
            Decimal("30"),
        )
        self.assertEqual(
            values[("risk.minimum_equity", dimensions)].value,
            Decimal("90"),
        )
        self.assertTrue(
            values[("risk.end_underwater", dimensions)].value
        )

    def test_flat_equity_has_zero_drawdown_and_unavailable_sharpe(self) -> None:
        calculated = CoreMetricCalculator().calculate(
            metric_input(("100", "100", "100", "100"))
        )
        by_key = {value.metric_key: value for value in calculated}
        self.assertEqual(by_key["risk.max_drawdown_rate"].value, 0)
        self.assertEqual(
            by_key["risk.sharpe"].status,
            MetricValueStatus.UNAVAILABLE,
        )
        self.assertEqual(
            by_key["risk.sharpe"].reason_code,
            "ZERO_VOLATILITY",
        )

    def test_purged_trace_never_turns_path_metrics_into_zero(self) -> None:
        calculated = CoreMetricCalculator().calculate(
            metric_input(("100", "110"), trace=False)
        )
        by_key = {value.metric_key: value for value in calculated}
        self.assertEqual(by_key["return.total_rate"].value, Decimal("0.1"))
        self.assertEqual(
            by_key["risk.max_drawdown_rate"].status,
            MetricValueStatus.UNAVAILABLE,
        )
        self.assertEqual(
            by_key["risk.max_drawdown_rate"].reason_code,
            "TRACE_PURGED",
        )

    def test_type_seven_quantiles_are_deterministic(self) -> None:
        values = [Decimal("0"), Decimal("10"), Decimal("20"), Decimal("30")]
        self.assertEqual(
            decimal_quantile(values, Decimal("0.25")),
            Decimal("7.50"),
        )
        self.assertEqual(
            decimal_quantile(values, Decimal("0.95")),
            Decimal("28.50"),
        )


if __name__ == "__main__":
    unittest.main()
