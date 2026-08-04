from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from market_protocol import MarketBatch, MarketFrame

from .fixed import PriceInput, _decimal


@dataclass(frozen=True, slots=True)
class PriceAnchor:
    date: date
    price: Decimal

    @classmethod
    def parse(
        cls,
        value: tuple[date | str, PriceInput],
    ) -> PriceAnchor:
        anchor_date, price = value
        parsed_date = (
            date.fromisoformat(anchor_date)
            if isinstance(anchor_date, str)
            else anchor_date
        )
        return cls(parsed_date, _decimal(price))


class AnchoredGBMMarketSource:
    """Generate reproducible daily OHLC bars through hard price anchors.

    Daily closes follow a Brownian bridge in log-price space. Each day's high
    and low come from a second intraday bridge conditioned on that day's open
    and close. Anchor closes remain exact. Optional floor and ceiling bounds
    reflect generated prices back into the configured interval.
    """

    def __init__(
        self,
        instrument: str,
        anchors: Iterable[PriceAnchor | tuple[date | str, PriceInput]],
        *,
        annual_volatility: PriceInput,
        intraday_steps: int = 24,
        periods_per_year: int = 365,
        price_quantum: PriceInput = Decimal("0.01"),
        price_floor: PriceInput | None = None,
        price_ceiling: PriceInput | None = None,
    ) -> None:
        if not instrument.strip():
            raise ValueError("instrument must not be empty")
        parsed = tuple(
            anchor
            if isinstance(anchor, PriceAnchor)
            else PriceAnchor.parse(anchor)
            for anchor in anchors
        )
        if len(parsed) < 2:
            raise ValueError("at least two anchors are required")
        if any(anchor.price <= 0 for anchor in parsed):
            raise ValueError("anchor prices must be > 0")
        if any(
            current.date >= following.date
            for current, following in zip(parsed, parsed[1:])
        ):
            raise ValueError("anchor dates must be strictly increasing")
        volatility = _decimal(annual_volatility)
        if volatility < 0:
            raise ValueError("annual_volatility must be >= 0")
        if intraday_steps < 2:
            raise ValueError("intraday_steps must be >= 2")
        if periods_per_year <= 0:
            raise ValueError("periods_per_year must be > 0")
        quantum = _decimal(price_quantum)
        if quantum <= 0:
            raise ValueError("price_quantum must be > 0")
        floor = _decimal(price_floor) if price_floor is not None else None
        ceiling = _decimal(price_ceiling) if price_ceiling is not None else None
        if floor is not None and floor <= 0:
            raise ValueError("price_floor must be > 0")
        if ceiling is not None and ceiling <= 0:
            raise ValueError("price_ceiling must be > 0")
        if floor is not None and ceiling is not None and floor >= ceiling:
            raise ValueError("price_floor must be < price_ceiling")
        if floor is not None and any(anchor.price < floor for anchor in parsed):
            raise ValueError("anchor prices must be >= price_floor")
        if ceiling is not None and any(
            anchor.price > ceiling for anchor in parsed
        ):
            raise ValueError("anchor prices must be <= price_ceiling")

        self.instrument = instrument
        self.anchors = parsed
        self.annual_volatility = volatility
        self.intraday_steps = intraday_steps
        self.periods_per_year = periods_per_year
        self.price_quantum = quantum
        self.price_floor = floor
        self.price_ceiling = ceiling
        self._frames: tuple[MarketFrame, ...] = ()
        self._index = -1

    def reset(self, seed: int | None = None) -> MarketFrame:
        self._frames = self._generate(seed)
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

    def _generate(self, seed: int | None) -> tuple[MarketFrame, ...]:
        generator = random.Random(seed)
        sigma = float(self.annual_volatility)
        closes = self._daily_closes(generator)

        frames: list[MarketFrame] = []
        exact_anchors = {anchor.date: anchor.price for anchor in self.anchors}
        for sequence, (bar_date, raw_close) in enumerate(closes):
            close = exact_anchors.get(
                bar_date,
                self._bounded_quantize(raw_close),
            )
            open_price = close if sequence == 0 else frames[-1].close
            intraday = self._bridge(
                float(open_price),
                float(close),
                self.intraday_steps,
                sigma,
                1 / (self.periods_per_year * self.intraday_steps),
                generator,
            )
            high = max(
                open_price,
                close,
                *(self._bounded_quantize(value) for value in intraday),
            )
            low = min(
                open_price,
                close,
                *(self._bounded_quantize(value) for value in intraday),
            )
            timestamp = int(
                datetime(
                    bar_date.year,
                    bar_date.month,
                    bar_date.day,
                    tzinfo=timezone.utc,
                ).timestamp()
                * 1_000
            )
            frames.append(
                MarketFrame(
                    sequence=sequence,
                    timestamp=timestamp,
                    instrument=self.instrument,
                    open=open_price,
                    high=high,
                    low=low,
                    close=close,
                )
            )
        return tuple(frames)

    def _daily_closes(
        self, generator: random.Random
    ) -> list[tuple[date, float]]:
        sigma = float(self.annual_volatility)
        closes: list[tuple[date, float]] = []
        for segment_index, (start, end) in enumerate(
            zip(self.anchors, self.anchors[1:])
        ):
            steps = (end.date - start.date).days
            segment = self._bridge(
                float(start.price),
                float(end.price),
                steps,
                sigma,
                1 / self.periods_per_year,
                generator,
            )
            for offset, close in enumerate(segment):
                if segment_index > 0 and offset == 0:
                    continue
                closes.append((start.date + timedelta(days=offset), close))
        return closes

    def _quantize(self, value: float) -> Decimal:
        return Decimal(str(value)).quantize(
            self.price_quantum,
            rounding=ROUND_HALF_UP,
        )

    def _bounded_quantize(self, value: float) -> Decimal:
        bounded = self._reflect_into_bounds(value)
        quantized = self._quantize(bounded)
        if self.price_floor is not None:
            quantized = max(quantized, self.price_floor)
        if self.price_ceiling is not None:
            quantized = min(quantized, self.price_ceiling)
        return quantized

    def _reflect_into_bounds(self, value: float) -> float:
        floor = (
            float(self.price_floor)
            if self.price_floor is not None
            else None
        )
        ceiling = (
            float(self.price_ceiling)
            if self.price_ceiling is not None
            else None
        )
        if floor is None and ceiling is None:
            return value
        if floor is None:
            assert ceiling is not None
            return min(value, ceiling)
        if ceiling is None:
            return max(value, floor)

        span = ceiling - floor
        offset = (value - floor) % (2 * span)
        if offset > span:
            offset = 2 * span - offset
        return floor + offset

    @staticmethod
    def _bridge(
        start_price: float,
        end_price: float,
        steps: int,
        sigma: float,
        dt: float,
        generator: random.Random,
    ) -> tuple[float, ...]:
        walk = [0.0]
        for _ in range(steps):
            walk.append(walk[-1] + generator.gauss(0.0, 1.0))
        terminal = walk[-1]
        start_log = math.log(start_price)
        end_log = math.log(end_price)
        values = []
        for index in range(steps + 1):
            fraction = index / steps
            bridge = walk[index] - fraction * terminal
            values.append(
                math.exp(
                    start_log
                    + fraction * (end_log - start_log)
                    + sigma * math.sqrt(dt) * bridge
                )
            )
        values[0] = start_price
        values[-1] = end_price
        return tuple(values)


class AnchoredGBMIntradayMarketSource(AnchoredGBMMarketSource):
    """Emit an executable intraday path while preserving daily anchors.

    Unlike the daily source, each sampled point is its own market frame. A
    strategy can therefore complete several grid legs during one calendar
    day without relying on unknowable ordering inside a daily high/low range.
    """

    def __init__(
        self,
        instrument: str,
        anchors: Iterable[PriceAnchor | tuple[date | str, PriceInput]],
        *,
        annual_volatility: PriceInput,
        bars_per_day: int = 288,
        periods_per_year: int = 365,
        price_quantum: PriceInput = Decimal("0.01"),
        price_floor: PriceInput | None = None,
        price_ceiling: PriceInput | None = None,
    ) -> None:
        if (
            isinstance(bars_per_day, bool)
            or not isinstance(bars_per_day, int)
            or bars_per_day < 2
        ):
            raise ValueError("bars_per_day must be an integer >= 2")
        if 86_400 % bars_per_day != 0:
            raise ValueError("bars_per_day must divide 86400 exactly")
        super().__init__(
            instrument,
            anchors,
            annual_volatility=annual_volatility,
            intraday_steps=bars_per_day,
            periods_per_year=periods_per_year,
            price_quantum=price_quantum,
            price_floor=price_floor,
            price_ceiling=price_ceiling,
        )
        self.bars_per_day = bars_per_day

    def _generate(self, seed: int | None) -> tuple[MarketFrame, ...]:
        generator = random.Random(seed)
        sigma = float(self.annual_volatility)
        daily_closes = self._daily_closes(generator)
        interval_ms = 86_400_000 // self.bars_per_day
        frames: list[MarketFrame] = []

        first_date, first_raw = daily_closes[0]
        first_price = self._anchor_or_quantized(first_date, first_raw)
        first_timestamp = self._timestamp(first_date)
        frames.append(
            MarketFrame(
                sequence=0,
                timestamp=first_timestamp,
                instrument=self.instrument,
                open=first_price,
                high=first_price,
                low=first_price,
                close=first_price,
            )
        )

        for (start_date, start_raw), (end_date, end_raw) in zip(
            daily_closes, daily_closes[1:]
        ):
            start_price = self._anchor_or_quantized(start_date, start_raw)
            end_price = self._anchor_or_quantized(end_date, end_raw)
            path = self._bridge(
                float(start_price),
                float(end_price),
                self.bars_per_day,
                sigma,
                1 / (self.periods_per_year * self.bars_per_day),
                generator,
            )
            points = [start_price]
            points.extend(
                self._bounded_quantize(value) for value in path[1:-1]
            )
            points.append(end_price)
            day_start = self._timestamp(start_date)
            for step in range(1, self.bars_per_day + 1):
                open_price = points[step - 1]
                close_price = points[step]
                frames.append(
                    MarketFrame(
                        sequence=len(frames),
                        timestamp=day_start + step * interval_ms,
                        instrument=self.instrument,
                        open=open_price,
                        high=max(open_price, close_price),
                        low=min(open_price, close_price),
                        close=close_price,
                    )
                )
        return tuple(frames)

    def _anchor_or_quantized(self, day: date, raw: float) -> Decimal:
        exact = next(
            (anchor.price for anchor in self.anchors if anchor.date == day),
            None,
        )
        return exact if exact is not None else self._bounded_quantize(raw)

    @staticmethod
    def _timestamp(day: date) -> int:
        return int(
            datetime(
                day.year,
                day.month,
                day.day,
                tzinfo=timezone.utc,
            ).timestamp()
            * 1_000
        )
