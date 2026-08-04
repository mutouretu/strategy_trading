#!/usr/bin/env python3
"""Normalize downloaded Binance Vision futures K-lines for experiments."""

from __future__ import annotations

import argparse
import csv
import io
import json
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import strategy_simulation  # noqa: F401 - activates sibling checkouts
from experiment_system import ParquetMarketStore
from market_protocol import MarketFrame


_INTERVAL_MILLISECONDS = 60_000


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "dates must use YYYY-MM-DD"
        ) from exc


def _timestamp(day: date) -> int:
    return int(
        datetime(
            day.year,
            day.month,
            day.day,
            tzinfo=timezone.utc,
        ).timestamp()
        * 1_000
    )


def _archives(root: Path) -> list[Path]:
    archives = sorted(root.glob("*.zip"))
    if not archives:
        raise ValueError(f"no zip archives found under {root}")
    return archives


def _rows(
    archive: Path,
    *,
    start_timestamp: int,
    end_timestamp: int,
) -> list[tuple[int, Decimal, Decimal, Decimal, Decimal]]:
    selected = []
    with zipfile.ZipFile(archive) as zipped:
        csv_members = [
            name for name in zipped.namelist() if name.endswith(".csv")
        ]
        if len(csv_members) != 1:
            raise ValueError(
                f"{archive} must contain exactly one CSV member"
            )
        with zipped.open(csv_members[0]) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8"))
            required = {"open_time", "open", "high", "low", "close"}
            if reader.fieldnames is None or not required <= set(
                reader.fieldnames
            ):
                raise ValueError(f"{archive} has an invalid K-line header")
            for row in reader:
                open_time = int(row["open_time"])
                if start_timestamp <= open_time < end_timestamp:
                    selected.append(
                        (
                            open_time,
                            Decimal(row["open"]),
                            Decimal(row["high"]),
                            Decimal(row["low"]),
                            Decimal(row["close"]),
                        )
                    )
    return selected


def import_klines(
    archive_root: Path,
    output_root: Path,
    *,
    instrument: str,
    start_date: date,
    end_date: date,
) -> dict[str, object]:
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    start_timestamp = _timestamp(start_date)
    end_timestamp = _timestamp(end_date + timedelta(days=1))
    values = [
        row
        for archive in _archives(archive_root)
        for row in _rows(
            archive,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
        )
    ]
    values.sort(key=lambda row: row[0])
    timestamps = [row[0] for row in values]
    if len(timestamps) != len(set(timestamps)):
        raise ValueError("source archives contain duplicate K-line timestamps")
    expected_count = (
        end_timestamp - start_timestamp
    ) // _INTERVAL_MILLISECONDS
    if len(values) != expected_count:
        raise ValueError(
            f"expected {expected_count} one-minute bars, got {len(values)}"
        )
    if any(
        timestamp != start_timestamp + index * _INTERVAL_MILLISECONDS
        for index, timestamp in enumerate(timestamps)
    ):
        raise ValueError("source archives have missing one-minute K-lines")

    frames = tuple(
        MarketFrame(
            sequence=index,
            timestamp=row[0],
            instrument=instrument,
            open=row[1],
            high=row[2],
            low=row[3],
            close=row[4],
        )
        for index, row in enumerate(values)
    )
    reference = ParquetMarketStore(output_root).persist(frames)
    closes = [frame.close for frame in frames]
    highs = [frame.high for frame in frames]
    lows = [frame.low for frame in frames]
    return {
        **reference.to_document(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "interval": "1m",
        "first_close": str(closes[0]),
        "last_close": str(closes[-1]),
        "minimum_low": str(min(lows)),
        "maximum_high": str(max(highs)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--instrument", default="BTCUSDT")
    parser.add_argument("--start-date", type=_date, required=True)
    parser.add_argument("--end-date", type=_date, required=True)
    arguments = parser.parse_args()
    document = import_klines(
        arguments.archive_root,
        arguments.output_root,
        instrument=arguments.instrument,
        start_date=arguments.start_date,
        end_date=arguments.end_date,
    )
    print(json.dumps(document, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
