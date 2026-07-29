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
    InverseContractFeeModel,
    InverseContractLedger,
    InverseContractMarginModel,
)
from market_protocol import MarketFrame  # noqa: E402
from market_simulator import FixedBarMarketSource  # noqa: E402
from simulation_runtime import (  # noqa: E402
    FlatMaintenanceMarginSchedule,
    FixedBpsSlippageModel,
    InsufficientMarginError,
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


class SingleInstructionPort:
    def __init__(self, trade: TradeInstruction) -> None:
        self.trade = trade
        self.fill_batches: list[tuple[SimFill, ...]] = []

    def initialize(self, frame: MarketFrame) -> None:
        return None

    def instructions_for(
        self,
        frame: MarketFrame,
    ) -> tuple[TradeInstruction, ...]:
        if frame.sequence == self.trade.frame_sequence:
            return (self.trade,)
        return ()

    def on_fills(self, fills: Sequence[SimFill]) -> None:
        self.fill_batches.append(tuple(fills))

    def on_market(self, frame: MarketFrame) -> None:
        return None


def source() -> FixedBarMarketSource:
    bars = [
        (PRICE, PRICE, PRICE, PRICE),
        (PRICE, PRICE, PRICE, PRICE),
    ]
    return FixedBarMarketSource(INSTRUMENT, bars)


def instruction(
    *,
    side: OrderSide,
    quantity: str,
    reduce_only: bool = False,
) -> TradeInstruction:
    return TradeInstruction(
        instruction_key="test-trade",
        source_intent_key="test-intent",
        instrument=INSTRUMENT,
        side=side,
        quantity=Decimal(quantity),
        price=PRICE,
        frame_sequence=1,
        intent_mode=TradeIntentMode.ACTIVE,
        reduce_only=reduce_only,
    )


def ledger(
    futures_wallet_btc: str = "0.001",
) -> InverseContractLedger:
    return InverseContractLedger(
        instrument=INSTRUMENT,
        contract_size=CONTRACT_SIZE,
        spot_base_balance=Decimal("1"),
        futures_wallet_balance=Decimal(futures_wallet_btc),
    )


def margin_model() -> InverseContractMarginModel:
    return InverseContractMarginModel(
        MarginConfig(
            leverage=Decimal("5"),
            maintenance_schedule=FlatMaintenanceMarginSchedule(
                Decimal("0.005")
            ),
        )
    )


def seed_fill(
    *,
    side: OrderSide,
    quantity: str,
) -> SimFill:
    return SimFill(
        fill_id="seed-fill",
        instruction_key="seed-instruction",
        source_intent_key="seed-intent",
        intent_mode=TradeIntentMode.ACTIVE,
        instrument=INSTRUMENT,
        side=side,
        price=PRICE,
        quantity=Decimal(quantity),
        sequence=0,
        timestamp=0,
        liquidity_role=LiquidityRole.TAKER,
        fee_rate=Decimal("0"),
        fee_amount=Decimal("0"),
        fee_asset="BTC",
        reduce_only=False,
    )


class InverseMarginExecutionTests(unittest.TestCase):
    def test_inverse_fee_uses_slippage_adjusted_fill_price(self) -> None:
        account = ledger()
        port = SingleInstructionPort(
            instruction(side=OrderSide.BUY, quantity="1")
        )
        fee_model = InverseContractFeeModel(
            contract_size=CONTRACT_SIZE,
            maker_fee_rate=Decimal("0.001"),
            taker_fee_rate=Decimal("0.001"),
        )

        result = SimulationRunner(
            source(),
            trade_port=port,
            ledger_factory=lambda: account,
            slippage_model=FixedBpsSlippageModel(Decimal("100")),
            fee_model=fee_model,
        ).run()

        fill = result.fills[0]
        self.assertEqual(fill.reference_price, PRICE)
        self.assertEqual(fill.price, Decimal("101000"))
        self.assertEqual(fill.slippage_bps, Decimal("100"))
        self.assertEqual(
            fill.fee_amount,
            CONTRACT_SIZE / fill.price * Decimal("0.001"),
        )

    def test_insufficient_opening_margin_has_no_fill_or_ledger_effect(
        self,
    ) -> None:
        account = ledger()
        port = SingleInstructionPort(
            instruction(side=OrderSide.BUY, quantity="6")
        )

        with self.assertRaises(InsufficientMarginError) as raised:
            SimulationRunner(
                source(),
                trade_port=port,
                ledger_factory=lambda: account,
                margin_model=margin_model(),
            ).run()

        projection = raised.exception.projected_snapshot
        self.assertEqual(
            projection.position_quantity,
            Decimal("6"),
        )
        self.assertEqual(
            projection.position_initial_margin,
            Decimal("0.0012"),
        )
        self.assertEqual(
            projection.available_balance,
            Decimal("-0.0002"),
        )
        self.assertEqual(
            projection.mark_price_source,
            "fill_price_proxy",
        )
        self.assertEqual(account.position_quantity, Decimal("0"))
        self.assertEqual(
            account.futures_wallet_balance,
            Decimal("0.001"),
        )
        self.assertEqual(account.total_fees, Decimal("0"))
        self.assertEqual(port.fill_batches, [])

    def test_exactly_zero_projected_available_balance_is_allowed(
        self,
    ) -> None:
        account = ledger()
        port = SingleInstructionPort(
            instruction(side=OrderSide.BUY, quantity="5")
        )

        result = SimulationRunner(
            source(),
            trade_port=port,
            ledger_factory=lambda: account,
            margin_model=margin_model(),
        ).run()

        self.assertEqual(len(result.fills), 1)
        self.assertEqual(account.position_quantity, Decimal("5"))
        self.assertEqual(len(port.fill_batches), 1)

    def test_same_direction_addition_uses_the_current_position(
        self,
    ) -> None:
        account = ledger("0.0005")
        account.apply(
            seed_fill(side=OrderSide.BUY, quantity="2")
        )
        port = SingleInstructionPort(
            instruction(side=OrderSide.BUY, quantity="1")
        )

        with self.assertRaises(InsufficientMarginError) as raised:
            SimulationRunner(
                source(),
                trade_port=port,
                ledger_factory=lambda: account,
                margin_model=margin_model(),
            ).run()

        projection = raised.exception.projected_snapshot
        self.assertEqual(
            projection.position_quantity,
            Decimal("3"),
        )
        self.assertEqual(
            projection.available_balance,
            Decimal("-0.0001"),
        )
        self.assertEqual(account.position_quantity, Decimal("2"))
        self.assertEqual(port.fill_batches, [])

    def test_fee_is_included_before_margin_is_accepted(self) -> None:
        account = ledger()
        port = SingleInstructionPort(
            instruction(side=OrderSide.BUY, quantity="5")
        )
        fee_model = InverseContractFeeModel(
            contract_size=CONTRACT_SIZE,
            maker_fee_rate=Decimal("0.001"),
            taker_fee_rate=Decimal("0.001"),
        )

        with self.assertRaises(InsufficientMarginError) as raised:
            SimulationRunner(
                source(),
                trade_port=port,
                ledger_factory=lambda: account,
                fee_model=fee_model,
                margin_model=margin_model(),
            ).run()

        projection = raised.exception.projected_snapshot
        self.assertEqual(
            projection.wallet_balance,
            Decimal("0.000995"),
        )
        self.assertEqual(
            projection.available_balance,
            Decimal("-0.000005"),
        )
        self.assertEqual(account.position_quantity, Decimal("0"))
        self.assertEqual(account.total_fees, Decimal("0"))
        self.assertEqual(port.fill_batches, [])

    def test_reduce_only_close_is_allowed_while_initial_margin_is_low(
        self,
    ) -> None:
        account = ledger("0.0001")
        account.apply(
            seed_fill(side=OrderSide.BUY, quantity="5")
        )
        port = SingleInstructionPort(
            instruction(
                side=OrderSide.SELL,
                quantity="1",
                reduce_only=True,
            )
        )

        result = SimulationRunner(
            source(),
            trade_port=port,
            ledger_factory=lambda: account,
            margin_model=margin_model(),
        ).run()

        self.assertEqual(len(result.fills), 1)
        self.assertEqual(account.position_quantity, Decimal("4"))

    def test_crossing_through_zero_checks_the_new_opposite_exposure(
        self,
    ) -> None:
        account = ledger("0.0001")
        account.apply(
            seed_fill(side=OrderSide.BUY, quantity="5")
        )
        port = SingleInstructionPort(
            instruction(side=OrderSide.SELL, quantity="6")
        )

        with self.assertRaises(InsufficientMarginError) as raised:
            SimulationRunner(
                source(),
                trade_port=port,
                ledger_factory=lambda: account,
                margin_model=margin_model(),
            ).run()

        self.assertEqual(
            raised.exception.projected_snapshot.position_quantity,
            Decimal("-1"),
        )
        self.assertEqual(account.position_quantity, Decimal("5"))
        self.assertEqual(port.fill_batches, [])


if __name__ == "__main__":
    unittest.main()
