from __future__ import annotations

import unittest
from decimal import Decimal

from market_protocol import MarketFrame
from simulation_runtime import (
    FlatMaintenanceMarginSchedule,
    LinearLedger,
    MaintenanceMarginTier,
    MarginConfig,
    NoMarginModel,
    TieredMaintenanceMarginSchedule,
)


def frame() -> MarketFrame:
    return MarketFrame(
        sequence=1,
        timestamp=1,
        instrument="BTCUSDT",
        open=Decimal("100000"),
        high=Decimal("100000"),
        low=Decimal("100000"),
        close=Decimal("100000"),
    )


class FlatMaintenanceMarginScheduleTests(unittest.TestCase):
    def test_requirement_is_returned_in_the_notional_asset(self) -> None:
        schedule = FlatMaintenanceMarginSchedule(Decimal("0.005"))

        self.assertEqual(
            schedule.requirement(
                position_notional=Decimal("10000"),
            ),
            Decimal("50.000"),
        )

    def test_rejects_invalid_rate_and_negative_notional(self) -> None:
        for rate in (Decimal("-0.001"), Decimal("1")):
            with self.subTest(rate=rate):
                with self.assertRaises(ValueError):
                    FlatMaintenanceMarginSchedule(rate)

        schedule = FlatMaintenanceMarginSchedule(Decimal("0.005"))
        with self.assertRaises(ValueError):
            schedule.requirement(
                position_notional=Decimal("-1"),
            )


def maintenance_tier(
    floor: str,
    cap: str | None,
    rate: str,
    deduction: str,
) -> MaintenanceMarginTier:
    return MaintenanceMarginTier(
        notional_floor=Decimal(floor),
        notional_cap=None if cap is None else Decimal(cap),
        maintenance_margin_rate=Decimal(rate),
        maintenance_amount_deduction=Decimal(deduction),
    )


def tiered_schedule(
    *tiers: MaintenanceMarginTier,
) -> TieredMaintenanceMarginSchedule:
    return TieredMaintenanceMarginSchedule(
        product="COIN-M",
        instrument="BTCUSD_PERP",
        source="versioned-test-fixture",
        effective_at="2026-07-26",
        version="test-v1",
        content_hash="sha256:test",
        tiers=tiers,
    )


class TieredMaintenanceMarginScheduleTests(unittest.TestCase):
    def test_selects_contiguous_tiers_and_applies_deduction(self) -> None:
        first = maintenance_tier("0", "10000", "0.005", "0")
        second = maintenance_tier(
            "10000",
            "50000",
            "0.01",
            "50",
        )
        third = maintenance_tier(
            "50000",
            None,
            "0.02",
            "550",
        )
        schedule = tiered_schedule(first, second, third)

        self.assertEqual(
            schedule.requirement(position_notional=Decimal("0")),
            Decimal("0"),
        )
        self.assertIs(
            schedule.tier_for(Decimal("10000")),
            first,
        )
        self.assertIs(
            schedule.tier_for(Decimal("10000.01")),
            second,
        )
        self.assertEqual(
            schedule.requirement(
                position_notional=Decimal("10000"),
            ),
            Decimal("50.000"),
        )
        self.assertEqual(
            schedule.requirement(
                position_notional=Decimal("20000"),
            ),
            Decimal("150.00"),
        )
        self.assertEqual(
            schedule.requirement(
                position_notional=Decimal("50000"),
            ),
            Decimal("450.00"),
        )
        self.assertEqual(
            schedule.requirement(
                position_notional=Decimal("60000"),
            ),
            Decimal("650.00"),
        )

    def test_requires_versioned_provenance_metadata(self) -> None:
        values = {
            "product": "COIN-M",
            "instrument": "BTCUSD_PERP",
            "source": "fixture",
            "effective_at": "2026-07-26",
            "version": "v1",
            "content_hash": "sha256:test",
        }
        tier = maintenance_tier("0", None, "0.005", "0")

        for name in values:
            with self.subTest(name=name):
                invalid = dict(values)
                invalid[name] = " "
                with self.assertRaises(ValueError):
                    TieredMaintenanceMarginSchedule(
                        **invalid,
                        tiers=(tier,),
                    )

    def test_rejects_invalid_tier_values(self) -> None:
        invalid_values = (
            ("-1", "10", "0.005", "0"),
            ("10", "10", "0.005", "0"),
            ("0", "10", "-0.001", "0"),
            ("0", "10", "1", "0"),
            ("0", "10", "0.005", "-1"),
        )
        for values in invalid_values:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    maintenance_tier(*values)

    def test_rejects_gaps_decreasing_rates_and_discontinuity(
        self,
    ) -> None:
        valid_first = maintenance_tier(
            "0",
            "10000",
            "0.005",
            "0",
        )
        invalid_following_tiers = (
            maintenance_tier(
                "11000",
                None,
                "0.01",
                "60",
            ),
            maintenance_tier(
                "10000",
                None,
                "0.004",
                "0",
            ),
            maintenance_tier(
                "10000",
                None,
                "0.01",
                "0",
            ),
        )
        for following in invalid_following_tiers:
            with self.subTest(following=following):
                with self.assertRaises(ValueError):
                    tiered_schedule(valid_first, following)

        with self.assertRaises(ValueError):
            tiered_schedule(
                maintenance_tier("0", None, "0.005", "0"),
                maintenance_tier(
                    "10000",
                    None,
                    "0.01",
                    "50",
                ),
            )
        with self.assertRaises(ValueError):
            tiered_schedule(
                maintenance_tier("0", None, "0.005", "1"),
            )

    def test_finite_final_cap_is_enforced(self) -> None:
        schedule = tiered_schedule(
            maintenance_tier("0", "10000", "0.005", "0"),
        )

        with self.assertRaises(ValueError):
            schedule.requirement(
                position_notional=Decimal("10000.01"),
            )


class MarginConfigTests(unittest.TestCase):
    def test_initial_margin_rate_is_derived_only_from_leverage(self) -> None:
        config = MarginConfig(
            leverage=Decimal("5"),
            maintenance_schedule=FlatMaintenanceMarginSchedule(
                Decimal("0.005")
            ),
        )

        self.assertEqual(config.initial_margin_rate, Decimal("0.2"))

    def test_rejects_nonpositive_leverage_and_invalid_schedule(self) -> None:
        schedule = FlatMaintenanceMarginSchedule(Decimal("0.005"))
        for leverage in (Decimal("0"), Decimal("-1")):
            with self.subTest(leverage=leverage):
                with self.assertRaises(ValueError):
                    MarginConfig(
                        leverage=leverage,
                        maintenance_schedule=schedule,
                    )

        with self.assertRaises(TypeError):
            MarginConfig(
                leverage=Decimal("5"),
                maintenance_schedule=object(),  # type: ignore[arg-type]
            )


class NoMarginModelTests(unittest.TestCase):
    def test_returns_no_snapshot_for_non_margin_probe(self) -> None:
        snapshot = NoMarginModel().snapshot(
            LinearLedger(Decimal("1000")),
            mark_price=Decimal("100000"),
            frame=frame(),
        )

        self.assertIsNone(snapshot)


if __name__ == "__main__":
    unittest.main()
