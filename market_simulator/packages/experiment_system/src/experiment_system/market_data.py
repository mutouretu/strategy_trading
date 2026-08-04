"""Content-addressed Parquet storage for reusable market paths."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from market_protocol import MarketFrame

from .errors import ExperimentValidationError
from .hashing import canonical_json, sha256_document
from .json_values import to_plain_json


MARKET_DATASET_SCHEMA_VERSION = "market-path/v1"


@dataclass(frozen=True, slots=True)
class MarketReference:
    market_path_id: str
    content_hash: str
    file_sha256: str
    storage_path: str
    schema_version: str
    frame_count: int
    instrument: str

    def to_document(self) -> dict[str, object]:
        return {
            "market_path_id": self.market_path_id,
            "content_hash": self.content_hash,
            "file_sha256": self.file_sha256,
            "storage_path": self.storage_path,
            "schema_version": self.schema_version,
            "frame_count": self.frame_count,
            "instrument": self.instrument,
        }


def _decimal_string(value: Decimal) -> str:
    plain = to_plain_json(value)
    assert isinstance(plain, str)
    return plain


def market_frame_document(frame: MarketFrame) -> dict[str, object]:
    return {
        "sequence": frame.sequence,
        "timestamp": frame.timestamp,
        "instrument": frame.instrument,
        "open": _decimal_string(frame.open),
        "high": _decimal_string(frame.high),
        "low": _decimal_string(frame.low),
        "close": _decimal_string(frame.close),
        "features": {
            key: _decimal_string(value)
            for key, value in sorted(frame.features.items())
        },
    }


def market_content_hash(frames: Sequence[MarketFrame]) -> str:
    return sha256_document(
        {
            "schema_version": MARKET_DATASET_SCHEMA_VERSION,
            "frames": [market_frame_document(frame) for frame in frames],
        }
    )


def _decimal_type(values: Sequence[Decimal]) -> pa.DataType:
    maximum_scale = 0
    maximum_integer_digits = 1
    for value in values:
        if not value.is_finite():
            raise ExperimentValidationError(
                "market prices must be finite Decimals"
            )
        exponent = value.as_tuple().exponent
        scale = max(0, -exponent)
        integer_digits = max(1, value.adjusted() + 1)
        maximum_scale = max(maximum_scale, scale)
        maximum_integer_digits = max(
            maximum_integer_digits,
            integer_digits,
        )
    precision = maximum_integer_digits + maximum_scale
    if precision <= 38:
        return pa.decimal128(max(1, precision), maximum_scale)
    if precision <= 76:
        return pa.decimal256(precision, maximum_scale)
    raise ExperimentValidationError(
        "market price precision exceeds Parquet decimal256"
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ParquetMarketStore:
    """Persist each unique market path once under its semantic hash."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def persist(
        self,
        frames: Sequence[MarketFrame],
    ) -> MarketReference:
        if not frames:
            raise ExperimentValidationError(
                "cannot persist an empty market path"
            )
        instruments = {frame.instrument for frame in frames}
        if len(instruments) != 1:
            raise ExperimentValidationError(
                "one market path must contain exactly one instrument"
            )
        sequences = [frame.sequence for frame in frames]
        if sequences != sorted(sequences) or len(sequences) != len(
            set(sequences)
        ):
            raise ExperimentValidationError(
                "market path sequences must be unique and ordered"
            )

        content_hash = market_content_hash(frames)
        market_path_id = content_hash[:20]
        self.root.mkdir(parents=True, exist_ok=True)
        destination = self.root / f"{market_path_id}.parquet"
        if destination.exists():
            return self._existing_reference(
                destination,
                content_hash=content_hash,
                frame_count=len(frames),
                instrument=next(iter(instruments)),
            )

        prices = [
            price
            for frame in frames
            for price in (frame.open, frame.high, frame.low, frame.close)
        ]
        price_type = _decimal_type(prices)
        metadata = {
            b"schema_version": MARKET_DATASET_SCHEMA_VERSION.encode(),
            b"market_path_id": market_path_id.encode(),
            b"content_hash": content_hash.encode(),
            b"frame_count": str(len(frames)).encode(),
        }
        schema = pa.schema(
            [
                ("sequence", pa.int64()),
                ("timestamp", pa.int64()),
                ("instrument", pa.string()),
                ("open", price_type),
                ("high", price_type),
                ("low", price_type),
                ("close", price_type),
                ("features_json", pa.string()),
            ],
            metadata=metadata,
        )
        table = pa.Table.from_arrays(
            [
                pa.array(
                    [frame.sequence for frame in frames],
                    type=pa.int64(),
                ),
                pa.array(
                    [frame.timestamp for frame in frames],
                    type=pa.int64(),
                ),
                pa.array(
                    [frame.instrument for frame in frames],
                    type=pa.string(),
                ),
                pa.array([frame.open for frame in frames], type=price_type),
                pa.array([frame.high for frame in frames], type=price_type),
                pa.array([frame.low for frame in frames], type=price_type),
                pa.array([frame.close for frame in frames], type=price_type),
                pa.array(
                    [
                        canonical_json(
                            {
                                key: value
                                for key, value in frame.features.items()
                            }
                        )
                        for frame in frames
                    ],
                    type=pa.string(),
                ),
            ],
            schema=schema,
        )

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.root,
                prefix=f".{market_path_id}.",
                suffix=".parquet.tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
            pq.write_table(
                table,
                temporary_path,
                compression="zstd",
            )
            os.replace(temporary_path, destination)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise

        return MarketReference(
            market_path_id=market_path_id,
            content_hash=content_hash,
            file_sha256=_file_sha256(destination),
            storage_path=str(destination.resolve()),
            schema_version=MARKET_DATASET_SCHEMA_VERSION,
            frame_count=len(frames),
            instrument=next(iter(instruments)),
        )

    def load(
        self,
        reference: MarketReference,
    ) -> tuple[MarketFrame, ...]:
        """Load and verify one stored market path without mutation."""

        path = Path(reference.storage_path)
        if not path.is_file():
            raise ExperimentValidationError(
                f"market dataset {path} does not exist"
            )
        actual = self._existing_reference(
            path,
            content_hash=reference.content_hash,
            frame_count=reference.frame_count,
            instrument=reference.instrument,
        )
        if (
            actual.market_path_id != reference.market_path_id
            or actual.file_sha256 != reference.file_sha256
            or actual.schema_version != reference.schema_version
        ):
            raise ExperimentValidationError(
                f"market dataset {path} does not match its reference"
            )
        table = pq.read_table(path)
        columns = {
            name: table.column(name).to_pylist()
            for name in table.column_names
        }
        frames: list[MarketFrame] = []
        try:
            for index in range(table.num_rows):
                raw_features = json.loads(
                    columns["features_json"][index]
                )
                if not isinstance(raw_features, dict):
                    raise ValueError(
                        "features_json must contain an object"
                    )
                frames.append(
                    MarketFrame(
                        sequence=columns["sequence"][index],
                        timestamp=columns["timestamp"][index],
                        instrument=columns["instrument"][index],
                        open=columns["open"][index],
                        high=columns["high"][index],
                        low=columns["low"][index],
                        close=columns["close"][index],
                        features={
                            key: Decimal(value)
                            for key, value in raw_features.items()
                        },
                    )
                )
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise ExperimentValidationError(
                f"market dataset {path} content is invalid"
            ) from exc
        return tuple(frames)

    def _existing_reference(
        self,
        path: Path,
        *,
        content_hash: str,
        frame_count: int,
        instrument: str,
    ) -> MarketReference:
        try:
            table = pq.read_table(path)
        except Exception as exc:
            raise ExperimentValidationError(
                f"existing market dataset {path} is unreadable"
            ) from exc
        schema = table.schema
        metadata = schema.metadata or {}
        expected = {
            "schema_version": MARKET_DATASET_SCHEMA_VERSION,
            "market_path_id": content_hash[:20],
            "content_hash": content_hash,
            "frame_count": str(frame_count),
        }
        actual = {
            key: metadata.get(key.encode(), b"").decode()
            for key in expected
        }
        if actual != expected:
            raise ExperimentValidationError(
                f"existing market dataset {path} metadata does not match"
            )
        required_columns = {
            "sequence",
            "timestamp",
            "instrument",
            "open",
            "high",
            "low",
            "close",
            "features_json",
        }
        if set(table.column_names) != required_columns:
            raise ExperimentValidationError(
                f"existing market dataset {path} columns do not match"
            )
        try:
            columns = {
                name: table.column(name).to_pylist()
                for name in required_columns
            }
            frame_documents = []
            for index in range(table.num_rows):
                raw_features = json.loads(
                    columns["features_json"][index]
                )
                if not isinstance(raw_features, dict) or any(
                    not isinstance(key, str)
                    or not isinstance(value, str)
                    for key, value in raw_features.items()
                ):
                    raise ValueError(
                        "features_json must contain decimal strings"
                    )
                frame_documents.append(
                    {
                        "sequence": columns["sequence"][index],
                        "timestamp": columns["timestamp"][index],
                        "instrument": columns["instrument"][index],
                        "open": _decimal_string(
                            columns["open"][index]
                        ),
                        "high": _decimal_string(
                            columns["high"][index]
                        ),
                        "low": _decimal_string(
                            columns["low"][index]
                        ),
                        "close": _decimal_string(
                            columns["close"][index]
                        ),
                        "features": {
                            key: raw_features[key]
                            for key in sorted(raw_features)
                        },
                    }
                )
        except (
            AssertionError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise ExperimentValidationError(
                f"existing market dataset {path} content is invalid"
            ) from exc
        stored_content_hash = sha256_document(
            {
                "schema_version": MARKET_DATASET_SCHEMA_VERSION,
                "frames": frame_documents,
            }
        )
        if stored_content_hash != content_hash:
            raise ExperimentValidationError(
                f"existing market dataset {path} content does not match"
            )
        if (
            table.num_rows != frame_count
            or set(columns["instrument"]) != {instrument}
        ):
            raise ExperimentValidationError(
                f"existing market dataset {path} identity does not match"
            )
        return MarketReference(
            market_path_id=content_hash[:20],
            content_hash=content_hash,
            file_sha256=_file_sha256(path),
            storage_path=str(path.resolve()),
            schema_version=MARKET_DATASET_SCHEMA_VERSION,
            frame_count=frame_count,
            instrument=instrument,
        )
