from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
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
    InverseContractLedger,
    InverseContractMarginModel,
)
from market_protocol import MarketFrame  # noqa: E402
from market_simulator import FixedBarMarketSource  # noqa: E402
from simulation_runtime import (  # noqa: E402
    FlatMaintenanceMarginSchedule,
    LiquidityRole,
    MarginConfig,
    MarkPriceSampling,
    OrderSide,
    SimFill,
    SimulationRunner,
    TradeInstruction,
    TradeIntentMode,
    simulation_result_to_document,
)


INSTRUMENT = "BTCUSD_PERP"
CONTRACT_SIZE = Decimal("100")
ENTRY_PRICE = Decimal("100000")
START_TIMESTAMP = int(
    datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000
)
DEFAULT_OUTPUT = (
    SIMULATOR_ROOT
    / "viewer"
    / "data"
    / "coinm-liquidation-adverse-extreme-v1.json"
)


class HoldPositionTradePort:
    """Submit no trades; the demo isolates platform liquidation behavior."""

    def initialize(self, frame: MarketFrame) -> None:
        pass

    def instructions_for(
        self,
        frame: MarketFrame,
    ) -> tuple[TradeInstruction, ...]:
        return ()

    def on_fills(self, fills: Sequence[SimFill]) -> None:
        pass

    def on_market(self, frame: MarketFrame) -> None:
        pass


def account() -> InverseContractLedger:
    ledger = InverseContractLedger(
        instrument=INSTRUMENT,
        contract_size=CONTRACT_SIZE,
        spot_base_balance=Decimal("1"),
        futures_wallet_balance=Decimal("0.003"),
    )
    ledger.apply(
        SimFill(
            fill_id="seed-long@0",
            instruction_key="seed-long",
            source_intent_key="seed-account-position",
            intent_mode=TradeIntentMode.ACTIVE,
            instrument=INSTRUMENT,
            side=OrderSide.BUY,
            price=ENTRY_PRICE,
            quantity=Decimal("10"),
            sequence=0,
            timestamp=START_TIMESTAMP,
            liquidity_role=LiquidityRole.TAKER,
            fee_rate=Decimal("0"),
            fee_amount=Decimal("0"),
            fee_asset="BTC",
            reduce_only=False,
        )
    )
    return ledger


def build_run() -> dict[str, object]:
    source = FixedBarMarketSource(
        INSTRUMENT,
        (
            ("100000", "100000", "100000", "100000"),
            ("100000", "105000", "90000", "95000"),
            ("95000", "96000", "80000", "90000"),
            ("90000", "92000", "77000", "90000"),
            ("90000", "110000", "90000", "105000"),
        ),
        start_timestamp=START_TIMESTAMP,
    )
    margin_model = InverseContractMarginModel(
        MarginConfig(
            leverage=Decimal("5"),
            maintenance_schedule=FlatMaintenanceMarginSchedule(
                Decimal("0.005")
            ),
        )
    )
    result = SimulationRunner(
        source,
        trade_port=HoldPositionTradePort(),
        ledger_factory=account,
        margin_model=margin_model,
        mark_price_sampling=MarkPriceSampling.ADVERSE_EXTREME,
    ).run()
    return simulation_result_to_document(
        result,
        run_id="coinm-liquidation-adverse-extreme-v1",
        interval="1d",
        source="fixed_liquidation_demo",
        manifest={
            "simulation_kind": "coinm_liquidation_demo",
            "market_type": "coinm",
            "contract_size": str(CONTRACT_SIZE),
            "initial_spot_btc": "1",
            "initial_futures_wallet_btc": "0.003",
            "seed_position_contracts": "10",
            "seed_entry_price": str(ENTRY_PRICE),
            "leverage": "5",
            "maintenance_margin_rate": "0.005",
            "maintenance_schedule_version": (
                margin_model.maintenance_schedule_version
            ),
            "mark_price_sampling": (
                MarkPriceSampling.ADVERSE_EXTREME.value
            ),
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a deterministic COIN-M liquidation run."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(build_run(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)


if __name__ == "__main__":
    main()
