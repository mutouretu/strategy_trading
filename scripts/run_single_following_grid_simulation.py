from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SIMULATOR_ROOT = PROJECT_ROOT.parent / "market_simulator"
for package_path in (
    PROJECT_ROOT,
    SIMULATOR_ROOT / "packages" / "market_protocol" / "src",
    SIMULATOR_ROOT / "packages" / "market_simulator" / "src",
    SIMULATOR_ROOT / "packages" / "simulation_runtime" / "src",
):
    sys.path.insert(0, str(package_path))

from grid_rule import (  # noqa: E402
    GridRuleConfig,
    GridMarketType,
    GridMode,
)
from grid_rule.adapters import (  # noqa: E402
    GridRuleSimulationAdapter,
    InverseContractFeeModel,
    InverseContractLedger,
)
from grid_strategies import (  # noqa: E402
    SingleFollowingGridStrategyConfig,
)
from grid_strategies.adapters import (  # noqa: E402
    SingleFollowingGridSimulationAdapter,
)
from market_simulator import AnchoredGBMMarketSource  # noqa: E402
from simulation_runtime import (  # noqa: E402
    FixedRateFeeModel,
    SimulationRunner,
    simulation_result_to_document,
)


SEED = 42
MAKER_FEE_RATE = Decimal("0.0002")
TAKER_FEE_RATE = Decimal("0.0005")
ANCHORS = (
    ("2026-01-01", "65000"),
    ("2026-07-01", "40000"),
    ("2027-01-01", "115000"),
    ("2027-07-01", "55000"),
    ("2028-01-01", "200000"),
    ("2028-07-01", "45000"),
    ("2029-01-01", "160000"),
)


def build_run(
    seed: int = SEED,
    *,
    move_grid: bool = True,
    coinm: bool = True,
    spot_btc: Decimal = Decimal("1"),
    futures_wallet_btc: Decimal = Decimal("0.1"),
    order_coin_qty: Decimal = Decimal("0.01"),
    maker_fee_rate: Decimal = MAKER_FEE_RATE,
    taker_fee_rate: Decimal = TAKER_FEE_RATE,
) -> dict[str, object]:
    spot_btc = Decimal(spot_btc)
    futures_wallet_btc = Decimal(futures_wallet_btc)
    order_coin_qty = Decimal(order_coin_qty)
    maker_fee_rate = Decimal(maker_fee_rate)
    taker_fee_rate = Decimal(taker_fee_rate)
    if coinm:
        config = GridRuleConfig(
            grid_id="grid-rule-coinm-long-3y",
            instrument="BTCUSD_PERP",
            mode=GridMode.LONG,
            anchor_price=65000,
            grid_ratio="0.04",
            grid_count=5,
            order_notional=0,
            tick_size="0.1",
            quantity_step="1",
            move_grid=move_grid,
            market_type=GridMarketType.COINM,
            order_coin_qty=order_coin_qty,
            contract_size="100",
        )
    else:
        config = GridRuleConfig(
            grid_id="grid-rule-linear-long-3y",
            instrument="BTCUSDT",
            mode=GridMode.LONG,
            anchor_price=65000,
            grid_ratio="0.04",
            grid_count=5,
            order_notional=650,
            tick_size="0.01",
            quantity_step="0.0001",
            move_grid=move_grid,
        )
    strategy_id: str | None = None
    if move_grid:
        strategy_id = (
            f"single-following-grid-"
            f"{'coinm' if coinm else 'linear'}-long-3y"
        )
        adapter = SingleFollowingGridSimulationAdapter(
            SingleFollowingGridStrategyConfig(
                strategy_id=strategy_id,
                rule=config,
            )
        )
    else:
        adapter = GridRuleSimulationAdapter(config)
    source = AnchoredGBMMarketSource(
        config.instrument,
        ANCHORS,
        annual_volatility="0.60",
        intraday_steps=24,
        price_floor="40000",
        price_ceiling="200000",
    )
    if coinm:
        runner = SimulationRunner(
            source,
            trade_port=adapter,
            fee_model=InverseContractFeeModel(
                contract_size=config.contract_size,
                maker_fee_rate=maker_fee_rate,
                taker_fee_rate=taker_fee_rate,
                fee_asset="BTC",
            ),
            ledger_factory=lambda: InverseContractLedger(
                instrument=config.instrument,
                contract_size=config.contract_size,
                spot_base_balance=spot_btc,
                futures_wallet_balance=futures_wallet_btc,
                base_asset="BTC",
                quote_asset="USDT",
            ),
        )
    else:
        runner = SimulationRunner(
            source,
            trade_port=adapter,
            initial_equity=10_000,
            fee_model=FixedRateFeeModel(
                maker_fee_rate=maker_fee_rate,
                taker_fee_rate=taker_fee_rate,
                fee_asset="USDT",
            ),
        )
    result = runner.run(seed=seed)
    document = simulation_result_to_document(
        result,
        run_id=(
            (
                f"single-following-grid-"
                f"{'coinm' if coinm else 'linear'}-long"
            )
            if move_grid
            else (
                f"grid-rule-"
                f"{'coinm' if coinm else 'linear'}-static-long"
            )
        )
        + f"-3y-seed-{seed}",
        interval="1d",
        source="anchored_gbm",
        seed=seed,
        manifest={
            "simulation_adapter": (
                "single_following_grid_simulation_adapter"
                if move_grid
                else "grid_rule_simulation_adapter"
            ),
            "strategy_id": strategy_id,
            "rule_engine": "grid_rule",
            "grid_id": config.grid_id,
            "mode": config.mode.value,
            "market_type": config.market_type.value,
            "move_grid": config.move_grid,
            "anchor_price": str(config.anchor_price),
            "grid_ratio": str(config.grid_ratio),
            "grid_count": config.grid_count,
            "order_notional": str(config.order_notional),
            "order_coin_qty": (
                None
                if config.order_coin_qty is None
                else str(config.order_coin_qty)
            ),
            "contract_size": str(config.contract_size),
            "maker_fee_rate": str(maker_fee_rate),
            "taker_fee_rate": str(taker_fee_rate),
            "initial_spot_btc": str(spot_btc) if coinm else None,
            "initial_futures_wallet_btc": (
                str(futures_wallet_btc) if coinm else None
            ),
            "price_floor": "40000",
            "price_ceiling": "200000",
            "anchors": [
                {"date": anchor_date, "price": price}
                for anchor_date, price in ANCHORS
            ],
        },
    )
    engine = (
        adapter.strategy.engine
        if isinstance(adapter, SingleFollowingGridSimulationAdapter)
        else adapter.engine
    )
    document["summary"]["completed_cycles"] = (
        engine.completed_cycles
    )
    document["summary"]["cells_added"] = engine.cells_added
    document["summary"]["cells_reclaimed"] = engine.cells_reclaimed
    document["summary"]["final_cell_count"] = len(engine.cells)
    document["summary"]["final_cells"] = [
        {
            "cell_id": cell.cell_id,
            "buy_price": str(cell.buy_price),
            "sell_price": str(cell.sell_price),
            "phase": cell.phase.value,
            "position_quantity": str(cell.position_quantity),
            "cycle_count": cell.cycle_count,
        }
        for cell in engine.cells
    ]
    document["summary"]["intent_count"] = len(result.intents)
    document["summary"]["instruction_count"] = len(
        result.instructions
    )
    document["summary"]["fill_count"] = len(result.fills)
    if coinm:
        futures_equity = [
            (
                snapshot["date"],
                Decimal(
                    snapshot["account_metrics"]["futures_equity_btc"]
                ),
            )
            for snapshot in document["equity"]
        ]
        minimum_date, minimum_equity = min(
            futures_equity,
            key=lambda item: item[1],
        )
        first_nonpositive = next(
            (
                (date, equity)
                for date, equity in futures_equity
                if equity <= 0
            ),
            None,
        )
        document["summary"]["minimum_futures_equity_btc"] = str(
            minimum_equity
        )
        document["summary"]["minimum_futures_equity_date"] = minimum_date
        document["summary"]["futures_equity_nonpositive"] = (
            first_nonpositive is not None
        )
        document["summary"]["first_nonpositive_futures_equity_date"] = (
            None if first_nonpositive is None else first_nonpositive[0]
        )
    return document


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument(
        "--static-grid",
        action="store_true",
        help="Run a direct static GridRule baseline for comparison.",
    )
    parser.add_argument(
        "--linear",
        action="store_true",
        help="Use the previous USD-M linear account instead of COIN-M.",
    )
    parser.add_argument(
        "--spot-btc",
        type=Decimal,
        default=Decimal("1"),
        help="Long-term spot BTC balance, never traded by the grid.",
    )
    parser.add_argument(
        "--futures-wallet-btc",
        type=Decimal,
        default=Decimal("0.1"),
        help="Initial COIN-M futures wallet balance in BTC.",
    )
    parser.add_argument(
        "--order-coin-qty",
        type=Decimal,
        default=Decimal("0.01"),
        help="Target BTC amount per COIN-M grid cell.",
    )
    parser.add_argument(
        "--maker-fee-rate",
        type=Decimal,
        default=MAKER_FEE_RATE,
    )
    parser.add_argument(
        "--taker-fee-rate",
        type=Decimal,
        default=TAKER_FEE_RATE,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
    )
    args = parser.parse_args()
    mode = "static" if args.static_grid else "following"
    market = "linear" if args.linear else "coinm"
    default_name = (
        f"grid-rule-{market}-static-long-3y-seed-{args.seed}.json"
        if args.static_grid
        else (
            f"single-following-grid-{market}-long-3y-"
            f"seed-{args.seed}.json"
        )
    )
    output = (
        args.output
        or SIMULATOR_ROOT / "viewer" / "data" / default_name
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            build_run(
                args.seed,
                move_grid=not args.static_grid,
                coinm=not args.linear,
                spot_btc=args.spot_btc,
                futures_wallet_btc=args.futures_wallet_btc,
                order_coin_qty=args.order_coin_qty,
                maker_fee_rate=args.maker_fee_rate,
                taker_fee_rate=args.taker_fee_rate,
            ),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(output)


if __name__ == "__main__":
    main()
