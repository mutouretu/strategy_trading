from __future__ import annotations

import json
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from examples.deterministic_probe import run_probe
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from simulation_runtime import (
    LiquidationEvent,
    MarginSnapshot,
    MarkPriceSampling,
    SimulationTerminationReason,
    simulation_result_to_document,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SimulationRunSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        schema_path = (
            PROJECT_ROOT
            / "viewer"
            / "simulation-run.schema.json"
        )
        cls.schema = json.loads(schema_path.read_text(encoding="utf-8"))
        cls.validator = Draft202012Validator(
            cls.schema,
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        )

    def test_explicit_result_is_valid_schema_v2(self) -> None:
        document = simulation_result_to_document(
            run_probe(),
            run_id="schema-v2-probe",
            interval="1d",
            source="fixed",
        )

        self.validator.validate(document)
        self.assertEqual(document["schema_version"], 2)
        self.assertNotIn("orders", document)
        self.assertEqual(
            [
                intent["status"]
                for intent in document["intents"]
            ],
            ["CANCELLED", "FILLED", "FILLED", "FILLED"],
        )
        self.assertEqual(
            [
                instruction["frame_sequence"]
                for instruction in document["instructions"]
            ],
            [2, 3, 5],
        )
        self.assertEqual(
            [
                fill["source_intent_key"]
                for fill in document["fills"]
            ],
            [
                "probe:entry:replacement",
                "probe:exit:take-profit",
                "probe:exit:active",
            ],
        )
        self.assertTrue(
            all(
                {
                    "liquidity_role",
                    "reference_price",
                    "slippage_amount",
                    "slippage_bps",
                    "fee_rate",
                    "fee_amount",
                    "fee_asset",
                    "reduce_only",
                }.issubset(fill)
                for fill in document["fills"]
            )
        )
        self.assertEqual(document["summary"]["total_fees"], "0")
        self.assertEqual(document["summary"]["total_funding"], "0")
        self.assertEqual(
            document["summary"]["initial_account_metrics"][
                "total_equity_usdt"
            ],
            document["summary"]["initial_equity"],
        )
        self.assertEqual(
            document["summary"][
                "net_pnl_after_fees_and_funding"
            ],
            document["summary"]["net_realized_pnl"],
        )
        self.assertFalse(document["manifest"]["funding_enabled"])
        self.assertFalse(document["manifest"]["slippage_enabled"])
        self.assertEqual(
            document["manifest"]["slippage_source"],
            "ZERO",
        )
        self.assertEqual(
            document["manifest"]["funding_source"],
            "ZERO",
        )
        self.assertFalse(
            document["manifest"]["funding_market_conditioned"]
        )
        self.assertEqual(document["funding_events"], [])
        self.assertEqual(
            document["summary"]["gross_realized_pnl"],
            document["summary"]["net_realized_pnl"],
        )
        self.assertTrue(
            all(
                {
                    "gross_realized_pnl",
                    "total_fees",
                    "net_realized_pnl",
                    "total_funding",
                    "net_pnl_after_fees_and_funding",
                }.issubset(snapshot)
                for snapshot in document["equity"]
            )
        )

    def test_existing_schema_v1_sample_remains_valid(self) -> None:
        sample_path = (
            PROJECT_ROOT
            / "viewer"
            / "data"
            / "btc-anchored-seed-42.json"
        )
        document = json.loads(sample_path.read_text(encoding="utf-8"))

        self.validator.validate(document)
        self.assertEqual(document["schema_version"], 1)
        self.assertIn("orders", document)

    def test_run_status_rejects_inconsistent_completion_state(
        self,
    ) -> None:
        document = simulation_result_to_document(
            run_probe(),
            run_id="invalid-status-probe",
            interval="1d",
            source="fixed",
        )
        document["run_status"]["bankrupt"] = True

        with self.assertRaises(ValidationError):
            self.validator.validate(document)

    def test_liquidated_result_exports_margin_and_event(self) -> None:
        result = run_probe()
        final_frame = result.frames[-1]
        snapshot = MarginSnapshot(
            sequence=final_frame.sequence,
            timestamp=final_frame.timestamp,
            instrument=final_frame.instrument,
            settlement_asset="BTC",
            notional_asset="USD",
            mark_price=Decimal("95"),
            mark_price_source="market_ohlc_proxy",
            leverage=Decimal("5"),
            position_quantity=Decimal("10"),
            position_unit="contracts",
            average_entry_price=Decimal("100"),
            position_notional=Decimal("1000"),
            wallet_balance=Decimal("0.003"),
            unrealized_pnl=Decimal("-0.00295"),
            margin_balance=Decimal("0.00005"),
            position_initial_margin=Decimal("0.002"),
            maintenance_margin=Decimal("0.00006"),
            available_balance=Decimal("-0.00195"),
            margin_buffer=Decimal("-0.00001"),
            initial_margin_utilization=Decimal("40"),
            maintenance_margin_utilization=Decimal("1.2"),
            effective_leverage=Decimal("200"),
            estimated_liquidation_price=Decimal("95.5"),
            liquidation_triggered=True,
            bankrupt=False,
        )
        event = LiquidationEvent(
            snapshot=snapshot,
            mark_price_sampling=MarkPriceSampling.ADVERSE_EXTREME,
            maintenance_schedule_version="fixture-v1",
            intrabar_ordering_ambiguous=True,
        )
        liquidated = replace(
            result,
            completed=False,
            liquidated=True,
            bankrupt=False,
            termination_reason=(
                SimulationTerminationReason.LIQUIDATION
            ),
            termination_sequence=final_frame.sequence,
            margin_snapshots=(snapshot,),
            account_events=(event,),
        )

        document = simulation_result_to_document(
            liquidated,
            run_id="schema-v2-liquidation",
            interval="1d",
            source="fixed",
        )

        self.validator.validate(document)
        self.assertEqual(
            document["manifest"]["mark_price_source"],
            "market_ohlc_proxy",
        )
        self.assertEqual(
            document["run_status"],
            {
                "completed": False,
                "liquidated": True,
                "bankrupt": False,
                "termination_reason": "LIQUIDATION",
                "termination_sequence": final_frame.sequence,
            },
        )
        self.assertEqual(document["margin"][0]["mark_price"], "95")
        self.assertEqual(
            document["account_events"][0]["mark_price_sampling"],
            "ADVERSE_EXTREME",
        )
        self.assertTrue(
            document["account_events"][0][
                "intrabar_ordering_ambiguous"
            ]
        )
        self.assertEqual(
            document["account_events"][0]["snapshot"],
            document["margin"][-1],
        )

    def test_all_bundled_viewer_runs_match_the_schema(self) -> None:
        data_directory = PROJECT_ROOT / "viewer" / "data"

        for sample_path in sorted(data_directory.glob("*.json")):
            with self.subTest(sample=sample_path.name):
                document = json.loads(
                    sample_path.read_text(encoding="utf-8")
                )
                self.validator.validate(document)


if __name__ == "__main__":
    unittest.main()
