"""Compatibility import for the application trading engine."""

from .application.engine import TradingEngine, run_loop

__all__ = ["TradingEngine", "run_loop"]
