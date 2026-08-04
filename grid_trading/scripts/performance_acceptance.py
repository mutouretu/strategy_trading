#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from grid_server.application.performance import run_benchmark_matrix, run_soak


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Offline scheduler performance acceptance; never contacts Binance."
    )
    subparsers = result.add_subparsers(dest="command", required=True)

    benchmark = subparsers.add_parser("benchmark")
    benchmark.add_argument("--steady-cycles", type=int, default=5)
    benchmark.add_argument("--output", default="runtime/performance/benchmark.json")

    soak = subparsers.add_parser("soak")
    soak.add_argument("--db", required=True)
    soak.add_argument("--output", required=True)
    soak.add_argument("--duration-sec", type=float, default=86400)
    soak.add_argument("--sample-interval-sec", type=float, default=60)
    soak.add_argument("--groups", type=int, default=50)
    soak.add_argument("--cells", type=int, default=5)
    soak.add_argument("--symbols", type=int, default=1)
    soak.add_argument(
        "--poll-intervals",
        type=float,
        nargs="+",
        default=[50.0, 600.0, 3600.0],
    )
    return result


def main() -> int:
    args = parser().parse_args()
    if args.command == "benchmark":
        result = run_benchmark_matrix(steady_cycles=args.steady_cycles)
        destination = Path(args.output).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({"passed": result["all_acceptance_checks_passed"], "output": str(destination)}))
        return 0 if result["all_acceptance_checks_passed"] else 1

    result = run_soak(
        db_path=args.db,
        output_path=args.output,
        duration_sec=args.duration_sec,
        sample_interval_sec=args.sample_interval_sec,
        groups=args.groups,
        cells_per_group=args.cells,
        poll_intervals=args.poll_intervals,
        symbol_count=args.symbols,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
