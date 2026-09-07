"""Small A-share quote adapter with a provider fallback."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import time
from typing import Iterable, Protocol
from zoneinfo import ZoneInfo

import requests


EASTMONEY_URL = "https://push2.eastmoney.com/api/qt/stock/get"
TENCENT_URL = "https://qt.gtimg.cn/q={market}{code}"
TIMEOUT_SECONDS = 10
MAX_ATTEMPTS = 2


class QuoteError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RealtimeQuote:
    symbol: str
    name: str
    price: Decimal
    price_time: datetime
    source_name: str


class QuoteProvider(Protocol):
    def fetch_many(
        self, symbols: Iterable[str]
    ) -> tuple[dict[str, RealtimeQuote], dict[str, str]]: ...


def _symbol_parts(symbol: str) -> tuple[str, str, str, int]:
    normalized = symbol.strip().upper().replace(".SS", ".SH")
    if "." not in normalized:
        raise QuoteError(f"股票代码缺少市场后缀：{symbol}")
    code, venue = normalized.rsplit(".", 1)
    if len(code) != 6 or not code.isdigit() or venue not in {"SH", "SZ", "BJ"}:
        raise QuoteError(f"股票代码格式不正确：{normalized}")
    return normalized, code, venue, 1 if venue == "SH" else 0


class PublicQuoteProvider:
    def __init__(self, timezone_name: str = "Asia/Shanghai") -> None:
        self.timezone = ZoneInfo(timezone_name)

    def _eastmoney(
        self, normalized: str, code: str, market_id: int
    ) -> RealtimeQuote:
        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = requests.get(
                    EASTMONEY_URL,
                    params={
                        "fltt": "2",
                        "invt": "2",
                        "fields": "f43,f57,f58,f59,f86",
                        "secid": f"{market_id}.{code}",
                        "_": str(int(time.time() * 1000)),
                    },
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                data = response.json().get("data")
                if not isinstance(data, dict) or str(data.get("f57") or "") != code:
                    raise QuoteError("东方财富行情代码校验失败。")
                price = Decimal(str(data["f43"]))
                if price <= 0:
                    raise QuoteError("东方财富暂无有效最新价。")
                try:
                    price_time = datetime.fromtimestamp(
                        int(data["f86"]), tz=self.timezone
                    )
                except (KeyError, TypeError, ValueError, OSError, OverflowError) as exc:
                    # Missing timestamps cannot be relabelled as today's quotes.
                    raise QuoteError("东方财富行情缺少有效时间。") from exc
                return RealtimeQuote(
                    normalized,
                    str(data.get("f58") or "").strip(),
                    price,
                    price_time,
                    "东方财富实时行情",
                )
            except (requests.RequestException, ValueError, KeyError) as exc:
                last_error = exc
                if attempt + 1 < MAX_ATTEMPTS:
                    time.sleep(0.35 * (attempt + 1))
        raise QuoteError("东方财富实时行情请求失败。") from last_error

    def _tencent(self, normalized: str, code: str, venue: str) -> RealtimeQuote:
        market = {"SH": "sh", "SZ": "sz", "BJ": "bj"}[venue]
        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = requests.get(
                    TENCENT_URL.format(market=market, code=code),
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                fields = response.content.decode("gbk").split('"', 2)[1].split("~")
                if fields[2] != code:
                    raise QuoteError("腾讯行情代码校验失败。")
                price = Decimal(fields[3])
                price_time = datetime.strptime(
                    fields[30], "%Y%m%d%H%M%S"
                ).replace(tzinfo=self.timezone)
                if price <= 0:
                    raise QuoteError("腾讯财经暂无有效最新价。")
                return RealtimeQuote(
                    normalized,
                    fields[1].strip(),
                    price,
                    price_time,
                    "腾讯财经实时行情",
                )
            except (requests.RequestException, ValueError, IndexError, UnicodeError) as exc:
                last_error = exc
                if attempt + 1 < MAX_ATTEMPTS:
                    time.sleep(0.35 * (attempt + 1))
        raise QuoteError("腾讯财经实时行情请求失败。") from last_error

    def fetch(self, symbol: str) -> RealtimeQuote:
        normalized, code, venue, market_id = _symbol_parts(symbol)
        try:
            return self._eastmoney(normalized, code, market_id)
        except QuoteError as first_error:
            try:
                return self._tencent(normalized, code, venue)
            except QuoteError as second_error:
                raise QuoteError(f"{normalized} 实时行情请求失败，请稍后重试。") from ExceptionGroup(
                    "所有实时行情源均不可用", [first_error, second_error]
                )

    def fetch_many(
        self, symbols: Iterable[str]
    ) -> tuple[dict[str, RealtimeQuote], dict[str, str]]:
        unique = list(dict.fromkeys(symbols))
        quotes: dict[str, RealtimeQuote] = {}
        failures: dict[str, str] = {}
        if not unique:
            return quotes, failures
        with ThreadPoolExecutor(max_workers=min(4, len(unique))) as executor:
            futures = {executor.submit(self.fetch, symbol): symbol for symbol in unique}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    quotes[symbol] = future.result()
                except Exception as exc:
                    failures[symbol] = str(exc)
        return quotes, failures
