"""Compatibility import for the Binance infrastructure adapter."""

from .infrastructure.binance import (
    BinanceAPIError,
    BinanceCoinMExchange,
    BinanceFuturesExchange,
    decimal_text,
)

__all__ = [
    "BinanceAPIError",
    "BinanceCoinMExchange",
    "BinanceFuturesExchange",
    "decimal_text",
]
