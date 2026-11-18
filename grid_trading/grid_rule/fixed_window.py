"""Compatibility imports for fixed grid order-window decisions."""

from ._strategy_core import ensure_strategy_core

ensure_strategy_core()

from trading_strategies.grid_core.fixed_window import *  # noqa: F401,F403,E402
