from __future__ import annotations

import unittest
from decimal import Decimal
from typing import Sequence

from market_protocol import MarketFrame
from market_simulator import FixedBarMarketSource
from simulation_runtime import (
    IntentSnapshot,
    IntentStatus,
    LinearLedger,
    OrderSide,
    ReduceOnlyViolationError,
    SimFill,
    SimulationRunner,
    TradeInstruction,
    TradeIntentMode,
)


def fixed_source(frame_count: int = 3) -> FixedBarMarketSource:
    return FixedBarMarketSource(
        "BTCUSD",
        [
            ("100", "101", "99", "100")
            for _ in range(frame_count)
        ],
    )


def instruction(
    instruction_key: str,
    frame_sequence: int,
    *,
    side: OrderSide = OrderSide.BUY,
    quantity: str = "1",
    price: str = "100",
    source_intent_key: str | None = None,
    intent_mode: TradeIntentMode = TradeIntentMode.ACTIVE,
    reduce_only: bool = False,
    instrument: str = "BTCUSD",
) -> TradeInstruction:
    return TradeInstruction(
        instruction_key=instruction_key,
        source_intent_key=source_intent_key or f"intent:{instruction_key}",
        instrument=instrument,
        side=side,
        quantity=Decimal(quantity),
        price=Decimal(price),
        frame_sequence=frame_sequence,
        intent_mode=intent_mode,
        reduce_only=reduce_only,
        tags={"test": "explicit-instruction"},
    )


class ScriptedTradePort:
    def __init__(
        self,
        instructions_by_sequence: dict[
            int,
            tuple[TradeInstruction, ...],
        ],
    ) -> None:
        self.instructions_by_sequence = instructions_by_sequence
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

    def on_fills(
        self,
        fills: Sequence[SimFill],
    ) -> None:
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


class TracedScriptedTradePort(ScriptedTradePort):
    def visible_intents(self) -> tuple[IntentSnapshot, ...]:
        return (
            IntentSnapshot(
                intent_key="visible-intent",
                instrument="BTCUSD",
                side=OrderSide.BUY,
                quantity=Decimal("1"),
                intent_mode=TradeIntentMode.ACTIVE,
            ),
        )


class TradeInstructionTests(unittest.TestCase):
    def test_model_validates_instruction_specific_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "instruction_key"):
            instruction("", 1)
        with self.assertRaisesRegex(ValueError, "source_intent_key"):
            instruction("bad-source", 1, source_intent_key=" ")
        with self.assertRaisesRegex(ValueError, "price"):
            instruction("bad-price", 1, price="0")
        with self.assertRaisesRegex(ValueError, "frame_sequence"):
            instruction("bad-sequence", -1)
        with self.assertRaisesRegex(TypeError, "intent_mode"):
            TradeInstruction(
                instruction_key="bad-mode",
                source_intent_key="intent:bad-mode",
                instrument="BTCUSD",
                side=OrderSide.BUY,
                quantity=Decimal("1"),
                price=Decimal("100"),
                frame_sequence=1,
                intent_mode="ACTIVE",  # type: ignore[arg-type]
            )


class TradeInstructionRunnerTests(unittest.TestCase):
    def test_runner_requires_a_trade_port(self) -> None:
        with self.assertRaisesRegex(TypeError, "trade_port"):
            SimulationRunner(fixed_source())  # type: ignore[call-arg]

    def test_explicit_price_is_applied_without_ohlc_matching(self) -> None:
        port = ScriptedTradePort(
            {
                1: (
                    instruction(
                        "trade-1",
                        1,
                        price="500",
                        source_intent_key="rsi:buy:1",
                    ),
                ),
            }
        )

        result = SimulationRunner(
            fixed_source(frame_count=2),
            trade_port=port,
            initial_equity=Decimal("1000"),
        ).run()

        self.assertEqual(len(result.fills), 1)
        fill = result.fills[0]
        self.assertEqual(fill.price, Decimal("500"))
        self.assertEqual(fill.instruction_key, "trade-1")
        self.assertEqual(fill.source_intent_key, "rsi:buy:1")
        self.assertEqual(fill.intent_mode, TradeIntentMode.ACTIVE)
        self.assertEqual(
            port.events,
            [
                ("initialize", 0),
                ("instructions", 1),
                ("fills", ("trade-1",)),
                ("market", 1),
            ],
        )

    def test_end_of_run_keeps_position_and_marks_it_at_last_close(
        self,
    ) -> None:
        source = FixedBarMarketSource(
            "BTCUSD",
            [
                ("100", "101", "99", "100"),
                ("100", "102", "98", "100"),
                ("118", "121", "117", "120"),
            ],
        )
        port = ScriptedTradePort(
            {
                1: (
                    instruction(
                        "entry",
                        1,
                        quantity="2",
                        price="100",
                    ),
                ),
            }
        )

        result = SimulationRunner(
            source,
            trade_port=port,
            initial_equity=Decimal("1000"),
        ).run()

        self.assertEqual(len(result.fills), 1)
        self.assertEqual(result.final_cash, Decimal("800"))
        self.assertEqual(
            result.final_positions,
            {"BTCUSD": Decimal("2")},
        )
        self.assertEqual(
            result.final_average_costs,
            {"BTCUSD": Decimal("100")},
        )
        self.assertEqual(result.realized_pnl, Decimal("0"))
        self.assertEqual(result.final_equity, Decimal("1040"))
        self.assertEqual(
            result.equity_curve[-1].marks["BTCUSD"],
            Decimal("120"),
        )

    def test_end_of_run_keeps_visible_intent_waiting(self) -> None:
        port = TracedScriptedTradePort({})

        result = SimulationRunner(
            fixed_source(frame_count=2),
            trade_port=port,
        ).run()

        self.assertEqual(result.fills, ())
        self.assertEqual(len(result.intents), 1)
        self.assertEqual(
            result.intents[0].status,
            IntentStatus.WAITING,
        )
        self.assertIsNone(result.intents[0].active_to_sequence)

    def test_same_bar_instructions_are_sorted_by_logical_key(self) -> None:
        port = ScriptedTradePort(
            {
                1: (
                    instruction("trade-b", 1, price="300"),
                    instruction("trade-a", 1, price="200"),
                ),
            }
        )

        result = SimulationRunner(
            fixed_source(frame_count=2),
            trade_port=port,
            initial_equity=Decimal("1000"),
        ).run()

        self.assertEqual(
            [fill.instruction_key for fill in result.fills],
            ["trade-a", "trade-b"],
        )
        self.assertEqual(
            [fill.price for fill in result.fills],
            [Decimal("200"), Decimal("300")],
        )

    def test_trace_snapshot_only_accepts_visible_instruction_source(
        self,
    ) -> None:
        port = TracedScriptedTradePort(
            {
                1: (
                    instruction(
                        "missing-source",
                        1,
                        source_intent_key="not-visible",
                    ),
                ),
            }
        )

        with self.assertRaisesRegex(
            ValueError,
            "instructions must reference visible intents: not-visible",
        ):
            SimulationRunner(
                fixed_source(frame_count=2),
                trade_port=port,
            ).run()

        self.assertEqual(port.fill_batches, [])

    def test_batch_is_rejected_before_side_effects_for_wrong_frame(
        self,
    ) -> None:
        port = ScriptedTradePort(
            {1: (instruction("stale", 0),)}
        )
        ledger = LinearLedger(Decimal("1000"))

        with self.assertRaisesRegex(
            ValueError,
            "frame_sequence=0, current_sequence=1",
        ):
            SimulationRunner(
                fixed_source(frame_count=2),
                trade_port=port,
                ledger_factory=lambda: ledger,
            ).run()

        self.assertEqual(ledger.positions, {})
        self.assertEqual(ledger.cash, Decimal("1000"))
        self.assertEqual(port.fill_batches, [])

    def test_batch_is_rejected_for_wrong_instrument(self) -> None:
        port = ScriptedTradePort(
            {
                1: (
                    instruction(
                        "wrong-instrument",
                        1,
                        instrument="ETHUSD",
                    ),
                ),
            }
        )

        with self.assertRaisesRegex(
            ValueError,
            "current_instrument=BTCUSD",
        ):
            SimulationRunner(
                fixed_source(frame_count=2),
                trade_port=port,
            ).run()

    def test_duplicate_and_reused_instruction_keys_fail_fast(self) -> None:
        duplicate_port = ScriptedTradePort(
            {
                1: (
                    instruction("duplicate", 1),
                    instruction("duplicate", 1, price="101"),
                ),
            }
        )
        with self.assertRaisesRegex(
            ValueError,
            "duplicate instruction keys: duplicate",
        ):
            SimulationRunner(
                fixed_source(frame_count=2),
                trade_port=duplicate_port,
            ).run()

        reused_port = ScriptedTradePort(
            {
                1: (instruction("reused", 1),),
                2: (instruction("reused", 2),),
            }
        )
        with self.assertRaisesRegex(
            ValueError,
            "instruction keys must not be reused: reused",
        ):
            SimulationRunner(
                fixed_source(frame_count=3),
                trade_port=reused_port,
            ).run()
        self.assertEqual(len(reused_port.fill_batches), 1)

    def test_reduce_only_instruction_reduces_current_position(self) -> None:
        port = ScriptedTradePort(
            {
                1: (
                    instruction(
                        "entry",
                        1,
                        quantity="2",
                        price="100",
                    ),
                ),
                2: (
                    instruction(
                        "exit",
                        2,
                        side=OrderSide.SELL,
                        quantity="1",
                        price="110",
                        intent_mode=TradeIntentMode.PASSIVE,
                        reduce_only=True,
                    ),
                ),
            }
        )

        result = SimulationRunner(
            fixed_source(frame_count=3),
            trade_port=port,
            initial_equity=Decimal("1000"),
        ).run()

        self.assertEqual(
            result.final_positions,
            {"BTCUSD": Decimal("1")},
        )
        self.assertEqual(result.final_cash, Decimal("910"))
        self.assertEqual(len(port.fill_batches), 2)

    def test_reduce_only_instruction_can_close_a_short_position(
        self,
    ) -> None:
        port = ScriptedTradePort(
            {
                1: (
                    instruction(
                        "short-entry",
                        1,
                        side=OrderSide.SELL,
                        quantity="2",
                    ),
                ),
                2: (
                    instruction(
                        "short-exit",
                        2,
                        side=OrderSide.BUY,
                        quantity="2",
                        reduce_only=True,
                    ),
                ),
            }
        )

        result = SimulationRunner(
            fixed_source(frame_count=3),
            trade_port=port,
        ).run()

        self.assertEqual(result.final_positions, {})
        self.assertEqual(len(port.fill_batches), 2)

    def test_same_bar_reduce_only_uses_stable_sequential_position(
        self,
    ) -> None:
        port = ScriptedTradePort(
            {
                1: (
                    instruction(
                        "entry",
                        1,
                        quantity="3",
                    ),
                ),
                2: (
                    instruction(
                        "reduce-b",
                        2,
                        side=OrderSide.SELL,
                        quantity="2",
                        reduce_only=True,
                    ),
                    instruction(
                        "reduce-a",
                        2,
                        side=OrderSide.SELL,
                        quantity="1",
                        reduce_only=True,
                    ),
                ),
            }
        )

        result = SimulationRunner(
            fixed_source(frame_count=3),
            trade_port=port,
        ).run()

        self.assertEqual(
            [fill.instruction_key for fill in result.fills],
            ["entry", "reduce-a", "reduce-b"],
        )
        self.assertEqual(result.final_positions, {})

    def test_later_same_bar_reduce_only_checks_remaining_position(
        self,
    ) -> None:
        port = ScriptedTradePort(
            {
                1: (
                    instruction(
                        "entry",
                        1,
                        quantity="3",
                    ),
                ),
                2: (
                    instruction(
                        "reduce-b",
                        2,
                        side=OrderSide.SELL,
                        quantity="2",
                        reduce_only=True,
                    ),
                    instruction(
                        "reduce-a",
                        2,
                        side=OrderSide.SELL,
                        quantity="2",
                        reduce_only=True,
                    ),
                ),
            }
        )
        ledger = LinearLedger()

        with self.assertRaisesRegex(
            ReduceOnlyViolationError,
            (
                "instruction_key=reduce-b, "
                "source_intent_key=intent:reduce-b, "
                "instrument=BTCUSD, current_position=1, "
                "side=SELL, quantity=2"
            ),
        ):
            SimulationRunner(
                fixed_source(frame_count=3),
                trade_port=port,
                ledger_factory=lambda: ledger,
            ).run()

        self.assertEqual(
            ledger.positions,
            {"BTCUSD": Decimal("1")},
        )
        self.assertEqual(len(port.fill_batches), 1)

    def test_invalid_reduce_only_instruction_is_not_applied_or_reported(
        self,
    ) -> None:
        port = ScriptedTradePort(
            {
                1: (
                    instruction(
                        "entry",
                        1,
                        quantity="2",
                    ),
                ),
                2: (
                    instruction(
                        "invalid-exit",
                        2,
                        side=OrderSide.SELL,
                        quantity="3",
                        reduce_only=True,
                    ),
                ),
            }
        )
        ledger = LinearLedger(Decimal("1000"))

        with self.assertRaisesRegex(
            ReduceOnlyViolationError,
            (
                "instruction_key=invalid-exit, "
                "source_intent_key=intent:invalid-exit, "
                "instrument=BTCUSD, current_position=2, "
                "side=SELL, quantity=3"
            ),
        ):
            SimulationRunner(
                fixed_source(frame_count=3),
                trade_port=port,
                ledger_factory=lambda: ledger,
            ).run()

        self.assertEqual(
            ledger.positions,
            {"BTCUSD": Decimal("2")},
        )
        self.assertEqual(ledger.cash, Decimal("800"))
        self.assertEqual(len(port.fill_batches), 1)

    def test_non_reduce_only_instruction_preserves_net_crossing(self) -> None:
        port = ScriptedTradePort(
            {
                1: (instruction("buy", 1),),
                2: (
                    instruction(
                        "sell",
                        2,
                        side=OrderSide.SELL,
                        quantity="2",
                        price="110",
                    ),
                ),
            }
        )

        result = SimulationRunner(
            fixed_source(frame_count=3),
            trade_port=port,
        ).run()

        self.assertEqual(
            result.final_positions,
            {"BTCUSD": Decimal("-1")},
        )
        self.assertEqual(
            result.final_average_costs,
            {"BTCUSD": Decimal("110")},
        )


if __name__ == "__main__":
    unittest.main()
