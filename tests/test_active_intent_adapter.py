from __future__ import annotations

import unittest
from decimal import Decimal

from examples.intent_adapter_support import (
    ActiveTradeIntent,
    ExampleTradeIntentBook,
)
from examples.rsi_signal_probe import (
    RSI_BARS,
    RsiPositionState,
    RsiSignalSimulationAdapter,
    run_rsi_probe,
)
from market_protocol import MarketFrame
from market_simulator import FixedBarMarketSource
from simulation_runtime import (
    OrderSide,
    SimulationRunner,
    TradeIntentMode,
)


def frame(
    sequence: int,
    *,
    open_price: str,
    high: str = "200",
    low: str = "1",
    close: str = "100",
) -> MarketFrame:
    return MarketFrame(
        sequence=sequence,
        timestamp=sequence,
        instrument="BTCUSD",
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
    )


class ActiveIntentBookTests(unittest.TestCase):
    def test_active_intents_wait_use_next_open_sort_and_issue_once(
        self,
    ) -> None:
        book = ExampleTradeIntentBook()
        book.enqueue_active(
            (
                ActiveTradeIntent(
                    intent_key="z-entry",
                    instrument="BTCUSD",
                    side=OrderSide.BUY,
                    quantity=Decimal("1"),
                    tags={"role": "entry"},
                ),
                ActiveTradeIntent(
                    intent_key="a-exit",
                    instrument="BTCUSD",
                    side=OrderSide.SELL,
                    quantity=Decimal("1"),
                    reduce_only=True,
                    tags={"role": "exit"},
                ),
            ),
            current_sequence=1,
        )

        self.assertEqual(
            book.instructions_for(
                frame(1, open_price="91"),
            ),
            (),
        )

        instructions = book.instructions_for(
            frame(
                2,
                open_price="123",
                high="124",
                low="122",
                close="123",
            ),
        )

        self.assertEqual(
            [
                instruction.source_intent_key
                for instruction in instructions
            ],
            ["a-exit", "z-entry"],
        )
        self.assertEqual(
            [instruction.price for instruction in instructions],
            [Decimal("123"), Decimal("123")],
        )
        self.assertEqual(
            [instruction.reduce_only for instruction in instructions],
            [True, False],
        )
        self.assertEqual(
            {instruction.intent_mode for instruction in instructions},
            {TradeIntentMode.ACTIVE},
        )
        self.assertEqual(
            book.instructions_for(frame(3, open_price="150")),
            (),
        )

    def test_active_intent_key_cannot_be_queued_twice(self) -> None:
        book = ExampleTradeIntentBook()
        active = ActiveTradeIntent(
            intent_key="one-shot",
            instrument="BTCUSD",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
        )
        book.enqueue_active((active,), current_sequence=0)

        with self.assertRaisesRegex(
            ValueError,
            "intent keys must not be reused",
        ):
            book.enqueue_active((active,), current_sequence=1)


class RsiSignalSimulationTests(unittest.TestCase):
    def test_close_signals_execute_at_following_bar_open(self) -> None:
        result = run_rsi_probe()

        self.assertEqual(
            [
                (
                    fill.source_intent_key,
                    fill.side,
                    fill.price,
                    fill.sequence,
                    fill.tags["signal_sequence"],
                )
                for fill in result.fills
            ],
            [
                (
                    "rsi:entry:2",
                    OrderSide.BUY,
                    Decimal("81"),
                    3,
                    "2",
                ),
                (
                    "rsi:exit:3",
                    OrderSide.SELL,
                    Decimal("119"),
                    4,
                    "3",
                ),
            ],
        )
        self.assertNotEqual(result.fills[0].price, Decimal(RSI_BARS[2][0]))
        self.assertNotEqual(result.fills[1].price, Decimal(RSI_BARS[3][0]))
        self.assertEqual(
            [fill.intent_mode for fill in result.fills],
            [TradeIntentMode.ACTIVE, TradeIntentMode.ACTIVE],
        )
        self.assertEqual(result.final_positions, {})
        self.assertEqual(result.realized_pnl, Decimal("38"))
        self.assertEqual(result.final_equity, Decimal("1038"))

    def test_rsi_adapter_finishes_flat_after_reduce_only_exit(self) -> None:
        adapter = RsiSignalSimulationAdapter("BTCUSD")

        result = SimulationRunner(
            FixedBarMarketSource("BTCUSD", RSI_BARS),
            trade_port=adapter,
            initial_equity=Decimal("1000"),
        ).run()

        self.assertEqual(adapter.rule.state, RsiPositionState.FLAT)
        self.assertEqual(result.fills[-1].tags["role"], "exit")


if __name__ == "__main__":
    unittest.main()
