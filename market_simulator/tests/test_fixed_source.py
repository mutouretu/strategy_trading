from __future__ import annotations

import unittest
from decimal import Decimal

from market_simulator import FixedBarMarketSource, FixedSequenceMarketSource


class FixedSequenceMarketSourceTests(unittest.TestCase):
    def test_reset_replays_the_same_frames(self) -> None:
        source = FixedSequenceMarketSource(
            "BTCUSD",
            ["65000", "62000", "59000"],
            start_timestamp=100,
            step_milliseconds=10,
        )

        first = source.reset(seed=7)
        rest = source.next_batch(10)
        replay = source.reset(seed=99)

        self.assertEqual(first.price, Decimal("65000"))
        self.assertEqual(first.open, first.high)
        self.assertEqual(first.high, first.low)
        self.assertEqual(first.low, first.close)
        self.assertEqual([frame.price for frame in rest], [Decimal("62000"), Decimal("59000")])
        self.assertEqual(replay, first)
        self.assertEqual(rest[-1].timestamp, 120)
        self.assertTrue(source.done is False)

    def test_explicit_bar_source_preserves_ohlc(self) -> None:
        source = FixedBarMarketSource(
            "BTCUSD",
            [("100", "112", "95", "108")],
        )

        frame = source.reset()

        self.assertEqual(
            (frame.open, frame.high, frame.low, frame.close),
            (Decimal("100"), Decimal("112"), Decimal("95"), Decimal("108")),
        )
        self.assertTrue(source.done)


if __name__ == "__main__":
    unittest.main()
