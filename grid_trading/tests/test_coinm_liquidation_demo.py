from __future__ import annotations

import unittest
from decimal import Decimal

from scripts.run_coinm_liquidation_demo import build_run


class CoinMLiquidationDemoTests(unittest.TestCase):
    def test_demo_stops_at_recovered_intrabar_low(self) -> None:
        document = build_run()

        self.assertEqual(
            document["run_status"],
            {
                "completed": False,
                "liquidated": True,
                "bankrupt": False,
                "termination_reason": "LIQUIDATION",
                "termination_sequence": 3,
            },
        )
        self.assertEqual(len(document["market"]), 4)
        self.assertEqual(document["market"][-1]["close"], "90000")
        event = document["account_events"][0]
        self.assertEqual(event["mark_price_sampling"], "ADVERSE_EXTREME")
        self.assertEqual(event["snapshot"]["mark_price"], "77000")
        self.assertGreater(
            Decimal(event["snapshot"]["margin_balance"]),
            Decimal("0"),
        )
        self.assertLessEqual(
            Decimal(event["snapshot"]["margin_balance"]),
            Decimal(event["snapshot"]["maintenance_margin"]),
        )
        self.assertFalse(event["intrabar_ordering_ambiguous"])


if __name__ == "__main__":
    unittest.main()
