"""Long-horizon anchored random bridge with per-segment volatility."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from market_protocol import MarketBatch, MarketFrame

from .models import (
    AnchorTargetType,
    AssetProfile,
    MarketScenario,
    ScenarioAnchor,
    VolatilityRegime,
)


ANCHORED_REGIME_BRIDGE_V1 = "anchored-regime-bridge/v1"
HOUR_MILLISECONDS = 3_600_000


@dataclass(frozen=True, slots=True)
class ResolvedAnchor:
    date: str
    price: Decimal
    target_type: AnchorTargetType

    def to_document(self) -> dict[str, str]:
        return {
            "date": self.date,
            "price": str(self.price),
            "target_type": self.target_type.value,
        }


@dataclass(frozen=True, slots=True)
class ResolvedVolatilityRegime:
    start: str
    end_exclusive: str
    annual_volatility: Decimal

    def to_document(self) -> dict[str, str]:
        return {
            "start": self.start,
            "end_exclusive": self.end_exclusive,
            "annual_volatility": str(self.annual_volatility),
        }


@dataclass(frozen=True, slots=True)
class GeneratedRegimeBridgePath:
    scenario_id: str
    seed: int
    frames: tuple[MarketFrame, ...]
    resolved_anchors: tuple[ResolvedAnchor, ...]
    resolved_volatility_regimes: tuple[ResolvedVolatilityRegime, ...]


class AnchoredRegimeBridgeModel:
    model_type = ANCHORED_REGIME_BRIDGE_V1

    def generate(
        self,
        scenario: MarketScenario,
        asset_profile: AssetProfile,
        *,
        seed: int,
    ) -> GeneratedRegimeBridgePath:
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("seed must be an integer >= 0")
        if scenario.model.type != self.model_type:
            raise ValueError("scenario market model does not match generator")
        if scenario.asset_profile_id != asset_profile.profile_id:
            raise ValueError("scenario asset profile identity does not match")
        if scenario.model.periods_per_year != asset_profile.periods_per_year:
            raise ValueError("scenario and asset periods_per_year do not match")
        if scenario.model.price_quantum != asset_profile.price_quantum:
            raise ValueError("scenario and asset price quantum do not match")

        generator = random.Random(seed)
        anchors = tuple(
            self._resolve_anchor(anchor, generator, scenario.model.price_quantum)
            for anchor in scenario.anchors
        )
        regimes = tuple(
            self._resolve_regime(regime, generator)
            for regime in scenario.volatility_regimes
        )
        frames = self._frames(
            scenario,
            anchors,
            regimes,
            generator,
        )
        return GeneratedRegimeBridgePath(
            scenario_id=scenario.scenario_id,
            seed=seed,
            frames=frames,
            resolved_anchors=anchors,
            resolved_volatility_regimes=regimes,
        )

    @staticmethod
    def _resolve_anchor(
        anchor: ScenarioAnchor,
        generator: random.Random,
        quantum: Decimal,
    ) -> ResolvedAnchor:
        if anchor.target.type is AnchorTargetType.HARD:
            assert anchor.target.price is not None
            price = anchor.target.price
        else:
            assert anchor.target.minimum is not None
            assert anchor.target.maximum is not None
            if anchor.target.minimum == anchor.target.maximum:
                price = anchor.target.minimum
            else:
                sampled = math.exp(
                    generator.uniform(
                        math.log(float(anchor.target.minimum)),
                        math.log(float(anchor.target.maximum)),
                    )
                )
                price = Decimal(str(sampled))
        return ResolvedAnchor(
            date=anchor.date.isoformat(),
            price=price.quantize(quantum, rounding=ROUND_HALF_UP),
            target_type=anchor.target.type,
        )

    @staticmethod
    def _resolve_regime(
        regime: VolatilityRegime,
        generator: random.Random,
    ) -> ResolvedVolatilityRegime:
        if regime.minimum == regime.maximum:
            volatility = regime.minimum
        else:
            volatility = Decimal(
                str(generator.uniform(float(regime.minimum), float(regime.maximum)))
            ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        return ResolvedVolatilityRegime(
            start=regime.start.isoformat(),
            end_exclusive=regime.end_exclusive.isoformat(),
            annual_volatility=volatility,
        )

    def _frames(
        self,
        scenario: MarketScenario,
        anchors: tuple[ResolvedAnchor, ...],
        regimes: tuple[ResolvedVolatilityRegime, ...],
        generator: random.Random,
    ) -> tuple[MarketFrame, ...]:
        quantum = scenario.model.price_quantum
        first_timestamp = self._timestamp(scenario.anchors[0].date)
        first_price = anchors[0].price
        frames = [
            MarketFrame(
                sequence=0,
                timestamp=first_timestamp,
                instrument=scenario.instrument,
                open=first_price,
                high=first_price,
                low=first_price,
                close=first_price,
            )
        ]
        for segment_index, (start_anchor, end_anchor, regime) in enumerate(
            zip(anchors, anchors[1:], regimes)
        ):
            start_date = scenario.anchors[segment_index].date
            end_date = scenario.anchors[segment_index + 1].date
            hours = (end_date - start_date).days * 24
            sigma = float(regime.annual_volatility)
            closes = self._log_bridge(
                float(start_anchor.price),
                float(end_anchor.price),
                steps=hours,
                sigma=sigma,
                dt=1 / (scenario.model.periods_per_year * 24),
                generator=generator,
            )
            closes[0] = float(start_anchor.price)
            closes[-1] = float(end_anchor.price)
            segment_start_timestamp = self._timestamp(start_date)
            previous_close = frames[-1].close
            for hour in range(1, hours + 1):
                close = (
                    end_anchor.price
                    if hour == hours
                    else self._quantize(closes[hour], quantum)
                )
                intrabar = self._log_bridge(
                    float(previous_close),
                    float(close),
                    steps=scenario.model.substeps_per_bar,
                    sigma=sigma,
                    dt=(
                        1
                        / (
                            scenario.model.periods_per_year
                            * 24
                            * scenario.model.substeps_per_bar
                        )
                    ),
                    generator=generator,
                )
                sampled = [self._quantize(value, quantum) for value in intrabar]
                frames.append(
                    MarketFrame(
                        sequence=len(frames),
                        timestamp=segment_start_timestamp + hour * HOUR_MILLISECONDS,
                        instrument=scenario.instrument,
                        open=previous_close,
                        high=max(previous_close, close, *sampled),
                        low=min(previous_close, close, *sampled),
                        close=close,
                    )
                )
                previous_close = close
        expected = (scenario.end - scenario.start).days * 24 + 1
        if len(frames) != expected:
            raise RuntimeError(
                f"generated frame count mismatch: expected {expected}, got {len(frames)}"
            )
        return tuple(frames)

    @staticmethod
    def _log_bridge(
        start_price: float,
        end_price: float,
        *,
        steps: int,
        sigma: float,
        dt: float,
        generator: random.Random,
    ) -> list[float]:
        if steps <= 0:
            raise ValueError("bridge steps must be > 0")
        walk = [0.0]
        for _ in range(steps):
            walk.append(walk[-1] + generator.gauss(0.0, 1.0))
        terminal = walk[-1]
        start_log = math.log(start_price)
        end_log = math.log(end_price)
        values = []
        for index in range(steps + 1):
            fraction = index / steps
            centered = walk[index] - fraction * terminal
            values.append(
                math.exp(
                    start_log
                    + fraction * (end_log - start_log)
                    + sigma * math.sqrt(dt) * centered
                )
            )
        values[0] = start_price
        values[-1] = end_price
        return values

    @staticmethod
    def _quantize(value: float, quantum: Decimal) -> Decimal:
        result = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
        if result <= 0:
            raise ValueError("generated price must be > 0")
        return result

    @staticmethod
    def _timestamp(value) -> int:
        return int(
            datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp()
            * 1_000
        )


class RegimeBridgeMarketSource:
    """MarketSource wrapper that reveals only sequential generated frames."""

    def __init__(
        self,
        scenario: MarketScenario,
        asset_profile: AssetProfile,
        *,
        model: AnchoredRegimeBridgeModel | None = None,
    ) -> None:
        self.scenario = scenario
        self.asset_profile = asset_profile
        self.model = model or AnchoredRegimeBridgeModel()
        self.generated: GeneratedRegimeBridgePath | None = None
        self._index = -1

    def reset(self, seed: int | None = None) -> MarketFrame:
        if seed is None:
            raise ValueError("regime bridge source requires an explicit seed")
        self.generated = self.model.generate(
            self.scenario,
            self.asset_profile,
            seed=seed,
        )
        self._index = 0
        return self.generated.frames[0]

    def next(self) -> MarketFrame:
        if self.generated is None or self._index < 0:
            raise RuntimeError("source must be reset before next()")
        if self.done:
            raise StopIteration
        self._index += 1
        return self.generated.frames[self._index]

    def next_batch(self, count: int) -> MarketBatch:
        if count < 0:
            raise ValueError("count must be >= 0")
        frames: list[MarketFrame] = []
        while len(frames) < count and not self.done:
            frames.append(self.next())
        return tuple(frames)

    @property
    def done(self) -> bool:
        return (
            self.generated is not None
            and self._index >= 0
            and self._index >= len(self.generated.frames) - 1
        )


def build_market_model_registry() -> "MarketModelRegistry":
    from .registry import MarketModelRegistry

    registry = MarketModelRegistry()
    registry.register(AnchoredRegimeBridgeModel())
    return registry
