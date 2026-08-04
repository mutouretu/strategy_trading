"""Reproducible preparation of content-locked historical market windows."""

from __future__ import annotations

import csv
import hashlib
import io
import ssl
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.request import Request, urlopen

import certifi
from experiment_system import MarketReference, ParquetMarketStore
from market_protocol import MarketFrame

from .errors import StudyConfigError
from .models import DatasetSplitSpec, DatasetStatus, DatasetWindow


BINANCE_COINM_MONTHLY_KLINE_ROOT = (
    "https://data.binance.vision/data/futures/cm/monthly/klines"
)
BINANCE_COINM_DAILY_AGG_TRADES_ROOT = (
    "https://data.binance.vision/data/futures/cm/daily/aggTrades"
)
ONE_MINUTE_MILLISECONDS = 60_000
ONE_DAY_MILLISECONDS = 86_400_000
ONE_MINUTE_BARS_PER_DAY = 1_440


def _day_timestamp(day: date) -> int:
    return int(
        datetime(
            day.year,
            day.month,
            day.day,
            tzinfo=timezone.utc,
        ).timestamp()
        * 1_000
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class DailyKline:
    timestamp: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True, slots=True)
class SourceArchive:
    day: date
    file_name: str
    url: str
    sha256: str
    frame_count: int
    data_type: str = "klines"
    frequency: str = "monthly"

    def to_document(self) -> dict[str, object]:
        return {
            "date": self.day.isoformat(),
            "file_name": self.file_name,
            "url": self.url,
            "sha256": self.sha256,
            "frame_count": self.frame_count,
            "data_type": self.data_type,
            "frequency": self.frequency,
        }


@dataclass(frozen=True, slots=True)
class FetchedDailyKlines:
    source: SourceArchive
    rows: tuple[DailyKline, ...]


@dataclass(frozen=True, slots=True)
class PreparedWindow:
    window: DatasetWindow
    reference: MarketReference
    archives: tuple[SourceArchive, ...]
    first_timestamp: int
    last_timestamp: int
    first_close: Decimal
    last_close: Decimal
    minimum_low: Decimal
    maximum_high: Decimal

    def to_document(self, *, project_root: Path) -> dict[str, object]:
        storage_path = Path(self.reference.storage_path)
        try:
            relative_path = storage_path.relative_to(project_root.resolve())
            path_value = relative_path.as_posix()
        except ValueError:
            path_value = str(storage_path)
        return {
            "key": self.window.key,
            "role": self.window.role.value,
            "market_key": self.window.market_key,
            "start": self.window.start.isoformat(),
            "end_exclusive": self.window.end_exclusive.isoformat(),
            "path": path_value,
            "market_path_id": self.reference.market_path_id,
            "content_sha256": self.reference.content_hash,
            "file_sha256": self.reference.file_sha256,
            "frame_count": self.reference.frame_count,
            "instrument": self.reference.instrument,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
            "first_close": str(self.first_close),
            "last_close": str(self.last_close),
            "minimum_low": str(self.minimum_low),
            "maximum_high": str(self.maximum_high),
            "source_archives": [item.to_document() for item in self.archives],
        }


@dataclass(frozen=True, slots=True)
class PreparedDataset:
    split: DatasetSplitSpec
    windows: tuple[PreparedWindow, ...]

    def locked_split_document(self) -> dict[str, object]:
        document = self.split.to_document()
        document["status"] = DatasetStatus.CONTENT_LOCKED.value
        hashes = {
            item.window.key: item.reference.content_hash
            for item in self.windows
        }
        for window in document["windows"]:
            assert isinstance(window, dict)
            window["content_sha256"] = hashes[str(window["key"])]
        return document

    def manifest_document(self, *, project_root: Path) -> dict[str, object]:
        return {
            "schema_version": "historical-market-manifest/v1",
            "split_id": self.split.split_id,
            "source": self.split.source,
            "instrument": self.split.instrument,
            "interval": self.split.interval,
            "proxy_market": self.split.proxy_market,
            "status": DatasetStatus.CONTENT_LOCKED.value,
            "windows": [
                item.to_document(project_root=project_root)
                for item in self.windows
            ],
        }


class BinanceCoinMKlineClient:
    """Download official monthly archives and expose validated UTC days."""

    def __init__(
        self,
        *,
        instrument: str,
        interval: str,
        base_url: str = BINANCE_COINM_MONTHLY_KLINE_ROOT,
        agg_trade_base_url: str = BINANCE_COINM_DAILY_AGG_TRADES_ROOT,
        cache_root: Path | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        if not instrument.strip():
            raise ValueError("instrument must not be empty")
        if interval != "1m":
            raise ValueError("6B Binance COIN-M preparation supports only 1m")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        self.instrument = instrument
        self.interval = interval
        self.base_url = base_url.rstrip("/")
        self.agg_trade_base_url = agg_trade_base_url.rstrip("/")
        self.cache_root = cache_root
        self.timeout_seconds = timeout_seconds
        self._months: dict[
            str,
            tuple[SourceArchive, dict[date, tuple[DailyKline, ...]]],
        ] = {}

    def fetch_day(self, day: date) -> FetchedDailyKlines:
        month_key = day.strftime("%Y-%m")
        if month_key not in self._months:
            self._months[month_key] = self._fetch_month(day)
        source, month_days = self._months[month_key]
        rows = month_days.get(day, ())
        try:
            _validate_daily_rows(rows, day=day, name=source.file_name)
        except StudyConfigError:
            return self._fetch_agg_trades_day(day)
        return FetchedDailyKlines(source=source, rows=rows)

    def _fetch_month(
        self,
        day: date,
    ) -> tuple[SourceArchive, dict[date, tuple[DailyKline, ...]]]:
        month_start = day.replace(day=1)
        next_month = (
            date(month_start.year + 1, 1, 1)
            if month_start.month == 12
            else date(month_start.year, month_start.month + 1, 1)
        )
        month_key = month_start.strftime("%Y-%m")
        file_name = f"{self.instrument}-{self.interval}-{month_key}.zip"
        url = (
            f"{self.base_url}/{self.instrument}/{self.interval}/{file_name}"
        )
        expected_sha256 = self._checksum(url, file_name)
        payload = self._archive_payload(url, file_name, expected_sha256)
        actual_sha256 = _sha256(payload)
        if actual_sha256 != expected_sha256:
            raise StudyConfigError(
                f"Binance archive checksum mismatch for {file_name}: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )
        rows = _parse_archive(
            payload,
            file_name=file_name,
        )
        _validate_month_archive_rows(
            rows,
            start=month_start,
            end_exclusive=next_month,
            name=file_name,
        )
        by_day: dict[date, list[DailyKline]] = {}
        for row in rows:
            row_day = datetime.fromtimestamp(
                row.timestamp / 1_000,
                timezone.utc,
            ).date()
            by_day.setdefault(row_day, []).append(row)
        return (
            SourceArchive(
                day=month_start,
                file_name=file_name,
                url=url,
                sha256=actual_sha256,
                frame_count=len(rows),
            ),
            {key: tuple(value) for key, value in by_day.items()},
        )

    def _fetch_agg_trades_day(self, day: date) -> FetchedDailyKlines:
        file_name = f"{self.instrument}-aggTrades-{day.isoformat()}.zip"
        url = f"{self.agg_trade_base_url}/{self.instrument}/{file_name}"
        expected_sha256 = self._checksum(url, file_name)
        payload = self._archive_payload(url, file_name, expected_sha256)
        actual_sha256 = _sha256(payload)
        if actual_sha256 != expected_sha256:
            raise StudyConfigError(
                f"Binance archive checksum mismatch for {file_name}: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )
        rows, trade_count = _klines_from_agg_trades(
            payload,
            file_name=file_name,
            day=day,
        )
        return FetchedDailyKlines(
            source=SourceArchive(
                day=day,
                file_name=file_name,
                url=url,
                sha256=actual_sha256,
                frame_count=trade_count,
                data_type="aggTrades",
                frequency="daily",
            ),
            rows=rows,
        )

    def _get(self, url: str) -> bytes:
        request = Request(
            url,
            headers={"User-Agent": "strategies-system/6B-market-lock"},
        )
        tls_context = ssl.create_default_context(cafile=certifi.where())
        try:
            with urlopen(
                request,
                timeout=self.timeout_seconds,
                context=tls_context,
            ) as response:
                return response.read()
        except OSError as exc:
            raise StudyConfigError(f"cannot download {url}: {exc}") from exc

    def _checksum(self, url: str, file_name: str) -> str:
        try:
            text = self._get(f"{url}.CHECKSUM").decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise StudyConfigError(
                f"Binance checksum for {file_name} is not ASCII"
            ) from exc
        fields = text.split()
        if len(fields) != 2 or fields[1].lstrip("*") != file_name:
            raise StudyConfigError(
                f"Binance checksum document for {file_name} is invalid"
            )
        digest = fields[0].lower()
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise StudyConfigError(
                f"Binance checksum for {file_name} is not SHA-256"
            )
        return digest

    def _archive_payload(
        self,
        url: str,
        file_name: str,
        expected_sha256: str,
    ) -> bytes:
        if self.cache_root is None:
            return self._get(url)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        path = self.cache_root / file_name
        if path.is_file():
            payload = path.read_bytes()
            if _sha256(payload) == expected_sha256:
                return payload
        payload = self._get(url)
        if _sha256(payload) != expected_sha256:
            return payload
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_bytes(payload)
        temporary.replace(path)
        return payload


def _parse_archive(
    payload: bytes,
    *,
    file_name: str,
) -> tuple[DailyKline, ...]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zipped:
            members = [
                name for name in zipped.namelist() if name.endswith(".csv")
            ]
            if len(members) != 1:
                raise StudyConfigError(
                    f"{file_name} must contain exactly one CSV member"
                )
            with zipped.open(members[0]) as raw:
                reader = csv.DictReader(
                    io.TextIOWrapper(raw, encoding="utf-8")
                )
                required = {"open_time", "open", "high", "low", "close"}
                if reader.fieldnames is None or not required <= set(
                    reader.fieldnames
                ):
                    raise StudyConfigError(
                        f"{file_name} has an invalid K-line header"
                    )
                rows = tuple(
                    DailyKline(
                        timestamp=int(row["open_time"]),
                        open=Decimal(row["open"]),
                        high=Decimal(row["high"]),
                        low=Decimal(row["low"]),
                        close=Decimal(row["close"]),
                    )
                    for row in reader
                )
    except (zipfile.BadZipFile, KeyError, ValueError, ArithmeticError) as exc:
        raise StudyConfigError(
            f"cannot parse Binance K-lines from {file_name}: {exc}"
        ) from exc
    return rows


def _validate_month_archive_rows(
    rows: tuple[DailyKline, ...],
    *,
    start: date,
    end_exclusive: date,
    name: str,
) -> None:
    start_timestamp = _day_timestamp(start)
    end_timestamp = _day_timestamp(end_exclusive)
    previous: int | None = None
    for index, row in enumerate(rows):
        if not start_timestamp <= row.timestamp < end_timestamp:
            raise StudyConfigError(
                f"{name} contains an out-of-month bar at index {index}"
            )
        if row.timestamp % ONE_MINUTE_MILLISECONDS or (
            previous is not None and row.timestamp <= previous
        ):
            raise StudyConfigError(
                f"{name} contains a duplicate or unordered bar at index {index}"
            )
        previous = row.timestamp
        _validate_ohlc(row, name=name)


def _klines_from_agg_trades(
    payload: bytes,
    *,
    file_name: str,
    day: date,
) -> tuple[tuple[DailyKline, ...], int]:
    minute_prices: dict[int, list[Decimal]] = {}
    trade_count = 0
    previous_timestamp: int | None = None
    start = _day_timestamp(day)
    end = start + ONE_DAY_MILLISECONDS
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zipped:
            members = [
                name for name in zipped.namelist() if name.endswith(".csv")
            ]
            if len(members) != 1:
                raise StudyConfigError(
                    f"{file_name} must contain exactly one CSV member"
                )
            with zipped.open(members[0]) as raw:
                reader = csv.DictReader(
                    io.TextIOWrapper(raw, encoding="utf-8")
                )
                required = {"price", "transact_time"}
                if reader.fieldnames is None or not required <= set(
                    reader.fieldnames
                ):
                    raise StudyConfigError(
                        f"{file_name} has an invalid aggregate-trade header"
                    )
                for item in reader:
                    timestamp = int(item["transact_time"])
                    if not start <= timestamp < end:
                        raise StudyConfigError(
                            f"{file_name} contains an out-of-day trade"
                        )
                    if (
                        previous_timestamp is not None
                        and timestamp < previous_timestamp
                    ):
                        raise StudyConfigError(
                            f"{file_name} trades are not time ordered"
                        )
                    previous_timestamp = timestamp
                    minute = timestamp - timestamp % ONE_MINUTE_MILLISECONDS
                    minute_prices.setdefault(minute, []).append(
                        Decimal(item["price"])
                    )
                    trade_count += 1
    except (zipfile.BadZipFile, KeyError, ValueError, ArithmeticError) as exc:
        raise StudyConfigError(
            f"cannot parse Binance aggregate trades from {file_name}: {exc}"
        ) from exc
    rows = tuple(
        DailyKline(
            timestamp=timestamp,
            open=prices[0],
            high=max(prices),
            low=min(prices),
            close=prices[-1],
        )
        for timestamp, prices in sorted(minute_prices.items())
    )
    _validate_daily_rows(rows, day=day, name=file_name)
    return rows, trade_count


def _validate_daily_rows(
    rows: tuple[DailyKline, ...],
    *,
    day: date,
    name: str,
) -> None:
    if len(rows) != ONE_MINUTE_BARS_PER_DAY:
        raise StudyConfigError(
            f"{name} must contain {ONE_MINUTE_BARS_PER_DAY} one-minute "
            f"bars, got {len(rows)}"
        )
    start = _day_timestamp(day)
    for index, row in enumerate(rows):
        if row.timestamp != start + index * ONE_MINUTE_MILLISECONDS:
            raise StudyConfigError(
                f"{name} has a missing or unordered bar at index {index}"
            )
        _validate_ohlc(row, name=name)


def _validate_ohlc(row: DailyKline, *, name: str) -> None:
    if min(row.open, row.high, row.low, row.close) <= 0:
        raise StudyConfigError(f"{name} contains a non-positive price")
    if row.high < max(row.open, row.close) or row.low > min(
        row.open, row.close
    ):
        raise StudyConfigError(f"{name} contains an invalid OHLC bar")


def _days(window: DatasetWindow):
    current = window.start
    while current < window.end_exclusive:
        yield current
        current += timedelta(days=1)


def prepare_dataset(
    split: DatasetSplitSpec,
    *,
    output_root: Path,
    client: BinanceCoinMKlineClient,
    progress: Callable[[DatasetWindow, date, int, int], None] | None = None,
) -> PreparedDataset:
    """Download, validate and persist every frozen split window."""

    if split.instrument != client.instrument:
        raise StudyConfigError(
            "dataset instrument does not match Binance client instrument"
        )
    if split.interval != client.interval or split.interval != "1m":
        raise StudyConfigError("6B dataset preparation requires interval=1m")
    prepared: list[PreparedWindow] = []
    store = ParquetMarketStore(output_root)
    for window in split.windows:
        dates = tuple(_days(window))
        values: list[DailyKline] = []
        archives: list[SourceArchive] = []
        archive_names: set[str] = set()
        for index, day in enumerate(dates, start=1):
            fetched = client.fetch_day(day)
            _validate_daily_rows(
                fetched.rows,
                day=day,
                name=fetched.source.file_name,
            )
            values.extend(fetched.rows)
            if fetched.source.file_name not in archive_names:
                archives.append(fetched.source)
                archive_names.add(fetched.source.file_name)
            if progress is not None:
                progress(window, day, index, len(dates))
        expected_count = len(dates) * ONE_MINUTE_BARS_PER_DAY
        if len(values) != expected_count:
            raise StudyConfigError(
                f"window {window.key!r} expected {expected_count} bars, "
                f"got {len(values)}"
            )
        frames = tuple(
            MarketFrame(
                sequence=index,
                timestamp=row.timestamp,
                instrument=split.instrument,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
            )
            for index, row in enumerate(values)
        )
        first_expected = _day_timestamp(window.start)
        last_expected = (
            _day_timestamp(window.end_exclusive) - ONE_MINUTE_MILLISECONDS
        )
        if (
            not frames
            or frames[0].timestamp != first_expected
            or frames[-1].timestamp != last_expected
        ):
            raise StudyConfigError(
                f"window {window.key!r} timestamps do not match its boundary"
            )
        reference = store.persist(frames)
        prepared.append(
            PreparedWindow(
                window=window,
                reference=reference,
                archives=tuple(archives),
                first_timestamp=frames[0].timestamp,
                last_timestamp=frames[-1].timestamp,
                first_close=frames[0].close,
                last_close=frames[-1].close,
                minimum_low=min(frame.low for frame in frames),
                maximum_high=max(frame.high for frame in frames),
            )
        )
    return PreparedDataset(split=split, windows=tuple(prepared))
