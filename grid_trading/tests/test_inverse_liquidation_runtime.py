from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SIMULATOR_ROOT = PROJECT_ROOT.parent / "market_simulator"
for package_path in (
    PROJECT_ROOT,
    SIMULATOR_ROOT / "packages" / "market_protocol" / "src",
    SIMULATOR_ROOT / "packages" / "market_simulator" / "src",
    SIMULATOR_ROOT / "packages" / "simulation_runtime" / "src",
):
    sys.path.insert(0, str(package_path))

from grid_rule.adapters import (  # noqa: E402
    InverseContractLedger,
    InverseContractMarginModel,
)
from market_protocol import MarketFrame  # noqa: E402
from market_simulator import FixedBarMarketSource  # noqa: E402
from simulation_runtime import (  # noqa: E402
    FlatMaintenanceMarginSchedule,
    IntentSnapshot,
    IntentStatus,
    LiquidityRole,
    MarginConfig,
    MarkPriceSampling,
    OrderSide,
    SimFill,
    SimulationRunner,
    SimulationTerminationReason,
    TradeInstruction,
    TradeIntentMode,
    simulation_result_to_document,
)


INSTRUMENT = "BTCUSD_PERP"
CONTRACT_SIZE = Decimal("100")
ENTRY_PRICE = Decimal("100000")


class RecordingTradePort:
    def __init__(
        self,
        instructions_by_sequence: dict[
            int,
            tuple[TradeInstruction, ...],
        ] | None = None,
    ) -> None:
        self.instructions_by_sequence = (
            instructions_by_sequence or {}
        )
        self.events: list[tuple[str, object]] = []
        self.fill_batches: list[tuple[SimFill, ...]] = []

    def initialize(self, frame: MarketFrame) -> None:
        self.events.append(("initialize", frame.sequence))

    def instructions_for(
        self,
        frame: MarketFrame,
    ) -> tuple[TradeInstruction, ...]:
        self.events.append(("instructions", frame.sequence))
        return self.instructions_by_sequence.get(frame.sequence, ())

    def on_fills(self, fills: Sequence[SimFill]) -> None:
        batch = tuple(fills)
        self.fill_batches.append(batch)
        self.events.append(
            (
                "fills",
                tuple(fill.instruction_key for fill in batch),
            )
        )

    def on_market(self, frame: MarketFrame) -> None:
        self.events.append(("market", frame.sequence))


class TracedLiquidatingTradePort(RecordingTradePort):
    def visible_intents(self) -> tuple[IntentSnapshot, ...]:
        return (
            IntentSnapshot(
                intent_key="intent:a-open",
                instrument=INSTRUMENT,
                side=OrderSide.BUY,
                quantity=Decimal("5"),
                intent_mode=TradeIntentMode.ACTIVE,
            ),
            IntentSnapshot(
                intent_key="intent:z-later",
                instrument=INSTRUMENT,
                side=OrderSide.BUY,
                quantity=Decimal("1"),
                intent_mode=TradeIntentMode.ACTIVE,
            ),
        )


def source(
    *bars: tuple[str, str, str, str],
) -> FixedBarMarketSource:
    return FixedBarMarketSource(INSTRUMENT, bars)


def ledger(
    futures_wallet_btc: str,
) -> InverseContractLedger:
    return InverseContractLedger(
        instrument=INSTRUMENT,
        contract_size=CONTRACT_SIZE,
        spot_base_balance=Decimal("1"),
        futures_wallet_balance=Decimal(futures_wallet_btc),
    )


def margin_model() -> InverseContractMarginModel:
    return InverseContractMarginModel(
        MarginConfig(
            leverage=Decimal("5"),
            maintenance_schedule=FlatMaintenanceMarginSchedule(
                Decimal("0.005")
            ),
        )
    )


def seed_long(
    account: InverseContractLedger,
    quantity: str = "10",
) -> None:
    account.apply(
        SimFill(
            fill_id="seed-fill",
            instruction_key="seed-instruction",
            source_intent_key="seed-intent",
            intent_mode=TradeIntentMode.ACTIVE,
            instrument=INSTRUMENT,
            side=OrderSide.BUY,
            price=ENTRY_PRICE,
            quantity=Decimal(quantity),
            sequence=0,
            timestamp=0,
            liquidity_role=LiquidityRole.TAKER,
            fee_rate=Decimal("0"),
            fee_amount=Decimal("0"),
            fee_asset="BTC",
            reduce_only=False,
        )
    )


def seed_short(
    account: InverseContractLedger,
    quantity: str = "10",
) -> None:
    account.apply(
        SimFill(
            fill_id="seed-short-fill",
            instruction_key="seed-short-instruction",
            source_intent_key="seed-short-intent",
            intent_mode=TradeIntentMode.ACTIVE,
            instrument=INSTRUMENT,
            side=OrderSide.SELL,
            price=ENTRY_PRICE,
            quantity=Decimal(quantity),
            sequence=0,
            timestamp=0,
            liquidity_role=LiquidityRole.TAKER,
            fee_rate=Decimal("0"),
            fee_amount=Decimal("0"),
            fee_asset="BTC",
            reduce_only=False,
        )
    )


def instruction(
    key: str,
    *,
    quantity: str,
    price: str = "100000",
    side: OrderSide = OrderSide.BUY,
    reduce_only: bool = False,
) -> TradeInstruction:
    return TradeInstruction(
        instruction_key=key,
        source_intent_key=f"intent:{key}",
        instrument=INSTRUMENT,
        side=side,
        quantity=Decimal(quantity),
        price=Decimal(price),
        frame_sequence=1,
        intent_mode=TradeIntentMode.ACTIVE,
        reduce_only=reduce_only,
    )


class InverseLiquidationRuntimeTests(unittest.TestCase):
    def test_initial_liquidation_stops_before_strategy_initialization(
        self,
    ) -> None:
        account = ledger("0.00005")
        seed_long(account)
        port = RecordingTradePort()

        result = SimulationRunner(
            source(
                ("100000", "100000", "100000", "100000"),
                ("100000", "100000", "100000", "100000"),
            ),
            trade_port=port,
            ledger_factory=lambda: account,
            margin_model=margin_model(),
        ).run()

        self.assertFalse(result.completed)
        self.assertTrue(result.liquidated)
        self.assertFalse(result.bankrupt)
        self.assertEqual(
            result.termination_reason,
            SimulationTerminationReason.LIQUIDATION,
        )
        self.assertEqual(result.termination_sequence, 0)
        self.assertEqual(len(result.frames), 1)
        self.assertEqual(len(result.equity_curve), 1)
        self.assertEqual(len(result.margin_snapshots), 1)
        self.assertEqual(len(result.account_events), 1)
        self.assertEqual(
            result.final_positions,
            {INSTRUMENT: Decimal("10")},
        )
        event = result.account_events[0]
        self.assertEqual(
            event.mark_price_sampling,
            MarkPriceSampling.CLOSE_ONLY,
        )
        self.assertEqual(
            event.maintenance_schedule_version,
            "flat-rate:0.005",
        )
        self.assertFalse(event.intrabar_ordering_ambiguous)
        self.assertEqual(port.events, [])
        document = simulation_result_to_document(
            result,
            run_id="liquidated-test",
            interval="1d",
            source="fixed",
        )
        self.assertTrue(document["run_status"]["liquidated"])
        self.assertEqual(
            document["account_events"][0]["snapshot"],
            document["margin"][-1],
        )

    def test_close_liquidation_stops_future_bars_and_callbacks(
        self,
    ) -> None:
        account = ledger("0.003")
        seed_long(account)
        port = RecordingTradePort()

        result = SimulationRunner(
            source(
                ("100000", "100000", "100000", "100000"),
                ("100000", "100000", "77000", "77000"),
                ("77000", "120000", "77000", "120000"),
            ),
            trade_port=port,
            ledger_factory=lambda: account,
            margin_model=margin_model(),
        ).run()

        self.assertFalse(result.completed)
        self.assertTrue(result.liquidated)
        self.assertFalse(result.bankrupt)
        self.assertEqual(result.termination_sequence, 1)
        self.assertEqual(
            [frame.sequence for frame in result.frames],
            [0, 1],
        )
        self.assertEqual(
            [snapshot.sequence for snapshot in result.margin_snapshots],
            [0, 1],
        )
        self.assertGreater(
            result.margin_snapshots[-1].margin_balance,
            Decimal("0"),
        )
        self.assertEqual(
            port.events,
            [
                ("initialize", 0),
                ("instructions", 1),
            ],
        )

    def test_post_fill_liquidation_keeps_fill_but_skips_notifications(
        self,
    ) -> None:
        account = ledger("0.001")
        opening = instruction("a-open", quantity="5")
        later = instruction("z-later", quantity="1")
        port = TracedLiquidatingTradePort(
            {1: (later, opening)}
        )

        result = SimulationRunner(
            source(
                ("100000", "100000", "100000", "100000"),
                ("100000", "100000", "83500", "83500"),
                ("83500", "90000", "83000", "90000"),
            ),
            trade_port=port,
            ledger_factory=lambda: account,
            margin_model=margin_model(),
        ).run()

        self.assertTrue(result.liquidated)
        self.assertFalse(result.bankrupt)
        self.assertEqual(
            [fill.instruction_key for fill in result.fills],
            ["a-open"],
        )
        self.assertEqual(
            [
                trade.instruction_key
                for trade in result.instructions
            ],
            ["a-open"],
        )
        self.assertEqual(
            result.final_positions,
            {INSTRUMENT: Decimal("5")},
        )
        self.assertEqual(port.fill_batches, [])
        self.assertEqual(
            port.events,
            [
                ("initialize", 0),
                ("instructions", 1),
            ],
        )
        self.assertEqual(len(result.intents), 2)
        self.assertEqual(
            [record.status for record in result.intents],
            [IntentStatus.WAITING, IntentStatus.WAITING],
        )
        self.assertTrue(
            all(
                record.active_to_sequence is None
                for record in result.intents
            )
        )

    def test_gap_beyond_bankruptcy_preserves_the_diagnostic(self) -> None:
        account = ledger("0.003")
        seed_long(account)

        result = SimulationRunner(
            source(
                ("100000", "100000", "100000", "100000"),
                ("100000", "100000", "70000", "70000"),
            ),
            trade_port=RecordingTradePort(),
            ledger_factory=lambda: account,
            margin_model=margin_model(),
        ).run()

        self.assertTrue(result.liquidated)
        self.assertTrue(result.bankrupt)
        self.assertTrue(result.account_events[0].bankrupt)
        self.assertLessEqual(
            result.margin_snapshots[-1].margin_balance,
            Decimal("0"),
        )

    def test_healthy_margin_run_completes_normally(self) -> None:
        account = ledger("0.003")
        seed_long(account)
        port = RecordingTradePort()

        result = SimulationRunner(
            source(
                ("100000", "100000", "100000", "100000"),
                ("100000", "100000", "80000", "80000"),
                ("80000", "90000", "80000", "90000"),
            ),
            trade_port=port,
            ledger_factory=lambda: account,
            margin_model=margin_model(),
        ).run()

        self.assertTrue(result.completed)
        self.assertFalse(result.liquidated)
        self.assertFalse(result.bankrupt)
        self.assertIsNone(result.termination_reason)
        self.assertIsNone(result.termination_sequence)
        self.assertEqual(result.account_events, ())
        self.assertEqual(
            [snapshot.sequence for snapshot in result.margin_snapshots],
            [0, 1, 2],
        )
        self.assertEqual(
            port.events,
            [
                ("initialize", 0),
                ("instructions", 1),
                ("market", 1),
                ("instructions", 2),
                ("market", 2),
            ],
        )

    def test_adverse_extreme_catches_recovered_long_at_low(self) -> None:
        account = ledger("0.003")
        seed_long(account)
        port = RecordingTradePort()

        result = SimulationRunner(
            source(
                ("100000", "100000", "100000", "100000"),
                ("100000", "105000", "77000", "100000"),
                ("100000", "100000", "100000", "100000"),
            ),
            trade_port=port,
            ledger_factory=lambda: account,
            margin_model=margin_model(),
            mark_price_sampling=MarkPriceSampling.ADVERSE_EXTREME,
        ).run()

        self.assertTrue(result.liquidated)
        self.assertFalse(result.bankrupt)
        self.assertEqual(result.termination_sequence, 1)
        event = result.account_events[0]
        self.assertEqual(
            event.mark_price_sampling,
            MarkPriceSampling.ADVERSE_EXTREME,
        )
        self.assertEqual(event.snapshot.mark_price, Decimal("77000"))
        self.assertFalse(event.intrabar_ordering_ambiguous)
        self.assertEqual(
            result.equity_curve[-1].marks[INSTRUMENT],
            Decimal("77000"),
        )
        self.assertEqual(
            port.events,
            [
                ("initialize", 0),
                ("instructions", 1),
            ],
        )

    def test_adverse_extreme_applies_to_seeded_first_frame(self) -> None:
        account = ledger("0.003")
        seed_long(account)
        port = RecordingTradePort()

        result = SimulationRunner(
            source(
                ("100000", "105000", "77000", "100000"),
                ("100000", "100000", "100000", "100000"),
            ),
            trade_port=port,
            ledger_factory=lambda: account,
            margin_model=margin_model(),
            mark_price_sampling=MarkPriceSampling.ADVERSE_EXTREME,
        ).run()

        self.assertTrue(result.liquidated)
        self.assertEqual(result.termination_sequence, 0)
        self.assertEqual(
            result.account_events[0].snapshot.mark_price,
            Decimal("77000"),
        )
        self.assertFalse(
            result.account_events[0].intrabar_ordering_ambiguous
        )
        self.assertEqual(port.events, [])

    def test_close_only_ignores_recovered_intrabar_touch(self) -> None:
        account = ledger("0.003")
        seed_long(account)

        result = SimulationRunner(
            source(
                ("100000", "100000", "100000", "100000"),
                ("100000", "105000", "77000", "100000"),
                ("100000", "100000", "100000", "100000"),
            ),
            trade_port=RecordingTradePort(),
            ledger_factory=lambda: account,
            margin_model=margin_model(),
            mark_price_sampling=MarkPriceSampling.CLOSE_ONLY,
        ).run()

        self.assertTrue(result.completed)
        self.assertFalse(result.liquidated)
        self.assertEqual(
            [snapshot.mark_price for snapshot in result.margin_snapshots],
            [
                Decimal("100000"),
                Decimal("100000"),
                Decimal("100000"),
            ],
        )

    def test_adverse_extreme_catches_recovered_short_at_high(self) -> None:
        account = ledger("0.003")
        seed_short(account)

        result = SimulationRunner(
            source(
                ("100000", "100000", "100000", "100000"),
                ("100000", "142500", "95000", "100000"),
            ),
            trade_port=RecordingTradePort(),
            ledger_factory=lambda: account,
            margin_model=margin_model(),
            mark_price_sampling=MarkPriceSampling.ADVERSE_EXTREME,
        ).run()

        self.assertTrue(result.liquidated)
        self.assertFalse(result.bankrupt)
        self.assertEqual(
            result.account_events[0].snapshot.mark_price,
            Decimal("142500"),
        )
        self.assertEqual(
            result.final_positions,
            {INSTRUMENT: Decimal("-10")},
        )

    def test_opening_gap_liquidates_before_current_bar_instructions(
        self,
    ) -> None:
        account = ledger("0.003")
        seed_long(account)
        port = RecordingTradePort(
            {1: (instruction("never-issued", quantity="1"),)}
        )

        result = SimulationRunner(
            source(
                ("100000", "100000", "100000", "100000"),
                ("77000", "100000", "76000", "100000"),
            ),
            trade_port=port,
            ledger_factory=lambda: account,
            margin_model=margin_model(),
            mark_price_sampling=MarkPriceSampling.ADVERSE_EXTREME,
        ).run()

        self.assertTrue(result.liquidated)
        self.assertFalse(result.bankrupt)
        self.assertEqual(result.fills, ())
        self.assertEqual(result.instructions, ())
        self.assertEqual(
            result.account_events[0].snapshot.mark_price,
            Decimal("77000"),
        )
        self.assertFalse(
            result.account_events[0].intrabar_ordering_ambiguous
        )
        self.assertEqual(port.events, [("initialize", 0)])

    def test_existing_position_and_same_bar_fill_are_ambiguous(
        self,
    ) -> None:
        account = ledger("0.003")
        seed_long(account)
        close_position = instruction(
            "close-position",
            quantity="10",
            side=OrderSide.SELL,
            reduce_only=True,
        )
        port = RecordingTradePort({1: (close_position,)})

        result = SimulationRunner(
            source(
                ("100000", "100000", "100000", "100000"),
                ("100000", "105000", "77000", "100000"),
            ),
            trade_port=port,
            ledger_factory=lambda: account,
            margin_model=margin_model(),
            mark_price_sampling=MarkPriceSampling.ADVERSE_EXTREME,
        ).run()

        self.assertTrue(result.liquidated)
        self.assertTrue(
            result.account_events[0].intrabar_ordering_ambiguous
        )
        self.assertEqual(result.fills, ())
        self.assertEqual(result.instructions, ())
        self.assertEqual(
            result.final_positions,
            {INSTRUMENT: Decimal("10")},
        )
        self.assertEqual(
            port.events,
            [
                ("initialize", 0),
                ("instructions", 1),
            ],
        )

    def test_new_fill_and_same_bar_extreme_are_ambiguous(self) -> None:
        account = ledger("0.001")
        opening = instruction("open-position", quantity="5")
        port = RecordingTradePort({1: (opening,)})

        result = SimulationRunner(
            source(
                ("100000", "100000", "100000", "100000"),
                ("100000", "100000", "83600", "100000"),
            ),
            trade_port=port,
            ledger_factory=lambda: account,
            margin_model=margin_model(),
            mark_price_sampling=MarkPriceSampling.ADVERSE_EXTREME,
        ).run()

        self.assertTrue(result.liquidated)
        self.assertFalse(result.bankrupt)
        self.assertTrue(
            result.account_events[0].intrabar_ordering_ambiguous
        )
        self.assertEqual(
            [fill.instruction_key for fill in result.fills],
            ["open-position"],
        )
        self.assertEqual(
            result.account_events[0].snapshot.mark_price,
            Decimal("83600"),
        )
        self.assertEqual(
            result.final_positions,
            {INSTRUMENT: Decimal("5")},
        )
        self.assertEqual(
            port.events,
            [
                ("initialize", 0),
                ("instructions", 1),
            ],
        )


if __name__ == "__main__":
    unittest.main()
