from __future__ import annotations

import tempfile
import unittest
import csv
import io
import zipfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import strategy_optimization  # noqa: F401 - activates local checkouts

from strategy_optimization import (
    DatasetRole,
    DatasetSplitSpec,
    DatasetStatus,
    DatasetWindow,
)
from strategy_optimization.datasets import (
    ONE_MINUTE_BARS_PER_DAY,
    ONE_MINUTE_MILLISECONDS,
    DailyKline,
    FetchedDailyKlines,
    SourceArchive,
    _day_timestamp,
    _klines_from_agg_trades,
    prepare_dataset,
)


class FakeClient:
    instrument = "BTCUSD_PERP"
    interval = "1m"

    def fetch_day(self, day: date) -> FetchedDailyKlines:
        start = _day_timestamp(day)
        rows = tuple(
            DailyKline(
                timestamp=start + index * ONE_MINUTE_MILLISECONDS,
                open=Decimal("60000") + index,
                high=Decimal("60002") + index,
                low=Decimal("59999") + index,
                close=Decimal("60001") + index,
            )
            for index in range(ONE_MINUTE_BARS_PER_DAY)
        )
        name = f"BTCUSD_PERP-1m-{day.isoformat()}.zip"
        return FetchedDailyKlines(
            source=SourceArchive(
                day=day,
                file_name=name,
                url=f"https://example.test/{name}",
                sha256="a" * 64,
                frame_count=len(rows),
                data_type="klines",
                frequency="daily",
            ),
            rows=rows,
        )


def split() -> DatasetSplitSpec:
    start = date(2026, 1, 1)
    roles = (
        DatasetRole.TRAIN,
        DatasetRole.VALIDATION,
        DatasetRole.HOLDOUT,
    )
    return DatasetSplitSpec(
        split_id="test-split",
        status=DatasetStatus.BOUNDARIES_LOCKED,
        source="test source",
        instrument="BTCUSD_PERP",
        interval="1m",
        windows=tuple(
            DatasetWindow(
                key=f"window-{index}",
                role=role,
                market_key=f"market-{index}",
                start=start + timedelta(days=index),
                end_exclusive=start + timedelta(days=index + 1),
            )
            for index, role in enumerate(roles)
        ),
    )


class DatasetPreparationTests(unittest.TestCase):
    def test_aggregate_trades_reconstruct_a_complete_ohlc_day(self) -> None:
        day = date(2026, 6, 29)
        start = _day_timestamp(day)
        csv_text = io.StringIO()
        writer = csv.writer(csv_text)
        writer.writerow(
            (
                "agg_trade_id",
                "price",
                "quantity",
                "first_trade_id",
                "last_trade_id",
                "transact_time",
                "is_buyer_maker",
            )
        )
        trade_id = 1
        for index in range(ONE_MINUTE_BARS_PER_DAY):
            timestamp = start + index * ONE_MINUTE_MILLISECONDS
            writer.writerow(
                (trade_id, "60000", "1", trade_id, trade_id, timestamp, "false")
            )
            trade_id += 1
            writer.writerow(
                (
                    trade_id,
                    "60001",
                    "1",
                    trade_id,
                    trade_id,
                    timestamp + 1,
                    "true",
                )
            )
            trade_id += 1
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as zipped:
            zipped.writestr("aggTrades.csv", csv_text.getvalue())

        rows, trade_count = _klines_from_agg_trades(
            payload.getvalue(),
            file_name="aggTrades.zip",
            day=day,
        )

        self.assertEqual(trade_count, ONE_MINUTE_BARS_PER_DAY * 2)
        self.assertEqual(len(rows), ONE_MINUTE_BARS_PER_DAY)
        self.assertEqual(rows[0].open, Decimal("60000"))
        self.assertEqual(rows[0].high, Decimal("60001"))
        self.assertEqual(rows[0].low, Decimal("60000"))
        self.assertEqual(rows[0].close, Decimal("60001"))

    def test_prepare_persists_three_semantically_locked_windows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            prepared = prepare_dataset(
                split(),
                output_root=project_root / "market_data",
                client=FakeClient(),
            )

            self.assertEqual(len(prepared.windows), 3)
            self.assertTrue(
                all(
                    item.reference.frame_count == ONE_MINUTE_BARS_PER_DAY
                    for item in prepared.windows
                )
            )
            locked = prepared.locked_split_document()
            self.assertEqual(locked["status"], "CONTENT_LOCKED")
            self.assertTrue(
                all(
                    len(window["content_sha256"]) == 64
                    for window in locked["windows"]
                )
            )
            manifest = prepared.manifest_document(project_root=project_root)
            self.assertEqual(manifest["status"], "CONTENT_LOCKED")
            self.assertEqual(
                manifest["windows"][0]["frame_count"],
                ONE_MINUTE_BARS_PER_DAY,
            )
            self.assertEqual(
                len(manifest["windows"][0]["source_archives"]),
                1,
            )

    def test_repeated_preparation_has_stable_content_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "market_data"
            first = prepare_dataset(split(), output_root=root, client=FakeClient())
            second = prepare_dataset(split(), output_root=root, client=FakeClient())

            self.assertEqual(
                [item.reference.content_hash for item in first.windows],
                [item.reference.content_hash for item in second.windows],
            )
            self.assertEqual(
                [item.reference.file_sha256 for item in first.windows],
                [item.reference.file_sha256 for item in second.windows],
            )


if __name__ == "__main__":
    unittest.main()
