"""Compatibility imports for fixed equal-USDT decisions.

The implementation now lives in trading_strategies.grid_core.
"""

from ._strategy_core import ensure_strategy_core

ensure_strategy_core()

from trading_strategies.grid_core.fixed_equal_usdt import *  # noqa: F401,F403,E402
