from __future__ import annotations

import json
import sys
import unittest
from decimal import Decimal
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
    MarginConfig,
)


FIXTURE = (
    PROJECT_ROOT
    / "tests"
    / "fixtures"
    / "binance_coinm_margin_official_zero_v1.json"
)


class CoinMMarginCalibrationTests(unittest.TestCase):
    def test_official_zero_position_account_fields_match(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        account_data = fixture["platform_account_asset"]
        position_data = fixture["platform_position"]
        mark = Decimal(fixture["mark_price_probe"])
        tolerance = Decimal(
            fixture["absolute_tolerances"]["BTC"]
        )
        ledger = InverseContractLedger(
            instrument=fixture["instrument"],
            contract_size=Decimal(fixture["contract_size"]),
            spot_base_balance=Decimal("0"),
            futures_wallet_balance=Decimal(
                account_data["walletBalance"]
            ),
        )
        model = InverseContractMarginModel(
            MarginConfig(
                leverage=Decimal(position_data["leverage"]),
                maintenance_schedule=(
                    FlatMaintenanceMarginSchedule(
                        Decimal(
                            fixture["maintenance_schedule"][
                                "maintenance_margin_rate"
                            ]
                        )
                    )
                ),
            )
        )
        frame = MarketFrame(
            sequence=0,
            timestamp=0,
            instrument=fixture["instrument"],
            open=mark,
            high=mark,
            low=mark,
            close=mark,
        )

        snapshot = model.snapshot(
            ledger,
            mark_price=mark,
            frame=frame,
            mark_price_source="official_docs_probe",
        )

        self.assertEqual(
            snapshot.notional_asset,
            fixture["notional_asset"],
        )
        comparisons = (
            (
                "wallet_balance",
                snapshot.wallet_balance,
                account_data["walletBalance"],
            ),
            (
                "unrealized_pnl",
                snapshot.unrealized_pnl,
                account_data["unrealizedProfit"],
            ),
            (
                "margin_balance",
                snapshot.margin_balance,
                account_data["marginBalance"],
            ),
            (
                "position_initial_margin",
                snapshot.position_initial_margin,
                account_data["positionInitialMargin"],
            ),
            (
                "maintenance_margin",
                snapshot.maintenance_margin,
                account_data["maintMargin"],
            ),
            (
                "available_balance",
                snapshot.available_balance,
                account_data["availableBalance"],
            ),
            (
                "position_quantity",
                snapshot.position_quantity,
                position_data["positionAmt"],
            ),
            (
                "position_notional",
                snapshot.position_notional,
                position_data["notionalValue"],
            ),
        )
        for field, actual, expected_text in comparisons:
            with self.subTest(field=field, expected=expected_text):
                self.assertLessEqual(
                    abs(actual - Decimal(expected_text)),
                    tolerance,
                )
        self.assertIsNone(snapshot.estimated_liquidation_price)
        self.assertFalse(snapshot.liquidation_triggered)
        self.assertFalse(snapshot.bankrupt)


if __name__ == "__main__":
    unittest.main()
