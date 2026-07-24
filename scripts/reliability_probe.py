#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gridtrader.application.reliability import (
    append_jsonl,
    collect_sample,
    summarize_jsonl,
)
from gridtrader.infrastructure.binance import BinanceFuturesExchange
from gridtrader.shared.config import (
    api_base_url,
    binance_base_url,
    binance_credentials,
    load_environment,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Read-only reliability sampler. It only performs SQLite reads, "
            "Binance GET requests, HTTP GET health checks and local process inspection."
        )
    )
    result.add_argument("--env-file", default=None)
    result.add_argument("--db", default=None)
    result.add_argument("--output", default="runtime/reliability/samples.jsonl")
    result.add_argument("--api-url", default=None)
    result.add_argument("--streamlit-url", default=None)
    result.add_argument("--pid-file", default=None)
    result.add_argument("--label", default="")
    result.add_argument("--timeout", type=float, default=5.0)
    result.add_argument(
        "--summary",
        metavar="JSONL",
        help="summarize an existing sample file instead of contacting Binance",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    if args.summary:
        print(json.dumps(summarize_jsonl(args.summary), ensure_ascii=False, indent=2))
        return 0

    load_environment(args.env_file, override=bool(args.env_file))
    db_path = args.db or os.getenv("GRID_DB_PATH", "grid_trading.sqlite3")
    api_url = args.api_url if args.api_url is not None else api_base_url()
    streamlit_url = (
        args.streamlit_url
        if args.streamlit_url is not None
        else os.getenv("GRID_STREAMLIT_URL", "").strip() or None
    )
    pid_file = args.pid_file or os.getenv("GRID_SCHEDULER_PID_FILE", "").strip() or None
    api_key, api_secret = binance_credentials(required=True)
    exchange = BinanceFuturesExchange(api_key, api_secret, binance_base_url())
    sample = collect_sample(
        db_path,
        exchange,
        api_url=api_url,
        streamlit_url=streamlit_url,
        pid_file=pid_file,
        label=args.label,
        timeout=args.timeout,
    )
    append_jsonl(args.output, sample)
    counts = sample.get("analysis", {}).get("anomaly_counts", {})
    active_counts = {key: value for key, value in counts.items() if value}
    print(
        json.dumps(
            {
                "sampled_at": sample["sampled_at"],
                "overall": sample["overall"],
                "duration_ms": sample["duration_ms"],
                "anomalies": active_counts,
                "output": str(Path(args.output).expanduser().resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
