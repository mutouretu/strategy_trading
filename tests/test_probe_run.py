from __future__ import annotations

import unittest
from decimal import Decimal

from examples.deterministic_probe import run_probe
from simulation_runtime import (
    IntentStatus,
    TradeIntentMode,
    simulation_result_to_document,
)


class DeterministicProbeTests(unittest.TestCase):
    def test_probe_covers_order_execution_and_accounting(self) -> None:
        result = run_probe()

        self.assertEqual(
            [fill.source_intent_key for fill in result.fills],
            [
                "probe:entry:replacement",
                "probe:exit:take-profit",
                "probe:exit:active",
            ],
        )
        self.assertEqual(
            [fill.price for fill in result.fills],
            [Decimal("99"), Decimal("108"), Decimal("96")],
        )
        self.assertEqual(
            [fill.sequence for fill in result.fills],
            [2, 3, 5],
        )
        self.assertEqual(
            [fill.intent_mode for fill in result.fills],
            [
                TradeIntentMode.PASSIVE,
                TradeIntentMode.PASSIVE,
                TradeIntentMode.ACTIVE,
            ],
        )
        self.assertEqual(
            [
                fill.source_intent_key
                for fill in result.fills
            ],
            [
                "probe:entry:replacement",
                "probe:exit:take-profit",
                "probe:exit:active",
            ],
        )
        self.assertEqual(
            [
                record.intent.intent_key
                for record in result.intents
            ],
            [
                "probe:entry:original",
                "probe:entry:replacement",
                "probe:exit:take-profit",
                "probe:exit:active",
            ],
        )
        self.assertEqual(
            [record.status for record in result.intents],
            [
                IntentStatus.CANCELLED,
                IntentStatus.FILLED,
                IntentStatus.FILLED,
                IntentStatus.FILLED,
            ],
        )
        self.assertEqual(
            [
                instruction.intent_mode
                for instruction in result.instructions
            ],
            [
                TradeIntentMode.PASSIVE,
                TradeIntentMode.PASSIVE,
                TradeIntentMode.ACTIVE,
            ],
        )
        self.assertEqual(result.final_positions, {})
        self.assertEqual(result.final_average_costs, {})
        self.assertEqual(result.final_cash, Decimal("1006"))
        self.assertEqual(result.realized_pnl, Decimal("6"))
        self.assertEqual(result.final_equity, Decimal("1006"))
        self.assertEqual(len(result.equity_curve), len(result.frames))
        self.assertTrue(result.completed)
        self.assertFalse(result.liquidated)
        self.assertFalse(result.bankrupt)
        self.assertIsNone(result.termination_reason)
        self.assertIsNone(result.termination_sequence)
        self.assertEqual(result.margin_snapshots, ())
        self.assertEqual(result.account_events, ())

    def test_probe_serializes_as_complete_simulation_run(self) -> None:
        document = simulation_result_to_document(
            run_probe(),
            run_id="probe",
            interval="1d",
            source="fixed",
        )

        self.assertEqual(document["schema_version"], 2)
        self.assertEqual(len(document["market"]), 6)
        self.assertNotIn("orders", document)
        self.assertEqual(len(document["intents"]), 4)
        self.assertEqual(len(document["instructions"]), 3)
        self.assertEqual(len(document["fills"]), 3)
        self.assertEqual(len(document["equity"]), 6)
        self.assertEqual(document["summary"]["final_equity"], "1006")
        self.assertEqual(
            [
                fill["intent_mode"]
                for fill in document["fills"]
            ],
            ["PASSIVE", "PASSIVE", "ACTIVE"],
        )


if __name__ == "__main__":
    unittest.main()
