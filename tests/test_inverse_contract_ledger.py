from __future__ import annotations

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

from grid_rule.adapters import InverseContractLedger  # noqa: E402
from simulation_runtime import (  # noqa: E402
    LiquidityRole,
    OrderSide,
    SimFill,
    TradeIntentMode,
)


INSTRUMENT = "BTCUSD_PERP"


def fill(
    key: str,
    side: OrderSide,
    price: str,
    quantity: str,
) -> SimFill:
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
        fee_amount=Decimal("0"),
        fee_asset="BTC",
        reduce_only=False,
    )


class InverseContractLedgerTests(unittest.TestCase):
    def ledger(self) -> InverseContractLedger:
        return InverseContractLedger(
            instrument=INSTRUMENT,
            contract_size=Decimal("100"),
            spot_base_balance=Decimal("1"),
            futures_wallet_balance=Decimal("0.1"),
        )

    def test_long_inverse_pnl_settles_in_btc_without_touching_spot(self) -> None:
        ledger = self.ledger()

        ledger.apply(fill("open", OrderSide.BUY, "100000", "2"))
        ledger.apply(fill("close", OrderSide.SELL, "120000", "2"))

        expected = Decimal("200") * (
            Decimal("1") / Decimal("100000")
            - Decimal("1") / Decimal("120000")
        )
        self.assertEqual(ledger.realized_pnl, expected)
        self.assertEqual(ledger.cash, Decimal("0.1") + expected)
        self.assertEqual(ledger.spot_base_balance, Decimal("1"))
        self.assertEqual(ledger.positions, {INSTRUMENT: Decimal("0")})

    def test_short_inverse_pnl_uses_reversed_reciprocal_prices(self) -> None:
        ledger = self.ledger()

        ledger.apply(fill("open", OrderSide.SELL, "120000", "3"))
        ledger.apply(fill("close", OrderSide.BUY, "100000", "3"))

        expected = Decimal("300") * (
            Decimal("1") / Decimal("100000")
            - Decimal("1") / Decimal("120000")
        )
        self.assertEqual(ledger.realized_pnl, expected)

    def test_add_and_partial_reduce_keep_inverse_weighted_cost(self) -> None:
        ledger = self.ledger()

        ledger.apply(fill("open-1", OrderSide.BUY, "100000", "2"))
        ledger.apply(fill("open-2", OrderSide.BUY, "120000", "1"))

        expected_average = Decimal("3") / (
            Decimal("2") / Decimal("100000")
            + Decimal("1") / Decimal("120000")
        )
        self.assertEqual(ledger.average_entry_price, expected_average)

        ledger.apply(fill("reduce", OrderSide.SELL, "130000", "1"))

        expected_realized = Decimal("100") * (
            Decimal("1") / expected_average
            - Decimal("1") / Decimal("130000")
        )
        self.assertEqual(ledger.position_quantity, Decimal("2"))
        self.assertEqual(ledger.average_entry_price, expected_average)
        self.assertEqual(ledger.gross_realized_pnl, expected_realized)
        self.assertEqual(
            ledger.futures_wallet_balance,
            Decimal("0.1") + expected_realized,
        )

    def test_account_metrics_separate_spot_wallet_and_unrealized_pnl(
        self,
    ) -> None:
        ledger = self.ledger()
        ledger.apply(fill("open", OrderSide.BUY, "100000", "2"))

        metrics = ledger.account_metrics(
            {INSTRUMENT: Decimal("80000")}
        )

        expected_unrealized = Decimal("200") * (
            Decimal("1") / Decimal("100000")
            - Decimal("1") / Decimal("80000")
        )
        expected_total_btc = Decimal("1.1") + expected_unrealized
        self.assertEqual(
            metrics["futures_unrealized_pnl_btc"],
            expected_unrealized,
        )
        self.assertEqual(metrics["spot_btc"], Decimal("1"))
        self.assertEqual(metrics["total_equity_btc"], expected_total_btc)
        self.assertEqual(
            metrics["total_equity_usdt"],
            expected_total_btc * Decimal("80000"),
        )
        self.assertEqual(metrics["contract_notional_usd"], Decimal("200"))


if __name__ == "__main__":
    unittest.main()
