from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.capture_coinm_margin_calibration import (  # noqa: E402
    build_fixture,
)


def capture_payloads() -> dict:
    symbol_info = {
        "symbol": "BTCUSD_PERP",
        "pair": "BTCUSD",
        "contractType": "PERPETUAL",
        "contractSize": 100,
        "baseAsset": "BTC",
        "quoteAsset": "USD",
        "marginAsset": "BTC",
        "pricePrecision": 1,
        "quantityPrecision": 0,
        "filters": [
            {
                "filterType": "PRICE_FILTER",
                "tickSize": "0.1",
            }
        ],
    }
    return {
        "exchange_info": {"symbols": [symbol_info]},
        "account": {
            "accountAlias": "must-not-be-exported",
            "assets": [
                {
                    "asset": "BTC",
                    "walletBalance": "0.01000000",
                    "unrealizedProfit": "-0.00010000",
                    "marginBalance": "0.00990000",
                    "maintMargin": "0.00004000",
                    "initialMargin": "0.00200000",
                    "positionInitialMargin": "0.00200000",
                    "openOrderInitialMargin": "0",
                    "availableBalance": "0.00790000",
                    "accountAlias": "must-not-be-exported",
                }
            ],
            "positions": [
                {
                    "symbol": "BTCUSD_PERP",
                    "positionSide": "BOTH",
                    "positionAmt": "10",
                    "entryPrice": "100000",
                    "unrealizedProfit": "-0.00010000",
                    "positionInitialMargin": "0.00200000",
                    "openOrderInitialMargin": "0",
                    "maintMargin": "0.00004000",
                    "leverage": "5",
                    "isolated": False,
                }
            ],
        },
        "position_risk": [
            {
                "symbol": "BTCUSD_PERP",
                "positionSide": "BOTH",
                "positionAmt": "10",
                "entryPrice": "100000",
                "markPrice": "99000",
                "unRealizedProfit": "-0.00010101",
                "liquidationPrice": "50300.0",
                "leverage": "5",
            }
        ],
        "leverage_brackets": [
            {
                "symbol": "BTCUSD_PERP",
                "notionalCoef": "1",
                "brackets": [
                    {
                        "bracket": 1,
                        "initialLeverage": 125,
                        "qtyCap": 500,
                        "qtyFloor": 0,
                        "maintMarginRatio": "0.004",
                        "cum": "0",
                    }
                ],
            }
        ],
    }


class CoinMMarginCalibrationCaptureTests(unittest.TestCase):
    def test_build_fixture_whitelists_and_versions_fields(self) -> None:
        payloads = capture_payloads()

        fixture = build_fixture(
            symbol="BTCUSD_PERP",
            base_url="https://dapi.binance.com",
            server_time_start=1000,
            server_time_end=1250,
            captured_at="2026-07-28T00:00:00+00:00",
            **payloads,
        )

        self.assertEqual(
            fixture["source_kind"],
            "authenticated_account_capture",
        )
        self.assertEqual(fixture["request_window"]["duration_ms"], 250)
        self.assertEqual(fixture["contract_size"], "100")
        self.assertEqual(fixture["notional_asset"], "USD")
        self.assertEqual(
            fixture["platform_position_risk"]["liquidationPrice"],
            "50300.0",
        )
        self.assertEqual(
            fixture["maintenance_schedule"]["raw_brackets"][0][
                "maintMarginRatio"
            ],
            "0.004",
        )
        serialized = str(fixture)
        self.assertNotIn("accountAlias", serialized)
        self.assertNotIn("must-not-be-exported", serialized)

    def test_zero_position_is_not_accepted_as_live_calibration(self) -> None:
        payloads = capture_payloads()
        payloads["account"]["positions"][0]["positionAmt"] = "0"
        payloads["position_risk"][0]["positionAmt"] = "0"

        with self.assertRaisesRegex(
            RuntimeError,
            "single non-zero side",
        ):
            build_fixture(
                symbol="BTCUSD_PERP",
                base_url="https://dapi.binance.com",
                server_time_start=1000,
                server_time_end=1250,
                captured_at="2026-07-28T00:00:00+00:00",
                **payloads,
            )

    def test_one_nonzero_hedge_side_is_accepted(self) -> None:
        payloads = capture_payloads()
        payloads["account"]["positions"][0]["positionSide"] = "LONG"
        payloads["position_risk"][0]["positionSide"] = "LONG"

        fixture = build_fixture(
            symbol="BTCUSD_PERP",
            base_url="https://testnet.binancefuture.com",
            server_time_start=1000,
            server_time_end=1250,
            captured_at="2026-07-28T00:00:00+00:00",
            **payloads,
        )

        self.assertEqual(fixture["position_side"], "LONG")

    def test_two_nonzero_hedge_sides_are_rejected(self) -> None:
        payloads = capture_payloads()
        payloads["account"]["positions"][0]["positionSide"] = "LONG"
        payloads["position_risk"][0]["positionSide"] = "LONG"
        payloads["account"]["positions"].append(
            {
                **payloads["account"]["positions"][0],
                "positionSide": "SHORT",
                "positionAmt": "-1",
            }
        )
        payloads["position_risk"].append(
            {
                **payloads["position_risk"][0],
                "positionSide": "SHORT",
                "positionAmt": "-1",
            }
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "single non-zero side",
        ):
            build_fixture(
                symbol="BTCUSD_PERP",
                base_url="https://testnet.binancefuture.com",
                server_time_start=1000,
                server_time_end=1250,
                captured_at="2026-07-28T00:00:00+00:00",
                **payloads,
            )

    def test_incoherent_sequential_snapshot_is_rejected(self) -> None:
        payloads = capture_payloads()
        payloads["account"]["assets"][0][
            "availableBalance"
        ] = "0.00789900"

        with self.assertRaisesRegex(
            RuntimeError,
            "not internally coherent",
        ):
            build_fixture(
                symbol="BTCUSD_PERP",
                base_url="https://testnet.binancefuture.com",
                server_time_start=1000,
                server_time_end=1250,
                captured_at="2026-07-28T00:00:00+00:00",
                **payloads,
            )


if __name__ == "__main__":
    unittest.main()
