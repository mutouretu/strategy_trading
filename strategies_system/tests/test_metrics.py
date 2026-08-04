from __future__ import annotations

import unittest

import strategy_simulation  # noqa: F401 - activates local checkout imports

from metric_system import MetricInput, MetricValueStatus

from strategy_simulation.metrics import BtcAccumulationMetricCalculator
from strategy_simulation.metrics.registry import (
    StrategySystemGridMetricCalculator,
)


def metric_input(strategy_type: str) -> MetricInput:
    return MetricInput(
        run_id="run-1",
        scenario_id="scenario-1",
        run_provider="strategies-simulation/v1",
        run_spec={},
        summary={},
        result_summary={},
        provider_summary={
            "strategy_type": strategy_type,
            "entry_contracts": "315",
            "estimated_liquidation_price_after_entry": "19981",
            "liquidation_target_deviation_rate": "-0.00095",
            "take_profit_level_count": 10,
            "completed_take_profit_level_count": 5,
            "exited_contracts": "155",
            "remaining_contracts": "160",
            "completed": False,
        },
        trace=None,
        trace_state="PURGED",
        interval_ms=86_400_000,
        equity_series=(),
        position_points=(),
    )


class StrategyMetricTests(unittest.TestCase):
    def test_grid_metrics_do_not_label_non_grid_strategies(self) -> None:
        values = StrategySystemGridMetricCalculator().calculate(
            metric_input("hold-btc/v1")
        )
        self.assertEqual(values, ())

    def test_ladder_summary_metrics_are_complete(self) -> None:
        values = BtcAccumulationMetricCalculator().calculate(
            metric_input("target-liquidation-ladder-long/v1")
        )
        by_key = {value.metric_key: value for value in values}
        self.assertEqual(len(values), 9)
        self.assertEqual(
            str(by_key["strategy.take_profit_completion_rate"].value),
            "0.5",
        )
        self.assertEqual(
            by_key["strategy.entry_contracts"].status,
            MetricValueStatus.AVAILABLE,
        )

    def test_non_ladder_strategy_is_explicitly_not_applicable(self) -> None:
        values = BtcAccumulationMetricCalculator().calculate(
            metric_input("hold-btc/v1")
        )
        self.assertTrue(
            all(value.status == MetricValueStatus.UNAVAILABLE for value in values)
        )
        self.assertTrue(all(value.reason_code == "NOT_APPLICABLE" for value in values))


if __name__ == "__main__":
    unittest.main()
