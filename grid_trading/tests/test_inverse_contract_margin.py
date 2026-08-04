from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from decimal import Decimal, localcontext
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SIMULATOR_ROOT = PROJECT_ROOT.parent / "market_simulator"
for package_path in (
    PROJECT_ROOT,
    SIMULATOR_ROOT / "packages" / "market_protocol" / "src",
    SIMULATOR_ROOT / "packages" / "simulation_runtime" / "src",
):
    sys.path.insert(0, str(package_path))

from grid_rule.adapters import (  # noqa: E402
    InverseContractLedger,
    InverseContractMarginModel,
)
from market_protocol import MarketFrame  # noqa: E402
from simulation_runtime import (  # noqa: E402
    FlatMaintenanceMarginSchedule,
    LiquidityRole,
    MaintenanceMarginTier,
    MarginConfig,
    OrderSide,
    SimFill,
    TieredMaintenanceMarginSchedule,
    TradeIntentMode,
)


INSTRUMENT = "BTCUSD_PERP"
CONTRACT_SIZE = Decimal("100")
MARK = Decimal("80000")


def market_frame(
    mark: Decimal = MARK,
    *,
    instrument: str = INSTRUMENT,
) -> MarketFrame:
    return MarketFrame(
        sequence=7,
        timestamp=7000,
        instrument=instrument,
        open=mark,
        high=mark,
        low=mark,
        close=mark,
    )


def fill(
    key: str,
    side: OrderSide,
    price: str,
    quantity: str,
    *,
    fee_amount: str = "0",
) -> SimFill:
    fee = Decimal(fee_amount)
    return SimFill(
        fill_id=f"fill:{key}",
        instruction_key=f"instruction:{key}",
        source_intent_key=f"intent:{key}",
        intent_mode=TradeIntentMode.ACTIVE,
        instrument=INSTRUMENT,
        side=side,
        price=Decimal(price),
        quantity=Decimal(quantity),
        sequence=1,
        timestamp=1,
        liquidity_role=LiquidityRole.TAKER,
        fee_rate=Decimal("0"),
        fee_amount=fee,
        fee_asset="BTC",
        reduce_only=False,
    )


def ledger(
    *,
    spot_btc: str = "1",
    futures_wallet_btc: str = "0.1",
) -> InverseContractLedger:
    return InverseContractLedger(
        instrument=INSTRUMENT,
        contract_size=CONTRACT_SIZE,
        spot_base_balance=Decimal(spot_btc),
        futures_wallet_balance=Decimal(futures_wallet_btc),
    )


def model(
    leverage: str = "5",
    *,
    maintenance_schedule: object | None = None,
) -> InverseContractMarginModel:
    schedule = (
        FlatMaintenanceMarginSchedule(Decimal("0.005"))
        if maintenance_schedule is None
        else maintenance_schedule
    )
    return InverseContractMarginModel(
        MarginConfig(
            leverage=Decimal(leverage),
            maintenance_schedule=schedule,  # type: ignore[arg-type]
        )
    )


def tiered_schedule() -> TieredMaintenanceMarginSchedule:
    return TieredMaintenanceMarginSchedule(
        product="COIN-M",
        instrument=INSTRUMENT,
        source="versioned-test-fixture",
        effective_at="2026-07-26",
        version="test-v1",
        content_hash="sha256:test",
        tiers=(
            MaintenanceMarginTier(
                notional_floor=Decimal("0"),
                notional_cap=Decimal("10000"),
                maintenance_margin_rate=Decimal("0.005"),
                maintenance_amount_deduction=Decimal("0"),
            ),
            MaintenanceMarginTier(
                notional_floor=Decimal("10000"),
                notional_cap=Decimal("50000"),
                maintenance_margin_rate=Decimal("0.01"),
                maintenance_amount_deduction=Decimal("50"),
            ),
            MaintenanceMarginTier(
                notional_floor=Decimal("50000"),
                notional_cap=None,
                maintenance_margin_rate=Decimal("0.02"),
                maintenance_amount_deduction=Decimal("550"),
            ),
        ),
    )


class InverseContractMarginModelTests(unittest.TestCase):
    def test_empty_account_has_no_position_margin_or_liquidation(self) -> None:
        account = ledger()

        snapshot = model().snapshot(
            account,
            mark_price=MARK,
            frame=market_frame(),
        )

        self.assertEqual(snapshot.position_quantity, Decimal("0"))
        self.assertEqual(snapshot.position_notional, Decimal("0"))
        self.assertEqual(snapshot.wallet_balance, Decimal("0.1"))
        self.assertEqual(snapshot.unrealized_pnl, Decimal("0"))
        self.assertEqual(snapshot.margin_balance, Decimal("0.1"))
        self.assertEqual(
            snapshot.position_initial_margin,
            Decimal("0"),
        )
        self.assertEqual(snapshot.maintenance_margin, Decimal("0"))
        self.assertEqual(snapshot.available_balance, Decimal("0.1"))
        self.assertEqual(snapshot.margin_buffer, Decimal("0.1"))
        self.assertIsNone(snapshot.initial_margin_utilization)
        self.assertIsNone(snapshot.maintenance_margin_utilization)
        self.assertIsNone(snapshot.effective_leverage)
        self.assertIsNone(snapshot.estimated_liquidation_price)
        self.assertFalse(snapshot.liquidation_triggered)
        self.assertFalse(snapshot.bankrupt)

    def test_long_snapshot_matches_hand_calculated_inverse_formulas(
        self,
    ) -> None:
        account = ledger()
        account.apply(fill("open", OrderSide.BUY, "100000", "2"))

        snapshot = model().snapshot(
            account,
            mark_price=MARK,
            frame=market_frame(),
            mark_price_source="market_ohlc_proxy",
        )

        with localcontext() as context:
            context.prec = 50
            notional = Decimal("200")
            unrealized = notional * (
                Decimal("1") / Decimal("100000")
                - Decimal("1") / MARK
            )
            margin_balance = Decimal("0.1") + unrealized
            initial_margin = notional / MARK / Decimal("5")
            maintenance_margin = (
                notional * Decimal("0.005") / MARK
            )
            initial_utilization = initial_margin / margin_balance
            maintenance_utilization = (
                maintenance_margin / margin_balance
            )
            effective_leverage = (
                notional / (margin_balance * MARK)
            )

        self.assertEqual(snapshot.settlement_asset, "BTC")
        self.assertEqual(snapshot.notional_asset, "USD")
        self.assertEqual(snapshot.position_unit, "CONTRACT")
        self.assertEqual(snapshot.mark_price_source, "market_ohlc_proxy")
        self.assertEqual(snapshot.position_quantity, Decimal("2"))
        self.assertEqual(snapshot.average_entry_price, Decimal("100000"))
        self.assertEqual(snapshot.position_notional, notional)
        self.assertEqual(snapshot.unrealized_pnl, unrealized)
        self.assertEqual(snapshot.margin_balance, margin_balance)
        self.assertEqual(
            snapshot.position_initial_margin,
            initial_margin,
        )
        self.assertEqual(
            snapshot.maintenance_margin,
            maintenance_margin,
        )
        self.assertEqual(
            snapshot.available_balance,
            margin_balance - initial_margin,
        )
        self.assertEqual(
            snapshot.margin_buffer,
            margin_balance - maintenance_margin,
        )
        self.assertEqual(
            snapshot.initial_margin_utilization,
            initial_utilization,
        )
        self.assertEqual(
            snapshot.maintenance_margin_utilization,
            maintenance_utilization,
        )
        self.assertEqual(
            snapshot.effective_leverage,
            effective_leverage,
        )

    def test_short_snapshot_uses_the_opposite_pnl_direction(self) -> None:
        account = ledger()
        account.apply(fill("open", OrderSide.SELL, "120000", "3"))
        mark = Decimal("150000")

        snapshot = model().snapshot(
            account,
            mark_price=mark,
            frame=market_frame(mark),
        )

        with localcontext() as context:
            context.prec = 50
            expected = -Decimal("300") * (
                Decimal("1") / Decimal("120000")
                - Decimal("1") / mark
            )
        self.assertEqual(snapshot.unrealized_pnl, expected)
        self.assertLess(snapshot.unrealized_pnl, 0)

    def test_fees_reduce_wallet_and_every_margin_balance_derived_from_it(
        self,
    ) -> None:
        account = ledger()
        account.apply(
            fill(
                "open",
                OrderSide.BUY,
                "100000",
                "2",
                fee_amount="0.001",
            )
        )

        snapshot = model().snapshot(
            account,
            mark_price=Decimal("100000"),
            frame=market_frame(Decimal("100000")),
        )

        self.assertEqual(snapshot.wallet_balance, Decimal("0.099"))
        self.assertEqual(snapshot.margin_balance, Decimal("0.099"))

    def test_leverage_changes_initial_margin_but_not_pnl_or_maintenance(
        self,
    ) -> None:
        account = ledger()
        account.apply(fill("open", OrderSide.BUY, "100000", "2"))
        current_frame = market_frame()

        five_x = model("5").snapshot(
            account,
            mark_price=MARK,
            frame=current_frame,
        )
        ten_x = model("10").snapshot(
            account,
            mark_price=MARK,
            frame=current_frame,
        )

        self.assertEqual(five_x.unrealized_pnl, ten_x.unrealized_pnl)
        self.assertEqual(five_x.margin_balance, ten_x.margin_balance)
        self.assertEqual(
            five_x.maintenance_margin,
            ten_x.maintenance_margin,
        )
        self.assertEqual(
            five_x.position_initial_margin,
            ten_x.position_initial_margin * Decimal("2"),
        )
        self.assertEqual(
            five_x.estimated_liquidation_price,
            ten_x.estimated_liquidation_price,
        )

    def test_spot_balance_never_changes_contract_margin_state(self) -> None:
        low_spot = ledger(spot_btc="1")
        high_spot = ledger(spot_btc="10")
        opening_fill = fill("open", OrderSide.BUY, "100000", "2")
        low_spot.apply(opening_fill)
        high_spot.apply(replace(opening_fill, fill_id="fill:other"))

        low_snapshot = model().snapshot(
            low_spot,
            mark_price=MARK,
            frame=market_frame(),
        )
        high_snapshot = model().snapshot(
            high_spot,
            mark_price=MARK,
            frame=market_frame(),
        )

        self.assertEqual(low_snapshot, high_snapshot)

    def test_larger_position_requires_more_maintenance_margin(self) -> None:
        small = ledger()
        large = ledger()
        small.apply(fill("small", OrderSide.BUY, "100000", "2"))
        large.apply(fill("large", OrderSide.BUY, "100000", "3"))

        small_snapshot = model().snapshot(
            small,
            mark_price=MARK,
            frame=market_frame(),
        )
        large_snapshot = model().snapshot(
            large,
            mark_price=MARK,
            frame=market_frame(),
        )

        self.assertGreater(
            large_snapshot.maintenance_margin,
            small_snapshot.maintenance_margin,
        )

    def test_favorable_mark_improves_long_margin_balance_and_buffer(
        self,
    ) -> None:
        account = ledger()
        account.apply(fill("open", OrderSide.BUY, "100000", "2"))

        adverse = model().snapshot(
            account,
            mark_price=Decimal("80000"),
            frame=market_frame(Decimal("80000")),
        )
        favorable = model().snapshot(
            account,
            mark_price=Decimal("120000"),
            frame=market_frame(Decimal("120000")),
        )

        self.assertGreater(
            favorable.unrealized_pnl,
            adverse.unrealized_pnl,
        )
        self.assertGreater(
            favorable.margin_balance,
            adverse.margin_balance,
        )
        self.assertGreater(
            favorable.margin_buffer,
            adverse.margin_buffer,
        )

    def test_long_liquidation_price_solves_the_margin_equation(
        self,
    ) -> None:
        account = ledger(futures_wallet_btc="0.003")
        account.apply(fill("open", OrderSide.BUY, "100000", "10"))
        margin_model = model()

        snapshot = margin_model.snapshot(
            account,
            mark_price=Decimal("100000"),
            frame=market_frame(Decimal("100000")),
        )

        with localcontext() as context:
            context.prec = 50
            expected = (
                Decimal("1000") + Decimal("5")
            ) / (
                Decimal("0.003")
                + Decimal("1000") / Decimal("100000")
            )
        self.assertEqual(
            snapshot.estimated_liquidation_price,
            expected,
        )

        at_liquidation = margin_model.snapshot(
            account,
            mark_price=expected,
            frame=market_frame(expected),
        )
        self.assertLessEqual(
            abs(at_liquidation.margin_buffer),
            Decimal("1e-48"),
        )
        below = margin_model.snapshot(
            account,
            mark_price=expected - Decimal("1"),
            frame=market_frame(expected - Decimal("1")),
        )
        above = margin_model.snapshot(
            account,
            mark_price=expected + Decimal("1"),
            frame=market_frame(expected + Decimal("1")),
        )
        self.assertTrue(below.liquidation_triggered)
        self.assertFalse(above.liquidation_triggered)

    def test_short_liquidation_price_is_above_entry(self) -> None:
        account = ledger(futures_wallet_btc="0.003")
        account.apply(fill("open", OrderSide.SELL, "100000", "10"))
        margin_model = model()

        snapshot = margin_model.snapshot(
            account,
            mark_price=Decimal("100000"),
            frame=market_frame(Decimal("100000")),
        )

        with localcontext() as context:
            context.prec = 50
            expected = (
                -Decimal("1000") + Decimal("5")
            ) / (
                Decimal("0.003")
                - Decimal("1000") / Decimal("100000")
            )
        self.assertEqual(
            snapshot.estimated_liquidation_price,
            expected,
        )
        self.assertGreater(expected, Decimal("100000"))

        below = margin_model.snapshot(
            account,
            mark_price=expected - Decimal("1"),
            frame=market_frame(expected - Decimal("1")),
        )
        above = margin_model.snapshot(
            account,
            mark_price=expected + Decimal("1"),
            frame=market_frame(expected + Decimal("1")),
        )
        self.assertFalse(below.liquidation_triggered)
        self.assertTrue(above.liquidation_triggered)

    def test_short_with_collateral_above_maximum_loss_has_no_price(
        self,
    ) -> None:
        account = ledger(futures_wallet_btc="0.02")
        account.apply(fill("open", OrderSide.SELL, "100000", "10"))

        snapshot = model().snapshot(
            account,
            mark_price=Decimal("100000"),
            frame=market_frame(Decimal("100000")),
        )

        self.assertIsNone(snapshot.estimated_liquidation_price)

    def test_tiered_deduction_is_used_in_liquidation_equation(
        self,
    ) -> None:
        account = ledger(futures_wallet_btc="0.05")
        account.apply(fill("open", OrderSide.BUY, "100000", "200"))
        margin_model = model(
            maintenance_schedule=tiered_schedule(),
        )

        snapshot = margin_model.snapshot(
            account,
            mark_price=Decimal("100000"),
            frame=market_frame(Decimal("100000")),
        )

        with localcontext() as context:
            context.prec = 50
            expected = (
                Decimal("20000") + Decimal("150")
            ) / (
                Decimal("0.05")
                + Decimal("20000") / Decimal("100000")
            )
        self.assertEqual(
            snapshot.maintenance_margin,
            Decimal("0.0015"),
        )
        self.assertEqual(
            snapshot.estimated_liquidation_price,
            expected,
        )

    def test_more_wallet_collateral_moves_liquidation_farther_away(
        self,
    ) -> None:
        low_wallet = ledger(futures_wallet_btc="0.003")
        high_wallet = ledger(futures_wallet_btc="0.004")
        opening_fill = fill("open", OrderSide.BUY, "100000", "10")
        low_wallet.apply(opening_fill)
        high_wallet.apply(
            replace(opening_fill, fill_id="fill:high-wallet")
        )

        low_snapshot = model().snapshot(
            low_wallet,
            mark_price=Decimal("100000"),
            frame=market_frame(Decimal("100000")),
        )
        high_snapshot = model().snapshot(
            high_wallet,
            mark_price=Decimal("100000"),
            frame=market_frame(Decimal("100000")),
        )

        self.assertLess(
            high_snapshot.estimated_liquidation_price,
            low_snapshot.estimated_liquidation_price,
        )

    def test_liquidation_triggers_at_maintenance_before_bankruptcy(
        self,
    ) -> None:
        for side, safe_mark, breached_mark in (
            (OrderSide.BUY, Decimal("100001"), Decimal("99999")),
            (OrderSide.SELL, Decimal("99999"), Decimal("100001")),
        ):
            with self.subTest(side=side):
                account = ledger(futures_wallet_btc="0.00005")
                account.apply(fill("open", side, "100000", "10"))
                margin_model = model()

                boundary = margin_model.snapshot(
                    account,
                    mark_price=Decimal("100000"),
                    frame=market_frame(Decimal("100000")),
                )
                safe = margin_model.snapshot(
                    account,
                    mark_price=safe_mark,
                    frame=market_frame(safe_mark),
                )
                breached = margin_model.snapshot(
                    account,
                    mark_price=breached_mark,
                    frame=market_frame(breached_mark),
                )

                self.assertEqual(
                    boundary.margin_balance,
                    boundary.maintenance_margin,
                )
                self.assertEqual(
                    boundary.estimated_liquidation_price,
                    Decimal("100000"),
                )
                self.assertTrue(boundary.liquidation_triggered)
                self.assertFalse(boundary.bankrupt)
                self.assertFalse(safe.liquidation_triggered)
                self.assertTrue(breached.liquidation_triggered)

    def test_underfunded_open_position_reports_liquidation_and_bankruptcy(
        self,
    ) -> None:
        account = ledger(futures_wallet_btc="0.00001")
        account.apply(fill("open", OrderSide.BUY, "100000", "10"))
        mark = Decimal("50000")

        snapshot = model().snapshot(
            account,
            mark_price=mark,
            frame=market_frame(mark),
        )

        self.assertLess(snapshot.margin_balance, 0)
        self.assertTrue(snapshot.liquidation_triggered)
        self.assertTrue(snapshot.bankrupt)
        self.assertIsNone(snapshot.initial_margin_utilization)
        self.assertIsNone(snapshot.maintenance_margin_utilization)
        self.assertIsNone(snapshot.effective_leverage)

    def test_snapshot_does_not_mutate_ledger(self) -> None:
        account = ledger()
        account.apply(fill("open", OrderSide.BUY, "100000", "2"))
        before = (
            account.futures_wallet_balance,
            account.position_quantity,
            account.average_entry_price,
            account.gross_realized_pnl,
            account.total_fees,
        )

        model().snapshot(
            account,
            mark_price=MARK,
            frame=market_frame(),
        )

        after = (
            account.futures_wallet_balance,
            account.position_quantity,
            account.average_entry_price,
            account.gross_realized_pnl,
            account.total_fees,
        )
        self.assertEqual(after, before)

    def test_rejects_invalid_mark_frame_or_ledger_type(self) -> None:
        account = ledger()

        with self.assertRaises(ValueError):
            model().snapshot(
                account,
                mark_price=Decimal("0"),
                frame=market_frame(),
            )
        with self.assertRaises(ValueError):
            model().snapshot(
                account,
                mark_price=MARK,
                frame=market_frame(instrument="ETHUSD_PERP"),
            )
        with self.assertRaises(TypeError):
            model().snapshot(  # type: ignore[arg-type]
                object(),
                mark_price=MARK,
                frame=market_frame(),
            )


if __name__ == "__main__":
    unittest.main()
