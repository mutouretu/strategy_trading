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

from grid_rule import GridMarketType, GridMode, GridRuleConfig  # noqa: E402
from grid_rule.adapters import (  # noqa: E402
    InverseContractFeeModel,
    InverseContractLedger,
)
from grid_strategies import (  # noqa: E402
    LayeredFollowingGridStrategyConfig,
)
from grid_strategies.adapters import (  # noqa: E402
    LayeredFollowingGridSimulationAdapter,
)
from market_simulator import AnchoredGBMMarketSource  # noqa: E402
from scripts.run_single_following_grid_simulation import (  # noqa: E402
    ANCHORS,
    MAKER_FEE_RATE,
    TAKER_FEE_RATE,
)
from simulation_runtime import (  # noqa: E402
    SimulationRunner,
    simulation_result_to_document,
)


SEED = 42


def build_run(
    seed: int = SEED,
    *,
    spot_btc: Decimal = Decimal("1"),
    futures_wallet_btc: Decimal = Decimal("0.2"),
    order_coin_qty: Decimal = Decimal("0.003"),
    maker_fee_rate: Decimal = MAKER_FEE_RATE,
    taker_fee_rate: Decimal = TAKER_FEE_RATE,
) -> dict[str, object]:
    spot_btc = Decimal(spot_btc)
    futures_wallet_btc = Decimal(futures_wallet_btc)
    order_coin_qty = Decimal(order_coin_qty)
    maker_fee_rate = Decimal(maker_fee_rate)
    taker_fee_rate = Decimal(taker_fee_rate)
    rule_template = GridRuleConfig(
        grid_id="layered-following-grid-template",
        instrument="BTCUSD_PERP",
        mode=GridMode.LONG,
        anchor_price=Decimal("65000"),
        grid_ratio=Decimal("0.02"),
        grid_count=3,
        order_notional=Decimal("0"),
        tick_size=Decimal("0.1"),
        quantity_step=Decimal("1"),
        move_grid=True,
        market_type=GridMarketType.COINM,
        order_coin_qty=order_coin_qty,
        contract_size=Decimal("100"),
    )
    strategy_config = LayeredFollowingGridStrategyConfig(
        strategy_id="layered-following-grid-coinm-long-3y",
        rule_template=rule_template,
        deployment_step=Decimal("5000"),
    )
    adapter = LayeredFollowingGridSimulationAdapter(strategy_config)
    source = AnchoredGBMMarketSource(
        rule_template.instrument,
        ANCHORS,
        annual_volatility=Decimal("0.60"),
        intraday_steps=24,
        price_floor=Decimal("40000"),
        price_ceiling=Decimal("200000"),
    )
    runner = SimulationRunner(
        source,
        trade_port=adapter,
        fee_model=InverseContractFeeModel(
            contract_size=rule_template.contract_size,
            maker_fee_rate=maker_fee_rate,
            taker_fee_rate=taker_fee_rate,
            fee_asset="BTC",
        ),
        ledger_factory=lambda: InverseContractLedger(
            instrument=rule_template.instrument,
            contract_size=rule_template.contract_size,
            spot_base_balance=spot_btc,
            futures_wallet_balance=futures_wallet_btc,
            base_asset="BTC",
            quote_asset="USDT",
        ),
    )
    result = runner.run(seed=seed)
    strategy = adapter.strategy
    document = simulation_result_to_document(
        result,
        run_id=f"layered-following-grid-coinm-long-3y-seed-{seed}",
        interval="1d",
        source="anchored_gbm",
        seed=seed,
        manifest={
            "simulation_adapter": (
                "layered_following_grid_simulation_adapter"
            ),
            "strategy_id": strategy_config.strategy_id,
            "rule_engine": "grid_rule",
            "instrument": rule_template.instrument,
            "mode": rule_template.mode.value,
            "market_type": rule_template.market_type.value,
            "move_grid": rule_template.move_grid,
            "base_anchor_price": str(rule_template.anchor_price),
            "deployment_step": str(strategy_config.deployment_step),
            "grid_ratio": str(rule_template.grid_ratio),
            "grid_count_per_layer": rule_template.grid_count,
            "order_coin_qty": str(rule_template.order_coin_qty),
            "contract_size": str(rule_template.contract_size),
            "maker_fee_rate": str(maker_fee_rate),
            "taker_fee_rate": str(taker_fee_rate),
            "initial_spot_btc": str(spot_btc),
            "initial_futures_wallet_btc": str(futures_wallet_btc),
            "price_floor": "40000",
            "price_ceiling": "200000",
            "anchors": [
                {"date": anchor_date, "price": price}
                for anchor_date, price in ANCHORS
            ],
        },
    )
    document["summary"].update(
        {
            "completed_cycles": strategy.completed_cycles,
            "cells_added": strategy.cells_added,
            "cells_reclaimed": strategy.cells_reclaimed,
            "layer_count": strategy.layer_count,
            "reset_count": strategy.reset_count,
            "retiring_grid_count": strategy.retiring_grid_count,
            "layers": [
                {
                    "layer_index": layer.layer_index,
                    "anchor_price": str(layer.anchor_price),
                    "generation": layer.generation,
                    "lower_edge": str(layer.lower_edge),
                    "upper_edge": str(layer.upper_edge),
                    "waiting_for_reentry": layer.waiting_for_reentry,
                    "reset_count": layer.reset_count,
                    "completed_cycles": layer.completed_cycles,
                    "position_quantity": str(layer.position_quantity),
                }
                for layer in strategy.layers
            ],
            "intent_count": len(result.intents),
            "instruction_count": len(result.instructions),
            "fill_count": len(result.fills),
        }
    )
    futures_equity = [
        (
            snapshot["date"],
            Decimal(snapshot["account_metrics"]["futures_equity_btc"]),
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
        "--spot-btc",
        type=Decimal,
        default=Decimal("1"),
    )
    parser.add_argument(
        "--futures-wallet-btc",
        type=Decimal,
        default=Decimal("0.2"),
    )
    parser.add_argument(
        "--order-coin-qty",
        type=Decimal,
        default=Decimal("0.003"),
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
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    output = args.output or (
        SIMULATOR_ROOT
        / "viewer"
        / "data"
        / f"layered-following-grid-coinm-long-3y-seed-{args.seed}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            build_run(
                args.seed,
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
