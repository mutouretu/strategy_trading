from __future__ import annotations

from urllib.parse import urlparse

from ..domain import FixedEntryMode
from ..infrastructure.binance import BinanceFuturesExchange
from ..infrastructure.okx import OkxSwapExchange


def is_live_exchange(exchange: object) -> bool:
    """Identify production adapters, including the scheduler snapshot wrapper."""
    raw = getattr(exchange, "exchange", exchange)
    if isinstance(raw, OkxSwapExchange):
        return not raw.simulated and urlparse(raw.base_url).hostname in {
            "openapi.okx.com", "us.okx.com", "eea.okx.com",
        }
    if isinstance(raw, BinanceFuturesExchange):
        return urlparse(raw.base_url).hostname in {"fapi.binance.com", "dapi.binance.com"}
    return False


def require_live_triggered_without_prebuy(
    exchange: object, entry_mode: FixedEntryMode, upper_seed_count: int,
) -> None:
    if not is_live_exchange(exchange):
        return
    if entry_mode != FixedEntryMode.PRICE_TRIGGERED:
        raise ValueError("实盘固定网格必须使用回踩触发模式，禁止初始预建仓")
    if upper_seed_count:
        raise ValueError("实盘固定网格禁止预买上方未触发单元格；预买格数必须为 0")
