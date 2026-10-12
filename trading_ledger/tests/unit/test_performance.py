from decimal import Decimal as D
import unittest

from trading_ledger.domain.performance import calculate_performance


class PerformanceTests(unittest.TestCase):
    def test_recovery_keeps_period_maximum_drawdown(self):
        result = calculate_performance(D("100"), D("0"), [
            (D("110"), D("0")), (D("99"), D("0")), (D("108"), D("0")),
        ])
        self.assertEqual(result, (D("0.08"), D("-0.10")))

    def test_deposit_and_withdrawal_are_not_profit_or_drawdown(self):
        self.assertEqual(calculate_performance(D("100"), D("0"), [
            (D("200"), D("100")), (D("220"), D("100")), (D("170"), D("50")),
        ]), (D("0.10"), D("0")))

    def test_initial_capital_is_included_in_period_peak(self):
        self.assertEqual(calculate_performance(D("100"), D("0"), [(D("90"), D("0"))]),
                         (D("-0.1"), D("-0.1")))

    def test_missing_and_zero_base_do_not_produce_fake_returns(self):
        self.assertEqual(calculate_performance(D("100"), D("0"), []), (None, None))
        self.assertEqual(calculate_performance(D("0"), D("0"), [(D("100"), D("100"))]), (None, None))
