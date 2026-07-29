from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from jsonschema import Draft202012Validator
from market_protocol import MarketFrame
from market_simulator import FixedBarMarketSource
from simulation_runtime import (
    FixedFundingSchedule,
    FixedRateFundingModel,
    LinearLedger,
    LiquidityRole,
    OrderSide,
    SimFill,
    SimulationRunner,
    TradeInstruction,
    TradeIntentMode,
    simulation_result_to_document,
)


INSTRUMENT = "BTCUSDT"
DAY_SECONDS = 86_400
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def frame(
    *,
    sequence: int = 1,
    timestamp: int = 86_400_000,
    price: str = "100",
) -> MarketFrame:
    mark = Decimal(price)
    return MarketFrame(
        sequence=sequence,
        timestamp=timestamp,
        instrument=INSTRUMENT,
        open=mark,
        high=mark,
        low=mark,
        close=mark,
    )


def fill(side: OrderSide, quantity: str = "2") -> SimFill:
    return SimFill(
        fill_id=f"fill:{side.value.lower()}",
        instruction_key=f"instruction:{side.value.lower()}",
        source_intent_key=f"intent:{side.value.lower()}",
        intent_mode=TradeIntentMode.ACTIVE,
        instrument=INSTRUMENT,
        side=side,
        price=Decimal("100"),
        quantity=Decimal(quantity),
        sequence=1,
        timestamp=86_400_000,
        liquidity_role=LiquidityRole.TAKER,
        fee_rate=Decimal("0"),
        fee_amount=Decimal("0"),
        fee_asset="USDT",
        reduce_only=False,
    )


class FixedFundingScheduleTests(unittest.TestCase):
    def test_schedule_matches_only_visible_aligned_frame_times(self) -> None:
        schedule = FixedFundingSchedule(
            interval_seconds=DAY_SECONDS,
        )

        self.assertTrue(schedule.includes(0))
        self.assertTrue(schedule.includes(86_400_000))
        self.assertFalse(schedule.includes(43_200_000))

    def test_rejects_invalid_interval_and_offset(self) -> None:
        for interval in (0, -1, True):
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    FixedFundingSchedule(interval_seconds=interval)

        with self.assertRaises(ValueError):
            FixedFundingSchedule(
                interval_seconds=DAY_SECONDS,
                offset_seconds=DAY_SECONDS,
            )


class FixedRateFundingModelTests(unittest.TestCase):
    def model(self, rate: str) -> FixedRateFundingModel:
        return FixedRateFundingModel(
            funding_rate=Decimal(rate),
            funding_interval_seconds=DAY_SECONDS,
        )

    def test_positive_rate_long_pays_and_short_receives(self) -> None:
        for side, expected_delta in (
            (OrderSide.BUY, Decimal("-2")),
            (OrderSide.SELL, Decimal("2")),
        ):
            with self.subTest(side=side):
                ledger = LinearLedger(Decimal("1000"))
                ledger.apply(fill(side))

                settlement = self.model("0.01").settle(
                    frame(),
                    ledger,
                    {INSTRUMENT: Decimal("100")},
                )

                self.assertIsNotNone(settlement)
                assert settlement is not None
                self.assertEqual(
                    settlement.position_notional,
                    Decimal("200"),
                )
                self.assertEqual(
                    settlement.position_value,
                    Decimal("200"),
                )
                self.assertEqual(
                    settlement.wallet_delta,
                    expected_delta,
                )

    def test_negative_rate_reverses_payment_direction(self) -> None:
        ledger = LinearLedger(Decimal("1000"))
        ledger.apply(fill(OrderSide.BUY))

        settlement = self.model("-0.01").settle(
            frame(),
            ledger,
            {INSTRUMENT: Decimal("100")},
        )

        self.assertIsNotNone(settlement)
        assert settlement is not None
        self.assertEqual(settlement.wallet_delta, Decimal("2"))

    def test_zero_position_rate_or_unaligned_frame_has_no_event(self) -> None:
        empty = LinearLedger(Decimal("1000"))
        self.assertIsNone(
            self.model("0.01").settle(
                frame(),
                empty,
                {INSTRUMENT: Decimal("100")},
            )
        )

        positioned = LinearLedger(Decimal("1000"))
        positioned.apply(fill(OrderSide.BUY))
        self.assertIsNone(
            self.model("0").settle(
                frame(),
                positioned,
                {INSTRUMENT: Decimal("100")},
            )
        )
        self.assertIsNone(
            self.model("0.01").settle(
                frame(timestamp=43_200_000),
                positioned,
                {INSTRUMENT: Decimal("100")},
            )
        )


class _RoundTripPort:
    def initialize(self, current: MarketFrame) -> None:
        return None

    def instructions_for(
        self,
        current: MarketFrame,
    ) -> tuple[TradeInstruction, ...]:
        if current.sequence == 1:
            return (
                TradeInstruction(
                    instruction_key="open@1",
                    source_intent_key="open",
                    instrument=current.instrument,
                    side=OrderSide.BUY,
                    quantity=Decimal("2"),
                    price=Decimal("100"),
                    frame_sequence=current.sequence,
                    intent_mode=TradeIntentMode.ACTIVE,
                ),
            )
        if current.sequence == 2:
            return (
                TradeInstruction(
                    instruction_key="close@2",
                    source_intent_key="close",
                    instrument=current.instrument,
                    side=OrderSide.SELL,
                    quantity=Decimal("2"),
                    price=Decimal("100"),
                    frame_sequence=current.sequence,
                    intent_mode=TradeIntentMode.ACTIVE,
                    reduce_only=True,
                ),
            )
        return ()

    def on_fills(self, fills: Sequence[SimFill]) -> None:
        return None

    def on_market(self, current: MarketFrame) -> None:
        return None


class FundingRuntimeTests(unittest.TestCase):
    def source(self) -> FixedBarMarketSource:
        return FixedBarMarketSource(
            INSTRUMENT,
            [
                ("100", "100", "100", "100"),
                ("100", "100", "100", "100"),
                ("100", "100", "100", "100"),
            ],
        )

    def test_funding_is_applied_after_fill_and_before_snapshot(self) -> None:
        result = SimulationRunner(
            self.source(),
            trade_port=_RoundTripPort(),
            initial_equity=Decimal("1000"),
            funding_model=FixedRateFundingModel(
                funding_rate=Decimal("0.01"),
                funding_interval_seconds=DAY_SECONDS,
            ),
        ).run()

        self.assertEqual(len(result.funding_events), 1)
        self.assertEqual(
            result.funding_events[0].sequence,
            1,
        )
        self.assertEqual(result.total_funding, Decimal("-2"))
        self.assertEqual(result.net_realized_pnl, Decimal("0"))
        self.assertEqual(
            result.net_pnl_after_fees_and_funding,
            Decimal("-2"),
        )
        self.assertEqual(result.final_cash, Decimal("998"))
        self.assertEqual(result.final_equity, Decimal("998"))
        self.assertEqual(
            result.equity_curve[1].total_funding,
            Decimal("-2"),
        )
        self.assertTrue(result.funding_enabled)
        self.assertEqual(result.funding_source, "FIXED")
        self.assertFalse(result.funding_market_conditioned)

        document = simulation_result_to_document(
            result,
            run_id="fixed-funding-linear",
            interval="1d",
            source="fixed",
        )
        schema = json.loads(
            (
                PROJECT_ROOT
                / "viewer"
                / "simulation-run.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema).validate(document)
        self.assertTrue(document["manifest"]["funding_enabled"])
        self.assertEqual(
            document["manifest"]["funding_source"],
            "FIXED",
        )
        self.assertFalse(
            document["manifest"]["funding_market_conditioned"]
        )
        self.assertEqual(
            document["funding_events"][0]["event_type"],
            "FUNDING_SETTLEMENT",
        )
        self.assertEqual(
            document["funding_events"][0]["wallet_delta"],
            "-2",
        )
        self.assertEqual(document["summary"]["total_funding"], "-2")
        self.assertEqual(
            document["summary"][
                "net_pnl_after_fees_and_funding"
            ],
            "-2",
        )
        self.assertEqual(
            document["summary"]["funding_event_count"],
            1,
        )

    def test_default_zero_model_is_explicit_and_compatible(self) -> None:
        result = SimulationRunner(
            self.source(),
            trade_port=_RoundTripPort(),
            initial_equity=Decimal("1000"),
        ).run()

        self.assertFalse(result.funding_enabled)
        self.assertEqual(result.funding_source, "ZERO")
        self.assertEqual(result.total_funding, Decimal("0"))
        self.assertEqual(result.funding_events, ())
        self.assertEqual(result.final_equity, Decimal("1000"))


if __name__ == "__main__":
    unittest.main()
