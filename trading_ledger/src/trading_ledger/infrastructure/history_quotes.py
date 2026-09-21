"""Unadjusted daily closes. Index symbols are kept out of the trading ledger."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import tempfile
import time
from zoneinfo import ZoneInfo

import requests

from trading_ledger.config import MODULE_ROOT
from .quotes import QuoteError, _symbol_parts

BENCHMARKS = {"上证指数": "000001.SH", "中证1000": "000852.SH", "创业板指": "399006.SZ"}
HISTORY_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def parse_closes(payload, ticker: str, start: date, end: date) -> dict[str, Decimal]:
    try:
        if payload.get("code") != 0:
            raise ValueError("provider error")
        rows = payload["data"][ticker]["day"]  # Never use qfq/hfq prices for ledger valuation.
        result = {}
        for row in rows:
            day = date.fromisoformat(row[0])
            close = Decimal(str(row[2]))
            if not close.is_finite() or close <= 0 or row[0] in result:
                raise ValueError("invalid daily close")
            if start <= day <= end:
                result[row[0]] = close
        if not result:
            raise ValueError("no daily closes")
        return dict(sorted(result.items()))
    except (AttributeError, KeyError, IndexError, TypeError, ValueError, ArithmeticError) as error:
        raise QuoteError(f"{ticker} 历史日线无效或该区间没有行情") from error


class HistoricalQuoteProvider:
    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir if cache_dir is not None else MODULE_ROOT / "data" / "market_history"

    def fetch(self, symbol: str, start: date, end: date) -> dict[str, Decimal]:
        if start > end or (end - start).days > 365:
            raise QuoteError("历史行情一次查询应在一年以内")
        _, code, venue, _ = _symbol_parts(symbol)
        ticker = venue.lower() + code
        key = hashlib.sha256(f"{ticker}:{start}:{end}:unadjusted".encode()).hexdigest()
        cache = self.cache_dir / f"{key}.json"
        try:
            saved = json.loads(cache.read_text())
            # Current-day candles can still change; refresh these after five minutes.
            today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
            if end < today or time.time() - saved["fetched_at"] < 300:
                return parse_closes(saved["payload"], ticker, start, end)
        except (OSError, ValueError, KeyError, QuoteError):
            pass
        try:
            response = requests.get(HISTORY_URL, params={
                "param": f"{ticker},day,{start},{end},640,",
            }, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            response.raise_for_status()
            payload = response.json()
            result = parse_closes(payload, ticker, start, end)
        except (requests.RequestException, ValueError) as error:
            raise QuoteError(f"{symbol} 历史行情获取失败：{error}") from error
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", dir=self.cache_dir, delete=False) as handle:
                json.dump({"fetched_at": time.time(), "payload": payload}, handle)
                temp_path = Path(handle.name)
            temp_path.replace(cache)
        except OSError:
            pass  # A read-only cache must not prevent displaying fetched market data.
        return result

    def benchmarks(self, start: date, end: date):
        def load(item):
            name, symbol = item
            try:
                return name, self.fetch(symbol, start, end), None
            except QuoteError as error:
                return name, {}, str(error)
        with ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(load, BENCHMARKS.items()))
        return ({name: prices for name, prices, error in results if not error},
                [error for _, _, error in results if error])
