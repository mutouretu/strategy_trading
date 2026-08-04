"""Strategy-neutral market path profiling."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from market_protocol import MarketFrame

from .models import MARKET_PROFILE_SCHEMA_VERSION, AnchorTargetType, MarketScenario


HOUR_MILLISECONDS = 3_600_000
HOURS_PER_YEAR = 365 * 24


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


@dataclass(frozen=True, slots=True)
class MarketPathProfile:
    frame_count: int
    first_timestamp: int
    last_timestamp: int
    initial_price: Decimal
    final_price: Decimal
    total_return_rate: Decimal
    annualized_return_rate: Decimal
    minimum_low: Decimal
    minimum_low_timestamp: int
    maximum_high: Decimal
    maximum_high_timestamp: int
    max_drawdown_rate: Decimal
    max_drawdown_peak_timestamp: int
    max_drawdown_trough_timestamp: int
    annualized_realized_volatility: Decimal
    annualized_upside_volatility: Decimal
    annualized_downside_volatility: Decimal
    maximum_absolute_bar_return: Decimal
    drawdown_event_counts: tuple[tuple[str, int], ...]
    threshold_time_rates: tuple[tuple[str, Decimal], ...]
    longest_below_075_hours: int
    longest_above_200_hours: int
    maximum_hard_anchor_deviation: Decimal
    boundary_touch_count: int = 0
    schema_version: str = MARKET_PROFILE_SCHEMA_VERSION

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "frame_count": self.frame_count,
            "first_timestamp": self.first_timestamp,
            "last_timestamp": self.last_timestamp,
            "initial_price": str(self.initial_price),
            "final_price": str(self.final_price),
            "total_return_rate": str(self.total_return_rate),
            "annualized_return_rate": str(self.annualized_return_rate),
            "minimum_low": str(self.minimum_low),
            "minimum_low_timestamp": self.minimum_low_timestamp,
            "maximum_high": str(self.maximum_high),
            "maximum_high_timestamp": self.maximum_high_timestamp,
            "max_drawdown_rate": str(self.max_drawdown_rate),
            "max_drawdown_peak_timestamp": self.max_drawdown_peak_timestamp,
            "max_drawdown_trough_timestamp": self.max_drawdown_trough_timestamp,
            "annualized_realized_volatility": str(
                self.annualized_realized_volatility
            ),
            "annualized_upside_volatility": str(
                self.annualized_upside_volatility
            ),
            "annualized_downside_volatility": str(
                self.annualized_downside_volatility
            ),
            "maximum_absolute_bar_return": str(self.maximum_absolute_bar_return),
            "drawdown_event_counts": dict(self.drawdown_event_counts),
            "threshold_time_rates": {
                key: str(value) for key, value in self.threshold_time_rates
            },
            "longest_below_075_hours": self.longest_below_075_hours,
            "longest_above_200_hours": self.longest_above_200_hours,
            "maximum_hard_anchor_deviation": str(
                self.maximum_hard_anchor_deviation
            ),
            "boundary_touch_count": self.boundary_touch_count,
        }


def _longest(values: list[bool]) -> int:
    longest = 0
    current = 0
    for value in values:
        if value:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _event_count(drawdowns: list[float], threshold: float) -> int:
    count = 0
    active = False
    for drawdown in drawdowns:
        if drawdown >= threshold and not active:
            count += 1
            active = True
        elif drawdown < threshold:
            active = False
    return count


def profile_market_path(
    frames: tuple[MarketFrame, ...],
    scenario: MarketScenario,
) -> MarketPathProfile:
    if not frames:
        raise ValueError("cannot profile an empty market path")
    expected_count = (scenario.end - scenario.start).days * 24 + 1
    if len(frames) != expected_count:
        raise ValueError(
            f"market path expected {expected_count} frames, got {len(frames)}"
        )
    for index, frame in enumerate(frames):
        if frame.sequence != index or frame.instrument != scenario.instrument:
            raise ValueError("market path sequence or instrument is invalid")
        if index and frame.timestamp - frames[index - 1].timestamp != HOUR_MILLISECONDS:
            raise ValueError("market path timestamps must be contiguous 1h bars")
        if index and frame.open != frames[index - 1].close:
            raise ValueError("market path open must equal previous close")

    initial = frames[0].close
    final = frames[-1].close
    total_return = final / initial - Decimal("1")
    years = Decimal(str((frames[-1].timestamp - frames[0].timestamp) / 1000 / 86400 / 365))
    annualized_return = _decimal(
        math.pow(float(final / initial), 1 / float(years)) - 1
    )

    closes = [float(frame.close) for frame in frames]
    log_returns = [
        math.log(current / previous)
        for previous, current in zip(closes, closes[1:])
    ]
    annualizer = math.sqrt(HOURS_PER_YEAR)
    realized = (
        statistics.pstdev(log_returns) * annualizer if len(log_returns) > 1 else 0.0
    )
    upside = [value for value in log_returns if value > 0]
    downside = [value for value in log_returns if value < 0]
    upside_vol = statistics.pstdev(upside) * annualizer if len(upside) > 1 else 0.0
    downside_vol = (
        statistics.pstdev(downside) * annualizer if len(downside) > 1 else 0.0
    )

    peak = frames[0].close
    peak_timestamp = frames[0].timestamp
    max_drawdown = Decimal("0")
    max_peak_timestamp = peak_timestamp
    max_trough_timestamp = peak_timestamp
    drawdowns: list[float] = []
    for frame in frames:
        if frame.close > peak:
            peak = frame.close
            peak_timestamp = frame.timestamp
        drawdown = Decimal("1") - frame.close / peak
        drawdowns.append(float(drawdown))
        if drawdown > max_drawdown:
            max_drawdown = drawdown
            max_peak_timestamp = peak_timestamp
            max_trough_timestamp = frame.timestamp

    minimum = min(frames, key=lambda frame: (frame.low, frame.timestamp))
    maximum = max(frames, key=lambda frame: (frame.high, -frame.timestamp))
    maximum_bar_return = max(
        abs(frame.close / frame.open - Decimal("1")) for frame in frames
    )
    threshold_rates = (
        (
            "below_0.75_p0",
            Decimal(sum(frame.close < initial * Decimal("0.75") for frame in frames))
            / Decimal(len(frames)),
        ),
        (
            "below_0.50_p0",
            Decimal(sum(frame.close < initial * Decimal("0.50") for frame in frames))
            / Decimal(len(frames)),
        ),
        (
            "above_1.50_p0",
            Decimal(sum(frame.close > initial * Decimal("1.50") for frame in frames))
            / Decimal(len(frames)),
        ),
        (
            "above_2.00_p0",
            Decimal(sum(frame.close > initial * Decimal("2.00") for frame in frames))
            / Decimal(len(frames)),
        ),
    )

    by_timestamp = {frame.timestamp: frame.close for frame in frames}
    hard_deviations: list[Decimal] = []
    for anchor in scenario.anchors:
        if anchor.target.type is not AnchorTargetType.HARD:
            continue
        assert anchor.target.price is not None
        timestamp = int(
            datetime(
                anchor.date.year,
                anchor.date.month,
                anchor.date.day,
                tzinfo=timezone.utc,
            ).timestamp()
            * 1_000
        )
        if timestamp not in by_timestamp:
            raise ValueError("hard anchor timestamp is absent from market path")
        hard_deviations.append(abs(by_timestamp[timestamp] - anchor.target.price))

    return MarketPathProfile(
        frame_count=len(frames),
        first_timestamp=frames[0].timestamp,
        last_timestamp=frames[-1].timestamp,
        initial_price=initial,
        final_price=final,
        total_return_rate=total_return,
        annualized_return_rate=annualized_return,
        minimum_low=minimum.low,
        minimum_low_timestamp=minimum.timestamp,
        maximum_high=maximum.high,
        maximum_high_timestamp=maximum.timestamp,
        max_drawdown_rate=max_drawdown,
        max_drawdown_peak_timestamp=max_peak_timestamp,
        max_drawdown_trough_timestamp=max_trough_timestamp,
        annualized_realized_volatility=_decimal(realized),
        annualized_upside_volatility=_decimal(upside_vol),
        annualized_downside_volatility=_decimal(downside_vol),
        maximum_absolute_bar_return=maximum_bar_return,
        drawdown_event_counts=(
            ("20pct", _event_count(drawdowns, 0.20)),
            ("30pct", _event_count(drawdowns, 0.30)),
            ("50pct", _event_count(drawdowns, 0.50)),
        ),
        threshold_time_rates=threshold_rates,
        longest_below_075_hours=_longest(
            [frame.close < initial * Decimal("0.75") for frame in frames]
        ),
        longest_above_200_hours=_longest(
            [frame.close > initial * Decimal("2.00") for frame in frames]
        ),
        maximum_hard_anchor_deviation=max(
            hard_deviations, default=Decimal("0")
        ),
    )


def aggregate_market_profiles(
    profiles: tuple[MarketPathProfile, ...],
) -> dict[str, object]:
    if not profiles:
        raise ValueError("cannot aggregate an empty profile collection")

    def distribution(name: str) -> dict[str, str]:
        values = sorted(Decimal(getattr(profile, name)) for profile in profiles)
        return {
            "minimum": str(values[0]),
            "median": str(statistics.median(values)),
            "maximum": str(values[-1]),
        }

    return {
        "path_count": len(profiles),
        "final_price": distribution("final_price"),
        "total_return_rate": distribution("total_return_rate"),
        "max_drawdown_rate": distribution("max_drawdown_rate"),
        "annualized_realized_volatility": distribution(
            "annualized_realized_volatility"
        ),
        "minimum_low": distribution("minimum_low"),
        "maximum_high": distribution("maximum_high"),
        "maximum_hard_anchor_deviation": distribution(
            "maximum_hard_anchor_deviation"
        ),
    }
