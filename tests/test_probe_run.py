from __future__ import annotations

import unittest
from decimal import Decimal

from examples.deterministic_probe import run_probe
from simulation_runtime import OrderStatus, simulation_result_to_document


class DeterministicProbeTests(unittest.TestCase):
    def test_probe_covers_order_execution_and_accounting(self) -> None:
        result = run_probe()

        self.assertEqual(
            [fill.order_key for fill in result.fills],
            [
                "probe:entry:replacement",
                "probe:exit:take-profit",
                "probe:exit:market",
            ],
        )
        self.assertEqual(
            [fill.price for fill in result.fills],
            [Decimal("99"), Decimal("108"), Decimal("96")],
        )
        self.assertEqual(
            [
                (
                    record.order.order_key,
                    record.active_from_sequence,
                    record.active_to_sequence,
                    record.status,
                )
                for record in result.orders
            ],
            [
                ("probe:entry:original", 0, 1, OrderStatus.CANCELLED),
                ("probe:entry:replacement", 1, 2, OrderStatus.FILLED),
                ("probe:exit:take-profit", 2, 3, OrderStatus.FILLED),
                ("probe:exit:market", 4, 5, OrderStatus.FILLED),
            ],
        )
        self.assertEqual(result.final_positions, {})
        self.assertEqual(result.final_average_costs, {})
        self.assertEqual(result.final_cash, Decimal("1006"))
        self.assertEqual(result.realized_pnl, Decimal("6"))
        self.assertEqual(result.final_equity, Decimal("1006"))
        self.assertEqual(len(result.equity_curve), len(result.frames))

    def test_probe_serializes_as_complete_simulation_run(self) -> None:
        document = simulation_result_to_document(
            run_probe(),
            run_id="probe",
            interval="1d",
            source="fixed",
        )

        self.assertEqual(len(document["market"]), 6)
        self.assertEqual(len(document["orders"]), 4)
        self.assertEqual(len(document["fills"]), 3)
        self.assertEqual(len(document["equity"]), 6)
        self.assertEqual(document["summary"]["final_equity"], "1006")
        self.assertEqual(document["orders"][0]["status"], "CANCELLED")


if __name__ == "__main__":
    unittest.main()
