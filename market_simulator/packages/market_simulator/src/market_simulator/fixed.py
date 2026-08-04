from __future__ import annotations

from decimal import Decimal
from typing import Iterable, TypeAlias

from market_protocol import MarketBatch, MarketFrame


PriceInput: TypeAlias = Decimal | str | int | float
BarInput: TypeAlias = tuple[PriceInput, PriceInput, PriceInput, PriceInput]


def _decimal(value: PriceInput) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


class _FixedFramesMarketSource:
    def __init__(self, frames: tuple[MarketFrame, ...]) -> None:
        if not frames:
            raise ValueError("frames must contain at least one value")
        self._frames = frames
        self._index = -1

    def reset(self, seed: int | None = None) -> MarketFrame:
        # Fixed sources are deterministic; seed is accepted for protocol parity.
        del seed
        self._index = 0
        return self._frames[0]

    def next(self) -> MarketFrame:
        if self._index < 0:
            raise RuntimeError("source must be reset before next()")
        if self.done:
            raise StopIteration
        self._index += 1
        return self._frames[self._index]

    def next_batch(self, count: int) -> MarketBatch:
        if count < 0:
            raise ValueError("count must be >= 0")
        frames: list[MarketFrame] = []
        while len(frames) < count and not self.done:
            frames.append(self.next())
        return tuple(frames)

    @property
    def done(self) -> bool:
        return self._index >= 0 and self._index >= len(self._frames) - 1


class FixedSequenceMarketSource(_FixedFramesMarketSource):
    """Deterministic point-price source represented as flat OHLC bars."""

    def __init__(
        self,
        instrument: str,
        prices: Iterable[PriceInput],
        *,
        start_timestamp: int = 0,
        step_milliseconds: int = 1_000,
    ) -> None:
        if not instrument.strip():
            raise ValueError("instrument must not be empty")
        if step_milliseconds <= 0:
            raise ValueError("step_milliseconds must be > 0")
        values = tuple(_decimal(price) for price in prices)
        frames = tuple(
            MarketFrame(
                sequence=index,
                timestamp=start_timestamp + index * step_milliseconds,
                instrument=instrument,
                open=price,
                high=price,
                low=price,
                close=price,
            )
            for index, price in enumerate(values)
        )
        super().__init__(frames)


class FixedBarMarketSource(_FixedFramesMarketSource):
    """Deterministic source backed by explicit ``(open, high, low, close)`` bars."""

    def __init__(
        self,
        instrument: str,
        bars: Iterable[BarInput],
        *,
        start_timestamp: int = 0,
        step_milliseconds: int = 86_400_000,
    ) -> None:
        if not instrument.strip():
            raise ValueError("instrument must not be empty")
        if step_milliseconds <= 0:
            raise ValueError("step_milliseconds must be > 0")
        values = tuple(bars)
        frames = tuple(
            MarketFrame(
                sequence=index,
                timestamp=start_timestamp + index * step_milliseconds,
                instrument=instrument,
                open=_decimal(bar[0]),
                high=_decimal(bar[1]),
                low=_decimal(bar[2]),
                close=_decimal(bar[3]),
            )
            for index, bar in enumerate(values)
        )
        super().__init__(frames)
