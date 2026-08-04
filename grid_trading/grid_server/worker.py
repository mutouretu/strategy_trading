"""Compatibility entry point for the legacy single-strategy worker."""

from .runtime.worker import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
