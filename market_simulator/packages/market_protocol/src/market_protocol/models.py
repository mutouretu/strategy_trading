from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping, TypeAlias


@dataclass(frozen=True, slots=True)
class MarketFrame:
    """One ordered OHLC bar emitted by a market source.

    ``timestamp`` is an integer chosen by the source (milliseconds since epoch
    is recommended). Exact-mode prices use Decimal so decision boundaries do
    not diverge through binary float conversion.
    """

    sequence: int
    timestamp: int
    instrument: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    features: Mapping[str, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("sequence must be >= 0")
        if not self.instrument.strip():
            raise ValueError("instrument must not be empty")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("OHLC prices must be > 0")
        if self.low > self.high:
            raise ValueError("low must be <= high")
        if not self.low <= self.open <= self.high:
            raise ValueError("open must be between low and high")
        if not self.low <= self.close <= self.high:
            raise ValueError("close must be between low and high")
        object.__setattr__(self, "features", MappingProxyType(dict(self.features)))

    @property
    def price(self) -> Decimal:
        """Compatibility alias for strategies that consume only bar close."""

        return self.close


MarketBatch: TypeAlias = tuple[MarketFrame, ...]
