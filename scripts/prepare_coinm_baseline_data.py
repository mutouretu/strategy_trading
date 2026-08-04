#!/usr/bin/env python3
"""Prepare and lock the official BTCUSD_PERP 1m windows used by 6B."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import strategy_simulation  # noqa: F401 - activates sibling checkouts

from strategy_optimization import DatasetStatus, load_dataset_split
from strategy_optimization.datasets import (
    BINANCE_COINM_DAILY_AGG_TRADES_ROOT,
    BINANCE_COINM_MONTHLY_KLINE_ROOT,
    BinanceCoinMKlineClient,
    prepare_dataset,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPLIT = (
    PROJECT_ROOT
    / "research"
    / "protocols"
    / "btc_coinm_historical_split_v1.json"
)
DEFAULT_MANIFEST = (
    PROJECT_ROOT
    / "research"
    / "data_manifests"
    / "btc_coinm_historical_split_v1.json"
)
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "experiments" / "market_data"


def _write_json(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _assert_lock_is_stable(
    current,
    locked_document: dict[str, object],
    *,
    refresh_lock: bool,
) -> None:
    if current.status is not DatasetStatus.CONTENT_LOCKED:
        return
    if current.to_document() == locked_document:
        return
    if not refresh_lock:
        raise ValueError(
            "downloaded data differs from the existing CONTENT_LOCKED "
            "protocol; pass --refresh-lock only after reviewing an official "
            "Binance archive replacement"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument(
        "--base-url",
        default=BINANCE_COINM_MONTHLY_KLINE_ROOT,
    )
    parser.add_argument(
        "--agg-trade-base-url",
        default=BINANCE_COINM_DAILY_AGG_TRADES_ROOT,
    )
    parser.add_argument("--timeout-seconds", type=int, default=60)
    parser.add_argument("--refresh-lock", action="store_true")
    arguments = parser.parse_args()

    split_path = arguments.split.resolve()
    split = load_dataset_split(split_path)
    client = BinanceCoinMKlineClient(
        instrument=split.instrument,
        interval=split.interval,
        base_url=arguments.base_url,
        agg_trade_base_url=arguments.agg_trade_base_url,
        cache_root=(
            None
            if arguments.cache_root is None
            else arguments.cache_root.resolve()
        ),
        timeout_seconds=arguments.timeout_seconds,
    )

    def progress(window, day, index, total) -> None:
        print(
            f"[{window.role.value}] {day.isoformat()} "
            f"({index}/{total})",
            flush=True,
        )

    prepared = prepare_dataset(
        split,
        output_root=arguments.output_root.resolve(),
        client=client,
        progress=progress,
    )
    locked = prepared.locked_split_document()
    _assert_lock_is_stable(
        split,
        locked,
        refresh_lock=arguments.refresh_lock,
    )
    manifest = prepared.manifest_document(project_root=PROJECT_ROOT)
    _write_json(arguments.manifest.resolve(), manifest)
    _write_json(split_path, locked)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
