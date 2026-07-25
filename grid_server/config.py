"""Compatibility import for shared environment configuration."""

from .shared.config import (
    api_base_url,
    binance_base_url,
    binance_coinm_base_url,
    binance_credentials,
    load_environment,
)

__all__ = [
    "api_base_url",
    "binance_base_url",
    "binance_coinm_base_url",
    "binance_credentials",
    "load_environment",
]
