"""Example-only active/passive intent resolution for simulation probes.

These types deliberately live outside ``simulation_runtime``. They demonstrate
what a strategy-owned simulation adapter is responsible for without making the
generic runtime own strategy intent lifecycles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping, Sequence

from market_protocol import MarketFrame
from simulation_runtime import (
    IntentSnapshot,
    OrderSide,
    SimFill,
    TradeInstruction,
    TradeIntentMode,
)


@dataclass(frozen=True, slots=True)
class PassiveTradeIntent:
    intent_key: str
    instrument: str
    side: OrderSide
    quantity: Decimal
    target_price: Decimal
    reduce_only: bool = False
    tags: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_common_intent_fields(
            intent_key=self.intent_key,
            instrument=self.instrument,
            side=self.side,
            quantity=self.quantity,
            reduce_only=self.reduce_only,
        )
        if self.target_price <= 0:
            raise ValueError("target_price must be > 0")
        object.__setattr__(
            self,
            "tags",
            MappingProxyType(dict(self.tags)),
        )


@dataclass(frozen=True, slots=True)
class ActiveTradeIntent:
    intent_key: str
    instrument: str
    side: OrderSide
    quantity: Decimal
    reduce_only: bool = False
    tags: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_common_intent_fields(
            intent_key=self.intent_key,
            instrument=self.instrument,
            side=self.side,
            quantity=self.quantity,
            reduce_only=self.reduce_only,
        )
        object.__setattr__(
            self,
            "tags",
            MappingProxyType(dict(self.tags)),
        )


TradeIntent = PassiveTradeIntent | ActiveTradeIntent


@dataclass(slots=True)
class _TrackedIntent:
    intent: TradeIntent
    active_after_sequence: int
    issued_at_sequence: int | None = None


class ExampleTradeIntentBook:
    """Resolve strategy-owned example intents into explicit current trades."""

    def __init__(self) -> None:
        self._passive: dict[str, _TrackedIntent] = {}
        self._active: dict[str, _TrackedIntent] = {}
        self._retired_keys: set[str] = set()

    def synchronize_passive(
        self,
        intents: Sequence[PassiveTradeIntent],
        *,
        current_sequence: int,
    ) -> None:
        """Replace passive desired intents while preserving activation age."""

        desired: dict[str, PassiveTradeIntent] = {}
        for intent in intents:
            if intent.intent_key in desired:
                raise ValueError(
                    f"duplicate passive intent key: {intent.intent_key}"
                )
            desired[intent.intent_key] = intent

        removed_keys = set(self._passive) - set(desired)
        self._retired_keys.update(removed_keys)

        new_keys = set(desired) - set(self._passive)
        unavailable = new_keys & (
            self._retired_keys | set(self._active)
        )
        if unavailable:
            raise ValueError(
                "intent keys must not be reused: "
                + ", ".join(sorted(unavailable))
            )

        synchronized: dict[str, _TrackedIntent] = {}
        for intent_key, intent in desired.items():
            existing = self._passive.get(intent_key)
            if existing is not None:
                if existing.intent != intent:
                    raise ValueError(
                        "passive intent changed without a new key: "
                        f"{intent_key}"
                    )
                synchronized[intent_key] = existing
                continue
            synchronized[intent_key] = _TrackedIntent(
                intent=intent,
                active_after_sequence=current_sequence,
            )
        self._passive = synchronized

    def enqueue_active(
        self,
        intents: Sequence[ActiveTradeIntent],
        *,
        current_sequence: int,
    ) -> None:
        """Queue one-shot active intents for the next executable frame."""

        batch_keys: set[str] = set()
        for intent in intents:
            if intent.intent_key in batch_keys:
                raise ValueError(
                    f"duplicate active intent key: {intent.intent_key}"
                )
            batch_keys.add(intent.intent_key)

        unavailable = batch_keys & (
            self._retired_keys
            | set(self._passive)
            | set(self._active)
        )
        if unavailable:
            raise ValueError(
                "intent keys must not be reused: "
                + ", ".join(sorted(unavailable))
            )

        for intent in intents:
            self._active[intent.intent_key] = _TrackedIntent(
                intent=intent,
                active_after_sequence=current_sequence,
            )

    def instructions_for(
        self,
        frame: MarketFrame,
    ) -> tuple[TradeInstruction, ...]:
        """Resolve eligible intents without letting them see their birth Bar."""

        tracked = [
            item
            for item in (*self._passive.values(), *self._active.values())
            if item.intent.instrument == frame.instrument
            and item.active_after_sequence < frame.sequence
            and item.issued_at_sequence is None
            and self._is_triggered(item.intent, frame)
        ]
        tracked.sort(key=lambda item: item.intent.intent_key)

        instructions = tuple(
            self._instruction(item.intent, frame)
            for item in tracked
        )
        for item in tracked:
            item.issued_at_sequence = frame.sequence
        return instructions

    def visible_intents(self) -> tuple[IntentSnapshot, ...]:
        """Expose current strategy intents without execution authority."""

        tracked = sorted(
            (*self._passive.values(), *self._active.values()),
            key=lambda item: item.intent.intent_key,
        )
        return tuple(
            IntentSnapshot(
                intent_key=item.intent.intent_key,
                instrument=item.intent.instrument,
                side=item.intent.side,
                quantity=item.intent.quantity,
                intent_mode=(
                    TradeIntentMode.ACTIVE
                    if isinstance(item.intent, ActiveTradeIntent)
                    else TradeIntentMode.PASSIVE
                ),
                target_price=(
                    None
                    if isinstance(item.intent, ActiveTradeIntent)
                    else item.intent.target_price
                ),
                reduce_only=item.intent.reduce_only,
                tags=item.intent.tags,
            )
            for item in tracked
        )

    def on_fills(self, fills: Sequence[SimFill]) -> None:
        """Retire exactly the issued source intents acknowledged by Runtime."""

        for fill in fills:
            tracked = self._passive.get(fill.source_intent_key)
            store = self._passive
            if tracked is None:
                tracked = self._active.get(fill.source_intent_key)
                store = self._active
            if tracked is None or tracked.issued_at_sequence is None:
                raise ValueError(
                    "unexpected fill intent key: "
                    f"{fill.source_intent_key}"
                )
            if fill.sequence != tracked.issued_at_sequence:
                raise ValueError(
                    "fill sequence does not match issued intent: "
                    f"{fill.source_intent_key}"
                )
            store.pop(fill.source_intent_key)
            self._retired_keys.add(fill.source_intent_key)

    @staticmethod
    def _is_triggered(
        intent: TradeIntent,
        frame: MarketFrame,
    ) -> bool:
        if isinstance(intent, ActiveTradeIntent):
            return True
        return frame.low <= intent.target_price <= frame.high

    @staticmethod
    def _instruction(
        intent: TradeIntent,
        frame: MarketFrame,
    ) -> TradeInstruction:
        is_active = isinstance(intent, ActiveTradeIntent)
        return TradeInstruction(
            instruction_key=(
                f"{intent.intent_key}:frame:{frame.sequence}"
            ),
            source_intent_key=intent.intent_key,
            instrument=intent.instrument,
            side=intent.side,
            quantity=intent.quantity,
            price=(
                frame.open
                if is_active
                else intent.target_price
            ),
            frame_sequence=frame.sequence,
            intent_mode=(
                TradeIntentMode.ACTIVE
                if is_active
                else TradeIntentMode.PASSIVE
            ),
            reduce_only=intent.reduce_only,
            tags=intent.tags,
        )


def _validate_common_intent_fields(
    *,
    intent_key: str,
    instrument: str,
    side: OrderSide,
    quantity: Decimal,
    reduce_only: bool,
) -> None:
    if not intent_key.strip():
        raise ValueError("intent_key must not be empty")
    if not instrument.strip():
        raise ValueError("instrument must not be empty")
    if not isinstance(side, OrderSide):
        raise TypeError("side must be an OrderSide")
    if quantity <= 0:
        raise ValueError("quantity must be > 0")
    if not isinstance(reduce_only, bool):
        raise TypeError("reduce_only must be a bool")
