from __future__ import annotations

import unittest

from gridtrader.application.performance import percentile, run_benchmark_case


class PerformanceAcceptanceTests(unittest.TestCase):
    def test_percentile_uses_observed_value(self) -> None:
        self.assertEqual(percentile([0.1, 0.2, 0.3, 0.4], 0.95), 0.4)
        self.assertEqual(percentile([], 0.95), 0.0)

    def test_exchange_reads_scale_by_symbol_not_cell(self) -> None:
        one_symbol = run_benchmark_case(
            groups=4,
            cells_per_group=5,
            poll_interval_sec=50,
            symbol_count=1,
            steady_cycles=2,
        )
        four_symbols = run_benchmark_case(
            groups=4,
            cells_per_group=5,
            poll_interval_sec=50,
            symbol_count=4,
            steady_cycles=2,
        )

        self.assertEqual(one_symbol["steady_cycles"]["mark_calls_per_cycle"], 1)
        self.assertEqual(one_symbol["steady_cycles"]["max_mark_calls_per_event"], 1)
        self.assertEqual(one_symbol["steady_cycles"]["max_open_order_calls_per_event"], 1)
        self.assertEqual(four_symbols["steady_cycles"]["mark_calls_per_cycle"], 4)
        self.assertEqual(four_symbols["steady_cycles"]["max_mark_calls_per_event"], 4)
        self.assertEqual(four_symbols["steady_cycles"]["max_open_order_calls_per_event"], 4)
        self.assertTrue(one_symbol["steady_cycles"]["request_scaling_by_symbol"])
        self.assertEqual(one_symbol["process_model"], {"scheduler_processes": 1, "engines": 4})


if __name__ == "__main__":
    unittest.main()
