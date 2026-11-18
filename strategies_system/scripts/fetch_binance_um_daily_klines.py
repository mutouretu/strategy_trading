#!/usr/bin/env python3
"""Fetch verified Binance USD-M daily 1m archives and import a market path."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import tempfile
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path

import certifi

from import_binance_futures_klines import import_klines


BASE_URL = "https://data.binance.vision/data/futures/um/daily/klines"


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from exc


def _days(start: date, end: date) -> tuple[date, ...]:
    if end < start:
        raise ValueError("end-date must be on or after start-date")
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


def _get(url: str) -> bytes:
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(url, context=context, timeout=30) as response:
        return response.read()


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def fetch_archives(
    *,
    symbol: str,
    start_date: date,
    end_date: date,
    archive_root: Path,
) -> list[dict[str, str]]:
    if not symbol.isalnum() or symbol.upper() != symbol:
        raise ValueError("symbol must contain only uppercase letters and digits")
    records = []
    for day in _days(start_date, end_date):
        filename = f"{symbol}-1m-{day.isoformat()}.zip"
        url = f"{BASE_URL}/{symbol}/1m/{filename}"
        checksum_text = _get(f"{url}.CHECKSUM").decode("ascii").strip()
        checksum_parts = checksum_text.split()
        if len(checksum_parts) != 2 or checksum_parts[1] != filename:
            raise ValueError(f"invalid published checksum for {filename}")
        expected_sha256 = checksum_parts[0].lower()
        if len(expected_sha256) != 64 or any(
            digit not in "0123456789abcdef" for digit in expected_sha256
        ):
            raise ValueError(f"invalid SHA-256 for {filename}")
        destination = archive_root / filename
        content = destination.read_bytes() if destination.exists() else b""
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            content = _get(url)
            if hashlib.sha256(content).hexdigest() != expected_sha256:
                raise ValueError(f"checksum mismatch for {filename}")
            _write_atomic(destination, content)
        if not zipfile.is_zipfile(destination):
            raise ValueError(f"invalid ZIP archive: {filename}")
        records.append({
            "date": day.isoformat(),
            "url": url,
            "sha256": expected_sha256,
        })
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="SOXLUSDT")
    parser.add_argument("--start-date", type=_date, required=True)
    parser.add_argument("--end-date", type=_date, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    arguments = parser.parse_args()

    archive_root = (
        arguments.output_root / "archives" / arguments.symbol
        / f"{arguments.start_date}_{arguments.end_date}" / "1m"
    )
    sources = fetch_archives(
        symbol=arguments.symbol,
        start_date=arguments.start_date,
        end_date=arguments.end_date,
        archive_root=archive_root,
    )
    reference = import_klines(
        archive_root,
        arguments.output_root,
        instrument=arguments.symbol,
        start_date=arguments.start_date,
        end_date=arguments.end_date,
    )
    manifest = {
        "schema_version": "binance-um-daily-klines-import/v1",
        "source": "Binance Vision USD-M futures 1m trade-price K-lines",
        "symbol": arguments.symbol,
        "interval": "1m",
        "timezone": "UTC",
        "start_date": arguments.start_date.isoformat(),
        "end_date": arguments.end_date.isoformat(),
        "archives": sources,
        "market_path": reference,
    }
    manifest_path = archive_root.parent / "manifest.json"
    _write_atomic(
        manifest_path,
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode(),
    )
    print(json.dumps({"manifest": str(manifest_path), **reference}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
