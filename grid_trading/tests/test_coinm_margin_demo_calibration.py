from __future__ import annotations

import json
import sys
import unittest
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
    MarginConfig,
    OrderSide,
    SimFill,
    TradeIntentMode,
)


LONG_FIXTURE = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "binance_coinm_margin_demo_aaveusd_perp_2026-07-28.json"
)
SHORT_FIXTURE = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "binance_coinm_margin_demo_aaveusd_perp_short_2026-07-28.json"
)


class CoinMMarginDemoCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.long_fixture = json.loads(
            LONG_FIXTURE.read_text(encoding="utf-8")
        )
        cls.short_fixture = json.loads(
            SHORT_FIXTURE.read_text(encoding="utf-8")
        )

    def test_aave_demo_long_snapshot_matches_model(self) -> None:
        self._assert_demo_snapshot_matches_model(
            self.long_fixture,
            expected_position_side="LONG",
        )

    def test_aave_demo_short_snapshot_matches_model(self) -> None:
        self._assert_demo_snapshot_matches_model(
            self.short_fixture,
            expected_position_side="SHORT",
        )

    def _assert_demo_snapshot_matches_model(
        self,
        fixture: dict,
        *,
        expected_position_side: str,
    ) -> None:
        account = fixture["platform_account_asset"]
        position = fixture["platform_account_position"]
        risk = fixture["platform_position_risk"]
        bracket = fixture["maintenance_schedule"][
            "raw_brackets"
        ][0]
        settlement_tolerance = Decimal(
            fixture["absolute_tolerances"][
                fixture["margin_asset"]
            ]
        )
        derived_tolerance = Decimal(
            fixture["capture_validation"][
                "derived_balance_tolerance"
            ]
        )
        price_tolerance = Decimal(
            fixture["absolute_tolerances"]["PRICE"]
        )

        self.assertEqual(fixture["source_kind"], "authenticated_account_capture")
        self.assertEqual(
            fixture["source_origin"],
            "https://testnet.binancefuture.com",
        )
        self.assertEqual(
            fixture["position_side"],
            expected_position_side,
        )
        self.assertFalse(position["isolated"])
        self.assertEqual(
            Decimal(position["openOrderInitialMargin"]),
            Decimal("0"),
        )
        self.assertEqual(int(bracket["bracket"]), 1)
        self.assertEqual(Decimal(str(bracket["cum"])), Decimal("0"))

        quantity = Decimal(position["positionAmt"])
        contract_size = Decimal(fixture["contract_size"])
        position_notional = abs(quantity) * contract_size
        entry_price = Decimal(position["entryPrice"])
        platform_unrealized = Decimal(
            account["unrealizedProfit"]
        )
        direction = Decimal("1") if quantity > 0 else Decimal("-1")
        with localcontext() as context:
            context.prec = 50
            account_mark = Decimal("1") / (
                Decimal("1") / entry_price
                - platform_unrealized
                / (direction * position_notional)
            )
            mark_from_initial_margin = (
                position_notional
                / Decimal(position["leverage"])
                / Decimal(account["positionInitialMargin"])
            )
            mark_from_maintenance_margin = (
                position_notional
                * Decimal(str(bracket["maintMarginRatio"]))
                / Decimal(account["maintMargin"])
            )

        # Account and positionRisk are sequential API responses. The account
        # response does not expose its mark, so infer it from inverse PnL and
        # require the independently implied margin marks to converge within
        # one platform price tick.
        self.assertLessEqual(
            abs(account_mark - mark_from_initial_margin),
            price_tolerance,
        )
        self.assertLessEqual(
            abs(account_mark - mark_from_maintenance_margin),
            price_tolerance,
        )

        ledger = InverseContractLedger(
            instrument=fixture["instrument"],
            contract_size=contract_size,
            spot_base_balance=Decimal("0"),
            futures_wallet_balance=Decimal(
                account["walletBalance"]
            ),
            base_asset=fixture["margin_asset"],
            quote_asset="USD",
            notional_asset=fixture["notional_asset"],
        )
        ledger.apply(
            SimFill(
                fill_id="demo-calibration-seed@0",
                instruction_key="demo-calibration-seed",
                source_intent_key="demo-calibration-position",
                intent_mode=TradeIntentMode.ACTIVE,
                instrument=fixture["instrument"],
                side=(
                    OrderSide.BUY
                    if quantity > 0
                    else OrderSide.SELL
                ),
                price=entry_price,
                quantity=abs(quantity),
                sequence=0,
                timestamp=fixture["request_window"][
                    "server_time_end"
                ],
                liquidity_role=LiquidityRole.TAKER,
                fee_rate=Decimal("0"),
                fee_amount=Decimal("0"),
                fee_asset=fixture["margin_asset"],
                reduce_only=False,
            )
        )
        model = InverseContractMarginModel(
            MarginConfig(
                leverage=Decimal(position["leverage"]),
                maintenance_schedule=(
                    FlatMaintenanceMarginSchedule(
                        Decimal(
                            str(bracket["maintMarginRatio"])
                        )
                    )
                ),
            )
        )
        frame = MarketFrame(
            sequence=0,
            timestamp=fixture["request_window"]["server_time_end"],
            instrument=fixture["instrument"],
            open=account_mark,
            high=account_mark,
            low=account_mark,
            close=account_mark,
        )

        snapshot = model.snapshot(
            ledger,
            mark_price=account_mark,
            frame=frame,
            mark_price_source="inferred_from_platform_account_pnl",
        )

        self.assertEqual(snapshot.position_quantity, quantity)
        self.assertEqual(snapshot.position_notional, position_notional)
        self.assertEqual(
            snapshot.notional_asset,
            fixture["notional_asset"],
        )
        account_comparisons = (
            (
                "unrealized_pnl",
                snapshot.unrealized_pnl,
                account["unrealizedProfit"],
            ),
            (
                "margin_balance",
                snapshot.margin_balance,
                account["marginBalance"],
            ),
            (
                "position_initial_margin",
                snapshot.position_initial_margin,
                account["positionInitialMargin"],
            ),
            (
                "maintenance_margin",
                snapshot.maintenance_margin,
                account["maintMargin"],
            ),
        )
        for field, actual, expected_text in account_comparisons:
            with self.subTest(field=field):
                self.assertLessEqual(
                    abs(actual - Decimal(expected_text)),
                    (
                        settlement_tolerance
                        if field
                        in {"unrealized_pnl", "margin_balance"}
                        else derived_tolerance
                    ),
                )
        self.assertLessEqual(
            abs(
                snapshot.available_balance
                - Decimal(account["availableBalance"])
            ),
            derived_tolerance,
        )
        platform_liquidation_price = Decimal(
            risk["liquidationPrice"]
        )
        if platform_liquidation_price == 0:
            # Binance uses zero as the no-finite-liquidation sentinel;
            # Runtime represents the same fact as None/null.
            self.assertIsNone(snapshot.estimated_liquidation_price)
        else:
            self.assertIsNotNone(
                snapshot.estimated_liquidation_price
            )
            self.assertLessEqual(
                abs(
                    snapshot.estimated_liquidation_price
                    - platform_liquidation_price
                ),
                price_tolerance,
            )


if __name__ == "__main__":
    unittest.main()
