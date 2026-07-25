from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
for package in ("market_protocol", "market_simulator"):
    sys.path.insert(0, str(PROJECT_ROOT / "packages" / package / "src"))

from market_simulator import AnchoredGBMMarketSource  # noqa: E402


ANCHORS = [
    ("2026-01-01", "65000"),
    ("2026-04-01", "62000"),
    ("2026-07-01", "59000"),
    ("2026-10-01", "61000"),
    ("2027-01-01", "64000"),
]


def build_run(seed: int) -> dict:
    source = AnchoredGBMMarketSource(
        "BTCUSD",
        ANCHORS,
        annual_volatility="0.60",
        intraday_steps=24,
    )
    first = source.reset(seed)
    frames = (first, *source.next_batch(10_000))
    return {
        "schema_version": 1,
        "manifest": {
            "run_id": f"btc-anchored-seed-{seed}",
            "instrument": "BTCUSD",
            "interval": "1d",
            "source": "anchored_gbm",
            "seed": seed,
            "annual_volatility": "0.60",
            "intraday_steps": 24,
            "anchors": [
                {"date": anchor_date, "price": price}
                for anchor_date, price in ANCHORS
            ],
        },
        "market": [
            {
                "sequence": frame.sequence,
                "timestamp": frame.timestamp,
                "date": datetime.fromtimestamp(
                    frame.timestamp / 1_000,
                    tz=timezone.utc,
                ).date().isoformat(),
                "instrument": frame.instrument,
                "open": str(frame.open),
                "high": str(frame.high),
                "low": str(frame.low),
                "close": str(frame.close),
            }
            for frame in frames
        ],
        "orders": [],
        "fills": [],
        "equity": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "viewer" / "data" / "btc-anchored-seed-42.json",
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
