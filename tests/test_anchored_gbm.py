from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from market_simulator import AnchoredGBMMarketSource


class AnchoredGBMMarketSourceTests(unittest.TestCase):
    def make_source(self) -> AnchoredGBMMarketSource:
        return AnchoredGBMMarketSource(
            "BTCUSD",
            [
                ("2026-01-01", "65000"),
                ("2026-01-10", "62000"),
                ("2026-01-20", "64000"),
            ],
            annual_volatility="0.60",
            intraday_steps=12,
        )

    @staticmethod
    def all_frames(source: AnchoredGBMMarketSource, seed: int):
        first = source.reset(seed)
        return (first, *source.next_batch(10_000))

    def test_seed_is_reproducible_and_other_seed_changes_path(self) -> None:
        source = self.make_source()

        first = self.all_frames(source, 42)
        replay = self.all_frames(source, 42)
        alternative = self.all_frames(source, 43)

        self.assertEqual(first, replay)
        self.assertNotEqual(
            [frame.close for frame in first[1:-1]],
            [frame.close for frame in alternative[1:-1]],
        )

    def test_anchor_closes_are_exact_and_dates_are_daily(self) -> None:
        frames = self.all_frames(self.make_source(), 42)
        by_date = {
            datetime.fromtimestamp(
                frame.timestamp / 1_000,
                tz=timezone.utc,
            ).date(): frame
            for frame in frames
        }

        self.assertEqual(len(frames), 20)
        self.assertEqual(by_date[date(2026, 1, 1)].close, Decimal("65000"))
        self.assertEqual(by_date[date(2026, 1, 10)].close, Decimal("62000"))
        self.assertEqual(by_date[date(2026, 1, 20)].close, Decimal("64000"))
        self.assertEqual(
            [frame.sequence for frame in frames],
            list(range(len(frames))),
        )

    def test_every_bar_has_valid_ohlc_and_continuous_open(self) -> None:
        frames = self.all_frames(self.make_source(), 42)

        for index, frame in enumerate(frames):
            self.assertLessEqual(frame.low, frame.open)
            self.assertLessEqual(frame.low, frame.close)
            self.assertGreaterEqual(frame.high, frame.open)
            self.assertGreaterEqual(frame.high, frame.close)
            if index:
                self.assertEqual(frame.open, frames[index - 1].close)

    def test_optional_price_bounds_constrain_every_ohlc_value(self) -> None:
        source = AnchoredGBMMarketSource(
            "BTCUSD",
            [
                ("2026-01-01", "65000"),
                ("2026-02-01", "40000"),
                ("2026-03-01", "200000"),
            ],
            annual_volatility="1.20",
            intraday_steps=24,
            price_floor="40000",
            price_ceiling="200000",
        )

        frames = self.all_frames(source, 42)

        for frame in frames:
            self.assertGreaterEqual(frame.low, Decimal("40000"))
            self.assertLessEqual(frame.high, Decimal("200000"))
        self.assertEqual(min(frame.close for frame in frames), Decimal("40000"))
        self.assertEqual(max(frame.close for frame in frames), Decimal("200000"))

    def test_anchor_outside_price_bounds_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "anchor prices must be >= price_floor",
        ):
            AnchoredGBMMarketSource(
                "BTCUSD",
                [("2026-01-01", "39000"), ("2026-02-01", "65000")],
                annual_volatility="0.60",
                price_floor="40000",
                price_ceiling="200000",
            )


if __name__ == "__main__":
    unittest.main()
