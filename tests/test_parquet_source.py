from __future__ import annotations

import hashlib
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from market_simulator import ParquetMarketSource


class ParquetMarketSourceTests(unittest.TestCase):
    def write_market(
        self,
        path: Path,
        *,
        content_sha256: str | None = None,
    ) -> str:
        table = pa.table(
            {
                "sequence": [0, 1],
                "timestamp": [1_000, 61_000],
                "instrument": ["BTCUSDT", "BTCUSDT"],
                "open": [Decimal("100.0"), Decimal("101.0")],
                "high": [Decimal("102.0"), Decimal("103.0")],
                "low": [Decimal("99.0"), Decimal("100.0")],
                "close": [Decimal("101.0"), Decimal("102.0")],
                "features_json": ["{}", "{}"],
            }
        )
        if content_sha256 is not None:
            table = table.replace_schema_metadata(
                {b"content_hash": content_sha256.encode()}
            )
        pq.write_table(table, path)
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_loads_verified_contiguous_frames(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.parquet"
            digest = self.write_market(path)

            source = ParquetMarketSource(
                path,
                expected_instrument="BTCUSDT",
                expected_file_sha256=digest,
                expected_frame_count=2,
                step_milliseconds=60_000,
            )

            self.assertEqual(source.instrument, "BTCUSDT")
            self.assertEqual(source.reset().close, Decimal("101.0"))
            self.assertEqual(source.next().close, Decimal("102.0"))
            self.assertTrue(source.done)

    def test_rejects_changed_file_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.parquet"
            self.write_market(path)

            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                ParquetMarketSource(
                    path,
                    expected_instrument="BTCUSDT",
                    expected_file_sha256="0" * 64,
                    expected_frame_count=2,
                    step_milliseconds=60_000,
                )

    def test_rejects_changed_semantic_content_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.parquet"
            digest = self.write_market(path, content_sha256="1" * 64)

            with self.assertRaisesRegex(ValueError, "content SHA-256 mismatch"):
                ParquetMarketSource(
                    path,
                    expected_instrument="BTCUSDT",
                    expected_file_sha256=digest,
                    expected_frame_count=2,
                    expected_content_sha256="2" * 64,
                    step_milliseconds=60_000,
                )


if __name__ == "__main__":
    unittest.main()
