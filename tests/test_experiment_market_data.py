from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from examples.deterministic_probe import run_probe
from experiment_system import (
    MARKET_DATASET_SCHEMA_VERSION,
    ExperimentValidationError,
    ParquetMarketStore,
)


class ParquetMarketStoreTests(unittest.TestCase):
    def test_persists_exact_content_addressed_ohlc_once(self) -> None:
        frames = run_probe().frames
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "market_data"
            store = ParquetMarketStore(root)

            first = store.persist(frames)
            second = store.persist(frames)

            self.assertEqual(first, second)
            self.assertEqual(first.frame_count, len(frames))
            self.assertEqual(first.instrument, "BTCUSD")
            self.assertEqual(
                first.schema_version,
                MARKET_DATASET_SCHEMA_VERSION,
            )
            self.assertEqual(
                [path.resolve() for path in root.glob("*.parquet")],
                [Path(first.storage_path)],
            )

            table = pq.read_table(first.storage_path)
            self.assertEqual(
                table.column("open").to_pylist(),
                [frame.open for frame in frames],
            )
            self.assertEqual(
                table.column("high").to_pylist(),
                [frame.high for frame in frames],
            )
            self.assertEqual(
                table.column("low").to_pylist(),
                [frame.low for frame in frames],
            )
            self.assertEqual(
                table.column("close").to_pylist(),
                [frame.close for frame in frames],
            )
            self.assertEqual(
                table.column("features_json").to_pylist(),
                ["{}"] * len(frames),
            )
            metadata = table.schema.metadata or {}
            self.assertEqual(
                metadata[b"content_hash"].decode(),
                first.content_hash,
            )

    def test_changed_frame_creates_a_distinct_market_path(self) -> None:
        frames = run_probe().frames
        changed = (
            replace(
                frames[0],
                high=Decimal("103"),
                close=Decimal("102"),
            ),
            *frames[1:],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ParquetMarketStore(root)

            original = store.persist(frames)
            modified = store.persist(changed)

            self.assertNotEqual(
                original.market_path_id,
                modified.market_path_id,
            )
            self.assertEqual(len(list(root.glob("*.parquet"))), 2)

    def test_rejects_empty_mixed_or_unordered_market_paths(self) -> None:
        frames = run_probe().frames
        store = ParquetMarketStore("unused")

        with self.assertRaisesRegex(
            ExperimentValidationError,
            "empty",
        ):
            store.persist(())
        with self.assertRaisesRegex(
            ExperimentValidationError,
            "one instrument",
        ):
            store.persist(
                (
                    frames[0],
                    replace(frames[1], instrument="ETHUSD"),
                )
            )
        with self.assertRaisesRegex(
            ExperimentValidationError,
            "unique and ordered",
        ):
            store.persist((frames[1], frames[0]))

    def test_existing_file_content_is_checked_not_only_metadata(
        self,
    ) -> None:
        frames = run_probe().frames
        with tempfile.TemporaryDirectory() as directory:
            store = ParquetMarketStore(directory)
            reference = store.persist(frames)
            table = pq.read_table(reference.storage_path)
            closes = table.column("close").to_pylist()
            closes[0] = Decimal("100")
            changed = table.set_column(
                table.column_names.index("close"),
                "close",
                pa.array(
                    closes,
                    type=table.schema.field("close").type,
                ),
            )
            pq.write_table(
                changed,
                reference.storage_path,
                compression="zstd",
            )

            with self.assertRaisesRegex(
                ExperimentValidationError,
                "content does not match",
            ):
                store.persist(frames)


if __name__ == "__main__":
    unittest.main()
