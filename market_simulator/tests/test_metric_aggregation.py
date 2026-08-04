from __future__ import annotations

import unittest

from metric_system import CORE_METRIC_SET, aggregate_scenario


class MetricAggregationTests(unittest.TestCase):
    def test_scenario_aggregation_uses_type_seven_and_keeps_events(self) -> None:
        rows = (
            {
                "run_id": "run-a",
                "status": "SUCCEEDED",
                "summary_scalars": {
                    "result.completed": True,
                    "result.liquidated": False,
                    "result.bankrupt": False,
                },
            },
            {
                "run_id": "run-b",
                "status": "SUCCEEDED",
                "summary_scalars": {
                    "result.completed": False,
                    "result.liquidated": True,
                    "result.bankrupt": True,
                },
            },
            {
                "run_id": "run-c",
                "status": "FAILED",
                "summary_scalars": {},
            },
        )
        dimensions = {
            "scope": "account.total_equity",
            "valuation_asset": "USDT",
        }
        evaluations = tuple(
            {
                "run_id": run_id,
                "status": "SUCCEEDED",
                "input_fingerprint": run_id * 8,
                "values": [
                    {
                        "metric_key": "return.total_rate",
                        "dimensions": dimensions,
                        "value_type": "DECIMAL",
                        "unit": "ratio",
                        "status": "AVAILABLE",
                        "value": value,
                    }
                ],
            }
            for run_id, value in (("run-a", "0.1"), ("run-b", "-0.3"))
        )

        aggregate = aggregate_scenario(
            experiment_id="experiment",
            scenario_id="scenario",
            metric_set=CORE_METRIC_SET,
            run_rows=rows,
            evaluations=evaluations,
        )

        self.assertEqual(aggregate["counts"]["run_count"], 3)
        self.assertEqual(
            aggregate["counts"]["execution_failed_count"],
            1,
        )
        self.assertEqual(aggregate["counts"]["liquidation_rate"], "0.5")
        statistics = aggregate["values"][0]["statistics"]
        self.assertEqual(statistics["mean"], "-0.1")
        self.assertEqual(statistics["median"], "-0.1")
        self.assertEqual(statistics["p05"], "-0.28")
        self.assertEqual(statistics["adverse_worst"], "-0.3")


if __name__ == "__main__":
    unittest.main()
