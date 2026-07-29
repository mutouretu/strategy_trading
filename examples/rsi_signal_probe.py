"""Minimal RSI-style rule and simulation adapter for active-intent timing."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Sequence

from examples.intent_adapter_support import (
    ActiveTradeIntent,
    ExampleTradeIntentBook,
)
from market_protocol import MarketFrame
from market_simulator import FixedBarMarketSource
from simulation_runtime import (
    IntentSnapshot,
    OrderSide,
    SimFill,
    SimulationResult,
    SimulationRunner,
    TradeInstruction,
)


INSTRUMENT = "BTCUSD"
INITIAL_EQUITY = Decimal("1000")
RSI_BARS = (
    ("100", "101", "99", "100"),
    ("100", "101", "89", "90"),
    ("90", "91", "79", "80"),
    ("81", "121", "80", "120"),
    ("119", "120", "110", "115"),
)


class RsiPositionState(StrEnum):
    FLAT = "FLAT"
    ENTRY_PENDING = "ENTRY_PENDING"
    LONG = "LONG"
    EXIT_PENDING = "EXIT_PENDING"


class RsiSignalSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class RsiSignal:
    intent_key: str
    side: RsiSignalSide
    quantity: Decimal
    reduce_only: bool
    rsi: Decimal
    signal_sequence: int


@dataclass(frozen=True, slots=True)
class RsiSignalFill:
    intent_key: str
    side: RsiSignalSide


class RsiSignalRule:
    """A small close-based RSI rule with no MarketFrame or ledger access."""

    def __init__(
        self,
        *,
        period: int = 2,
        oversold: Decimal = Decimal("30"),
        overbought: Decimal = Decimal("70"),
        quantity: Decimal = Decimal("1"),
    ) -> None:
        self.period = period
        self.oversold = Decimal(oversold)
        self.overbought = Decimal(overbought)
        self.quantity = Decimal(quantity)
        if self.period < 1:
            raise ValueError("period must be >= 1")
        if not Decimal("0") <= self.oversold < self.overbought:
            raise ValueError("RSI thresholds must satisfy 0 <= low < high")
        if self.overbought > 100:
            raise ValueError("overbought must be <= 100")
        if self.quantity <= 0:
            raise ValueError("quantity must be > 0")
        self._closes: list[Decimal] = []
        self._state = RsiPositionState.FLAT
        self._pending_key: str | None = None

    @property
    def state(self) -> RsiPositionState:
        return self._state

    def initialize(self, close: Decimal) -> None:
        if self._closes:
            raise RuntimeError("RSI rule is already initialized")
        self._closes.append(self._validated_close(close))

    def on_market(
        self,
        close: Decimal,
        *,
        sequence: int,
    ) -> tuple[RsiSignal, ...]:
        if not self._closes:
            raise RuntimeError("RSI rule must be initialized first")
        self._closes.append(self._validated_close(close))
        rsi = self._rsi()
        if rsi is None:
            return ()

        if (
            self._state == RsiPositionState.FLAT
            and rsi <= self.oversold
        ):
            return (self._signal(RsiSignalSide.BUY, rsi, sequence),)
        if (
            self._state == RsiPositionState.LONG
            and rsi >= self.overbought
        ):
            return (self._signal(RsiSignalSide.SELL, rsi, sequence),)
        return ()

    def on_fills(self, fills: Sequence[RsiSignalFill]) -> None:
        for fill in fills:
            if fill.intent_key != self._pending_key:
                raise ValueError(
                    "unexpected RSI fill intent key: "
                    f"{fill.intent_key}"
                )
            if self._state == RsiPositionState.ENTRY_PENDING:
                if fill.side != RsiSignalSide.BUY:
                    raise ValueError("RSI entry fill must be BUY")
                self._state = RsiPositionState.LONG
            elif self._state == RsiPositionState.EXIT_PENDING:
                if fill.side != RsiSignalSide.SELL:
                    raise ValueError("RSI exit fill must be SELL")
                self._state = RsiPositionState.FLAT
            else:
                raise ValueError(
                    f"RSI rule has no pending fill in state {self._state}"
                )
            self._pending_key = None

    def _signal(
        self,
        side: RsiSignalSide,
        rsi: Decimal,
        sequence: int,
    ) -> RsiSignal:
        role = "entry" if side == RsiSignalSide.BUY else "exit"
        key = f"rsi:{role}:{sequence}"
        self._pending_key = key
        self._state = (
            RsiPositionState.ENTRY_PENDING
            if side == RsiSignalSide.BUY
            else RsiPositionState.EXIT_PENDING
        )
        return RsiSignal(
            intent_key=key,
            side=side,
            quantity=self.quantity,
            reduce_only=side == RsiSignalSide.SELL,
            rsi=rsi,
            signal_sequence=sequence,
        )

    def _rsi(self) -> Decimal | None:
        if len(self._closes) < self.period + 1:
            return None
        changes = [
            current - previous
            for previous, current in zip(
                self._closes[-self.period - 1 : -1],
                self._closes[-self.period :],
            )
        ]
        gains = sum(
            (max(change, Decimal("0")) for change in changes),
            Decimal("0"),
        )
        losses = sum(
            (max(-change, Decimal("0")) for change in changes),
            Decimal("0"),
        )
        if losses == 0:
            return Decimal("100")
        if gains == 0:
            return Decimal("0")
        relative_strength = gains / losses
        return Decimal("100") - (
            Decimal("100") / (Decimal("1") + relative_strength)
        )

    @staticmethod
    def _validated_close(close: Decimal) -> Decimal:
        value = Decimal(close)
        if value <= 0:
            raise ValueError("close must be > 0")
        return value


class RsiSignalSimulationAdapter:
    """Convert close-based RSI signals to next-Bar-open instructions."""

    def __init__(
        self,
        instrument: str,
        rule: RsiSignalRule | None = None,
    ) -> None:
        if not instrument.strip():
            raise ValueError("instrument must not be empty")
        self.instrument = instrument
        self.rule = rule or RsiSignalRule()
        self._intent_book = ExampleTradeIntentBook()

    def initialize(self, frame: MarketFrame) -> None:
        self._check_instrument(frame.instrument)
        self.rule.initialize(frame.close)

    def instructions_for(
        self,
        frame: MarketFrame,
    ) -> tuple[TradeInstruction, ...]:
        self._check_instrument(frame.instrument)
        return self._intent_book.instructions_for(frame)

    def visible_intents(self) -> tuple[IntentSnapshot, ...]:
        return self._intent_book.visible_intents()

    def on_fills(self, fills: Sequence[SimFill]) -> None:
        self._intent_book.on_fills(fills)
        self.rule.on_fills(
            tuple(
                RsiSignalFill(
                    intent_key=fill.source_intent_key,
                    side=RsiSignalSide(fill.side.value),
                )
                for fill in fills
            )
        )

    def on_market(self, frame: MarketFrame) -> None:
        self._check_instrument(frame.instrument)
        signals = self.rule.on_market(
            frame.close,
            sequence=frame.sequence,
        )
        self._intent_book.enqueue_active(
            tuple(self._active_intent(signal) for signal in signals),
            current_sequence=frame.sequence,
        )

    def _active_intent(self, signal: RsiSignal) -> ActiveTradeIntent:
        return ActiveTradeIntent(
            intent_key=signal.intent_key,
            instrument=self.instrument,
            side=OrderSide(signal.side.value),
            quantity=signal.quantity,
            reduce_only=signal.reduce_only,
            tags={
                "probe": "rsi-signal",
                "role": (
                    "entry"
                    if signal.side == RsiSignalSide.BUY
                    else "exit"
                ),
                "signal_sequence": str(signal.signal_sequence),
                "signal_rsi": str(signal.rsi),
            },
        )

    def _check_instrument(self, instrument: str) -> None:
        if instrument != self.instrument:
            raise ValueError(
                f"unexpected instrument {instrument}; "
                f"expected {self.instrument}"
            )


def run_rsi_probe() -> SimulationResult:
    source = FixedBarMarketSource(INSTRUMENT, RSI_BARS)
    adapter = RsiSignalSimulationAdapter(INSTRUMENT)
    return SimulationRunner(
        source,
        trade_port=adapter,
        initial_equity=INITIAL_EQUITY,
    ).run()
