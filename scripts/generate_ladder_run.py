from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
for package in ("market_protocol", "market_simulator", "simulation_runtime"):
    sys.path.insert(0, str(PROJECT_ROOT / "packages" / package / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from examples.geometric_ladder_probe import (  # noqa: E402
    ANCHORS,
    ANNUAL_VOLATILITY,
    LEVEL_COUNT,
    ORDER_QUANTITY,
    PRICE_CEILING,
    PRICE_FLOOR,
    SEED,
    STEP_RATIO,
    run_ladder_probe,
)
from simulation_runtime import simulation_result_to_document  # noqa: E402


def build_run(seed: int = SEED) -> dict[str, object]:
    result = run_ladder_probe(seed)
    document = simulation_result_to_document(
        result,
        run_id=f"btc-geometric-ladder-3y-seed-{seed}",
        interval="1d",
        source="anchored_gbm",
        seed=seed,
        manifest={
            "trade_provider": "geometric_ladder_probe",
            "annual_volatility": str(ANNUAL_VOLATILITY),
            "price_floor": str(PRICE_FLOOR),
            "price_ceiling": str(PRICE_CEILING),
            "step_ratio": str(STEP_RATIO),
            "level_count": LEVEL_COUNT,
            "order_quantity": str(ORDER_QUANTITY),
            "anchors": [
                {"date": anchor_date, "price": price}
                for anchor_date, price in ANCHORS
            ],
        },
    )
    document["summary"]["intent_count"] = len(result.intents)
    document["summary"]["instruction_count"] = len(
        result.instructions
    )
    document["summary"]["fill_count"] = len(result.fills)
    document["summary"]["completed_sell_count"] = sum(
        fill.side.value == "SELL" for fill in result.fills
    )
    return document


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            PROJECT_ROOT
            / "viewer"
            / "data"
            / "btc-geometric-ladder-3y-seed-42.json"
        ),
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(build_run(args.seed), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)


if __name__ == "__main__":
    main()
