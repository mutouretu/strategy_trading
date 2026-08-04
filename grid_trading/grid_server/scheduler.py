"""Compatibility entry point for the shared strategy scheduler."""

from .runtime.scheduler import ACTIVE_STATUSES, StrategyScheduler, main, run_scheduler_forever

__all__ = ["ACTIVE_STATUSES", "StrategyScheduler", "main", "run_scheduler_forever"]

if __name__ == "__main__":
    raise SystemExit(main())
