from __future__ import annotations

from typing import Protocol

from .models import MarketBatch, MarketFrame


class MarketSource(Protocol):
    """Pull-based, resettable source of ordered market observations."""

    def reset(self, seed: int | None = None) -> MarketFrame:
        """Reset the source and return its first frame."""

    def next(self) -> MarketFrame:
        """Return the next frame, or raise StopIteration when exhausted."""

    def next_batch(self, count: int) -> MarketBatch:
        """Return up to ``count`` subsequent frames."""

    @property
    def done(self) -> bool:
        """Whether the last available frame has already been emitted."""
