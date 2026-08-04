"""Passive grid-intent execution support for simulation adapters."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Mapping, Sequence

from grid_rule import GridFill, GridOrderIntent, GridOrderRole, GridOrderSide
from market_protocol import MarketFrame
from simulation_runtime import (
    IntentSnapshot,
    OrderSide,
    SimFill,
    TradeInstruction,
    TradeIntentMode,
)


def bar_covers_price(frame: MarketFrame, price: Decimal) -> bool:
    """Return whether a completed OHLC bar inclusively covers one price."""

    return frame.low <= price <= frame.high


def simulation_fills_to_grid_fills(
    fills: Sequence[SimFill],
) -> tuple[GridFill, ...]:
    """Translate runtime fills without adding execution semantics."""

    return tuple(
        GridFill(
            order_key=fill.source_intent_key,
            instrument=fill.instrument,
            side=GridOrderSide(fill.side.value),
            price=fill.price,
            quantity=fill.quantity,
            sequence=fill.sequence,
            timestamp=fill.timestamp,
        )
        for fill in fills
    )


@dataclass(slots=True)
class _TrackedIntent:
    intent: GridOrderIntent
    active_after_sequence: int
    issued_at_sequence: int | None = None


class PassiveGridIntentBook:
    """Own pending grid intents and resolve them into current-bar trades.

    The rule/strategy remains the source of truth for the complete desired
    intent set. This book only preserves when each intent became effective and
    ensures that an instruction is issued at most once.
    """

    def __init__(self) -> None:
        self._tracked: dict[str, _TrackedIntent] = {}
        self._retired_keys: set[str] = set()

    @property
    def intents(self) -> tuple[GridOrderIntent, ...]:
        return tuple(
            tracked.intent
            for tracked in sorted(
                self._tracked.values(),
                key=lambda tracked: tracked.intent.order_key,
            )
        )

    def synchronize(
        self,
        intents: Sequence[GridOrderIntent],
        *,
        current_sequence: int,
    ) -> None:
        """Replace the desired set while preserving existing activation age."""

        desired: dict[str, GridOrderIntent] = {}
        for intent in intents:
            if intent.order_key in desired:
                raise ValueError(
                    f"duplicate grid intent key: {intent.order_key}"
                )
            desired[intent.order_key] = intent

        removed_keys = set(self._tracked) - set(desired)
        self._retired_keys.update(removed_keys)

        reused_keys = (
            set(desired) - set(self._tracked)
        ) & self._retired_keys
        if reused_keys:
            raise ValueError(
                "retired grid intent keys must not be reused: "
                + ", ".join(sorted(reused_keys))
            )

        synchronized: dict[str, _TrackedIntent] = {}
        for intent_key, intent in desired.items():
            existing = self._tracked.get(intent_key)
            if existing is not None:
                if existing.intent != intent:
                    raise ValueError(
                        "grid intent changed without a new key: "
                        f"{intent_key}"
                    )
                synchronized[intent_key] = existing
                continue
            synchronized[intent_key] = _TrackedIntent(
                intent=intent,
                active_after_sequence=current_sequence,
            )
        self._tracked = synchronized

    def instructions_for(
        self,
        frame: MarketFrame,
        *,
        tags_for: Callable[[GridOrderIntent], Mapping[str, str]],
    ) -> tuple[TradeInstruction, ...]:
        """Resolve eligible pre-existing passive intents for one OHLC bar."""

        eligible = [
            tracked
            for tracked in self._tracked.values()
            if tracked.intent.instrument == frame.instrument
            and tracked.active_after_sequence < frame.sequence
            and tracked.issued_at_sequence is None
            and bar_covers_price(frame, tracked.intent.price)
        ]
        eligible.sort(key=lambda tracked: tracked.intent.order_key)

        instructions = tuple(
            TradeInstruction(
                instruction_key=(
                    f"{tracked.intent.order_key}:frame:{frame.sequence}"
                ),
                source_intent_key=tracked.intent.order_key,
                instrument=tracked.intent.instrument,
                side=OrderSide(tracked.intent.side.value),
                quantity=tracked.intent.quantity,
                price=tracked.intent.price,
                frame_sequence=frame.sequence,
                intent_mode=TradeIntentMode.PASSIVE,
                reduce_only=(
                    tracked.intent.role == GridOrderRole.EXIT
                ),
                tags=tags_for(tracked.intent),
            )
            for tracked in eligible
        )
        for tracked in eligible:
            tracked.issued_at_sequence = frame.sequence
        return instructions

    def visible_intents(
        self,
        *,
        tags_for: Callable[[GridOrderIntent], Mapping[str, str]],
    ) -> tuple[IntentSnapshot, ...]:
        """Expose a read-only snapshot for Runtime reporting."""

        return tuple(
            IntentSnapshot(
                intent_key=tracked.intent.order_key,
                instrument=tracked.intent.instrument,
                side=OrderSide(tracked.intent.side.value),
                quantity=tracked.intent.quantity,
                intent_mode=TradeIntentMode.PASSIVE,
                target_price=tracked.intent.price,
                reduce_only=(
                    tracked.intent.role == GridOrderRole.EXIT
                ),
                tags=tags_for(tracked.intent),
            )
            for tracked in sorted(
                self._tracked.values(),
                key=lambda tracked: tracked.intent.order_key,
            )
        )
