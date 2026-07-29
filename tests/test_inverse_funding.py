from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SIMULATOR_ROOT = PROJECT_ROOT.parent / "market_simulator"
for package_path in (
    PROJECT_ROOT,
    SIMULATOR_ROOT / "packages" / "market_protocol" / "src",
    SIMULATOR_ROOT / "packages" / "market_simulator" / "src",
    SIMULATOR_ROOT / "packages" / "simulation_runtime" / "src",
):
    sys.path.insert(0, str(package_path))

from grid_rule.adapters import (  # noqa: E402
    FixedRateInverseContractFundingModel,
    InverseContractLedger,
    InverseContractMarginModel,
)
from market_protocol import MarketFrame  # noqa: E402
from market_simulator import FixedBarMarketSource  # noqa: E402
from simulation_runtime import (  # noqa: E402
    FlatMaintenanceMarginSchedule,
    LiquidityRole,
    MarginConfig,
    OrderSide,
    SimFill,
    SimulationRunner,
    TradeInstruction,
    TradeIntentMode,
)


INSTRUMENT = "BTCUSD_PERP"
PRICE = Decimal("100000")
CONTRACT_SIZE = Decimal("100")
DAY_SECONDS = 86_400


def ledger(
    wallet: str = "0.1",
) -> InverseContractLedger:
    return InverseContractLedger(
        instrument=INSTRUMENT,
        contract_size=CONTRACT_SIZE,
        spot_base_balance=Decimal("1"),
        futures_wallet_balance=Decimal(wallet),
    )


def fill(side: OrderSide, quantity: str = "2") -> SimFill:
    return SimFill(
        fill_id=f"fill:{side.value.lower()}",
        instruction_key=f"instruction:{side.value.lower()}",
        source_intent_key=f"intent:{side.value.lower()}",
        intent_mode=TradeIntentMode.ACTIVE,
        instrument=INSTRUMENT,
        side=side,
        price=PRICE,
        quantity=Decimal(quantity),
        sequence=1,
        timestamp=86_400_000,
        liquidity_role=LiquidityRole.TAKER,
        fee_rate=Decimal("0"),
        fee_amount=Decimal("0"),
        fee_asset="BTC",
        reduce_only=False,
    )


def frame(sequence: int = 1) -> MarketFrame:
    return MarketFrame(
        sequence=sequence,
        timestamp=sequence * 86_400_000,
        instrument=INSTRUMENT,
        open=PRICE,
        high=PRICE,
        low=PRICE,
        close=PRICE,
    )


class InverseFundingModelTests(unittest.TestCase):
    def model(
        self,
        rate: str = "0.01",
    ) -> FixedRateInverseContractFundingModel:
        return FixedRateInverseContractFundingModel(
            funding_rate=Decimal(rate),
            funding_interval_seconds=DAY_SECONDS,
        )

    def test_inverse_long_pays_and_short_receives_in_btc(self) -> None:
        for side, expected_delta in (
            (OrderSide.BUY, Decimal("-0.00002")),
            (OrderSide.SELL, Decimal("0.00002")),
        ):
            with self.subTest(side=side):
                account = ledger()
                account.apply(fill(side))

                settlement = self.model().settle(
                    frame(),
                    account,
                    {INSTRUMENT: PRICE},
                )

                self.assertIsNotNone(settlement)
                assert settlement is not None
                self.assertEqual(
                    settlement.position_notional,
                    Decimal("200"),
                )
                self.assertEqual(
                    settlement.position_value,
                    Decimal("0.002"),
                )
                self.assertEqual(
                    settlement.wallet_delta,
                    expected_delta,
                )
                self.assertEqual(settlement.notional_asset, "USD")
                self.assertEqual(settlement.settlement_asset, "BTC")

                account.apply_funding(settlement)
                self.assertEqual(account.total_funding, expected_delta)
                self.assertEqual(
                    account.futures_wallet_balance,
                    Decimal("0.1") + expected_delta,
                )
                self.assertEqual(
                    account.net_pnl_after_fees_and_funding,
                    expected_delta,
                )

    def test_negative_rate_reverses_inverse_payment(self) -> None:
        account = ledger()
        account.apply(fill(OrderSide.BUY))

        settlement = self.model("-0.01").settle(
            frame(),
            account,
            {INSTRUMENT: PRICE},
        )

        self.assertIsNotNone(settlement)
        assert settlement is not None
        self.assertEqual(
            settlement.wallet_delta,
            Decimal("0.00002"),
        )

    def test_empty_position_produces_no_funding_event(self) -> None:
        self.assertIsNone(
            self.model().settle(
                frame(),
                ledger(),
                {INSTRUMENT: PRICE},
            )
        )


class _OpenLongPort:
    def __init__(self) -> None:
        self.fill_batches: list[tuple[SimFill, ...]] = []

    def initialize(self, current: MarketFrame) -> None:
        return None

    def instructions_for(
        self,
        current: MarketFrame,
    ) -> tuple[TradeInstruction, ...]:
        if current.sequence != 1:
            return ()
        return (
            TradeInstruction(
                instruction_key="open@1",
                source_intent_key="open",
                instrument=INSTRUMENT,
                side=OrderSide.BUY,
                quantity=Decimal("5"),
                price=PRICE,
                frame_sequence=1,
                intent_mode=TradeIntentMode.ACTIVE,
            ),
        )

    def on_fills(self, fills: Sequence[SimFill]) -> None:
        self.fill_batches.append(tuple(fills))

    def on_market(self, current: MarketFrame) -> None:
        return None


class InverseFundingRuntimeTests(unittest.TestCase):
    def test_funding_wallet_debit_can_trigger_liquidation(self) -> None:
        account = ledger("0.0011")
        port = _OpenLongPort()
        source = FixedBarMarketSource(
            INSTRUMENT,
            [
                (PRICE, PRICE, PRICE, PRICE),
                (PRICE, PRICE, PRICE, PRICE),
            ],
        )
        margin_model = InverseContractMarginModel(
            MarginConfig(
                leverage=Decimal("5"),
                maintenance_schedule=(
                    FlatMaintenanceMarginSchedule(
                        Decimal("0.005")
                    )
                ),
            )
        )

        result = SimulationRunner(
            source,
            trade_port=port,
            ledger_factory=lambda: account,
            funding_model=(
                FixedRateInverseContractFundingModel(
                    funding_rate=Decimal("0.22"),
                    funding_interval_seconds=DAY_SECONDS,
                )
            ),
            margin_model=margin_model,
        ).run()

        self.assertEqual(len(result.fills), 1)
        self.assertEqual(len(result.funding_events), 1)
        self.assertEqual(
            result.funding_events[0].wallet_delta,
            Decimal("-0.00110"),
        )
        self.assertEqual(result.total_funding, Decimal("-0.00110"))
        self.assertEqual(
            result.net_pnl_after_fees_and_funding,
            Decimal("-0.00110"),
        )
        self.assertEqual(account.futures_wallet_balance, Decimal("0"))
        self.assertTrue(result.liquidated)
        self.assertTrue(result.bankrupt)
        self.assertEqual(
            result.margin_snapshots[-1].wallet_balance,
            Decimal("0"),
        )
        self.assertEqual(port.fill_batches, [])


if __name__ == "__main__":
    unittest.main()
