"""Deterministic market source backed by normalized Parquet OHLC data."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
from market_protocol import MarketFrame

from .fixed import _FixedFramesMarketSource


_REQUIRED_COLUMNS = {
    "sequence",
    "timestamp",
    "instrument",
    "open",
    "high",
    "low",
    "close",
}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ParquetMarketSource(_FixedFramesMarketSource):
    """Load one immutable, ordered OHLC path from a local Parquet file.

    The expected identity fields make an ignored local market-data asset safe
    to reference from a versioned experiment specification: replacing the
    file, changing its instrument or truncating its rows fails before a Run.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        expected_instrument: str,
        expected_file_sha256: str,
        expected_frame_count: int,
        expected_content_sha256: str | None = None,
        step_milliseconds: int | None = None,
    ) -> None:
        source_path = Path(path)
        if not source_path.is_file():
            raise ValueError(f"market parquet does not exist: {source_path}")
        if not expected_instrument.strip():
            raise ValueError("expected_instrument must not be empty")
        if len(expected_file_sha256) != 64 or any(
            character not in "0123456789abcdef"
            for character in expected_file_sha256.lower()
        ):
            raise ValueError("expected_file_sha256 must be a SHA-256 hex digest")
        if expected_frame_count < 1:
            raise ValueError("expected_frame_count must be >= 1")
        if expected_content_sha256 is not None and (
            len(expected_content_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in expected_content_sha256.lower()
            )
        ):
            raise ValueError(
                "expected_content_sha256 must be a SHA-256 hex digest"
            )
        if step_milliseconds is not None and step_milliseconds <= 0:
            raise ValueError("step_milliseconds must be > 0")

        actual_file_sha256 = _file_sha256(source_path)
        if actual_file_sha256 != expected_file_sha256.lower():
            raise ValueError(
                "market parquet SHA-256 mismatch: "
                f"expected {expected_file_sha256.lower()}, "
                f"got {actual_file_sha256}"
            )
        table = pq.read_table(source_path)
        if expected_content_sha256 is not None:
            metadata = table.schema.metadata or {}
            actual_content_sha256 = metadata.get(b"content_hash", b"").decode()
            if actual_content_sha256 != expected_content_sha256.lower():
                raise ValueError(
                    "market parquet content SHA-256 mismatch: "
                    f"expected {expected_content_sha256.lower()}, "
                    f"got {actual_content_sha256 or '<missing>'}"
                )
        missing = _REQUIRED_COLUMNS - set(table.column_names)
        if missing:
            raise ValueError(
                f"market parquet is missing columns: {sorted(missing)}"
            )
        if table.num_rows != expected_frame_count:
            raise ValueError(
                "market parquet frame count mismatch: "
                f"expected {expected_frame_count}, got {table.num_rows}"
            )
        columns = {
            name: table.column(name).to_pylist()
            for name in _REQUIRED_COLUMNS
        }
        raw_features = (
            table.column("features_json").to_pylist()
            if "features_json" in table.column_names
            else ["{}"] * table.num_rows
        )
        frames: list[MarketFrame] = []
        for index in range(table.num_rows):
            feature_document = json.loads(raw_features[index])
            if not isinstance(feature_document, dict):
                raise ValueError("features_json must contain an object")
            frames.append(
                MarketFrame(
                    sequence=int(columns["sequence"][index]),
                    timestamp=int(columns["timestamp"][index]),
                    instrument=str(columns["instrument"][index]),
                    open=Decimal(columns["open"][index]),
                    high=Decimal(columns["high"][index]),
                    low=Decimal(columns["low"][index]),
                    close=Decimal(columns["close"][index]),
                    features={
                        str(key): Decimal(value)
                        for key, value in feature_document.items()
                    },
                )
            )
        sequences = [frame.sequence for frame in frames]
        if sequences != list(range(expected_frame_count)):
            raise ValueError(
                "market parquet sequences must be contiguous from zero"
            )
        instruments = {frame.instrument for frame in frames}
        if instruments != {expected_instrument}:
            raise ValueError(
                "market parquet instrument mismatch: "
                f"expected {expected_instrument!r}, got {sorted(instruments)!r}"
            )
        timestamps = [frame.timestamp for frame in frames]
        if timestamps != sorted(timestamps) or len(set(timestamps)) != len(
            timestamps
        ):
            raise ValueError(
                "market parquet timestamps must be unique and ordered"
            )
        if step_milliseconds is not None and any(
            later - earlier != step_milliseconds
            for earlier, later in zip(timestamps, timestamps[1:])
        ):
            raise ValueError(
                "market parquet timestamps do not match step_milliseconds"
            )

        self.path = source_path.resolve()
        self.instrument = expected_instrument
        self.file_sha256 = actual_file_sha256
        self.content_sha256 = expected_content_sha256
        super().__init__(tuple(frames))
