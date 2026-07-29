from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence

from examples.intent_adapter_support import (
    ActiveTradeIntent,
    ExampleTradeIntentBook,
    PassiveTradeIntent,
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
START_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
INITIAL_EQUITY = Decimal("1000")

# This path is deliberately small and human-verifiable. It exercises a
# cancelled passive intent, its replacement, passive take profit, and an active
# exit that executes at the following open.
BARS = (
    ("100", "102", "98", "101"),
    ("101", "103", "97", "100"),
    ("100", "104", "98", "103"),
    ("103", "109", "101", "108"),
    ("108", "109", "96", "97"),
    ("96", "100", "94", "99"),
)


class DeterministicProbeTradeProvider:
    """A tiny intent-owning adapter used only to verify the runtime."""

    def __init__(self) -> None:
        self._book = ExampleTradeIntentBook()
        self._passive: tuple[PassiveTradeIntent, ...] = ()
        self._entry_filled = False
        self._partial_exit_filled = False
        self._active_exit_submitted = False

    def initialize(self, frame: MarketFrame) -> None:
        self._passive = (
            self._passive_intent(
                "probe:entry:original",
                frame.instrument,
                OrderSide.BUY,
                price="95",
                quantity="2",
                step="original-entry",
            ),
        )
        self._book.synchronize_passive(
            self._passive,
            current_sequence=frame.sequence,
        )

    def instructions_for(
        self,
        frame: MarketFrame,
    ) -> tuple[TradeInstruction, ...]:
        return self._book.instructions_for(frame)

    def visible_intents(self) -> tuple[IntentSnapshot, ...]:
        return self._book.visible_intents()

    def on_market(self, frame: MarketFrame) -> None:
        if frame.sequence == 1 and not self._entry_filled:
            # Omitting the original key cancels it; the new key replaces it.
            self._passive = (
                self._passive_intent(
                    "probe:entry:replacement",
                    frame.instrument,
                    OrderSide.BUY,
                    price="99",
                    quantity="2",
                    step="replacement-entry",
                ),
            )
            self._book.synchronize_passive(
                self._passive,
                current_sequence=frame.sequence,
            )
        elif (
            self._partial_exit_filled
            and not self._active_exit_submitted
            and frame.close <= Decimal("98")
        ):
            self._active_exit_submitted = True
            self._book.enqueue_active(
                (
                    ActiveTradeIntent(
                        intent_key="probe:exit:active",
                        instrument=frame.instrument,
                        side=OrderSide.SELL,
                        quantity=Decimal("1"),
                        reduce_only=True,
                        tags={"probe_step": "active-exit"},
                    ),
                ),
                current_sequence=frame.sequence,
            )

    def on_fills(
        self,
        fills: Sequence[SimFill],
    ) -> None:
        self._book.on_fills(fills)
        for fill in fills:
            if fill.source_intent_key == "probe:entry:replacement":
                self._entry_filled = True
                self._passive = (
                    self._passive_intent(
                        "probe:exit:take-profit",
                        fill.instrument,
                        OrderSide.SELL,
                        price="108",
                        quantity="1",
                        step="take-profit",
                        reduce_only=True,
                    ),
                )
                self._book.synchronize_passive(
                    self._passive,
                    current_sequence=fill.sequence,
                )
            elif fill.source_intent_key == "probe:exit:take-profit":
                self._partial_exit_filled = True
                self._passive = ()
                self._book.synchronize_passive(
                    (),
                    current_sequence=fill.sequence,
                )
            elif fill.source_intent_key == "probe:exit:active":
                pass
            else:
                raise ValueError(
                    f"unexpected fill: {fill.source_intent_key}"
                )

    @staticmethod
    def _passive_intent(
        intent_key: str,
        instrument: str,
        side: OrderSide,
        *,
        price: str,
        quantity: str,
        step: str,
        reduce_only: bool = False,
    ) -> PassiveTradeIntent:
        return PassiveTradeIntent(
            intent_key=intent_key,
            instrument=instrument,
            side=side,
            target_price=Decimal(price),
            quantity=Decimal(quantity),
            reduce_only=reduce_only,
            tags={"probe_step": step},
        )


def run_probe() -> SimulationResult:
    source = FixedBarMarketSource(
        INSTRUMENT,
        BARS,
        start_timestamp=int(START_DATE.timestamp() * 1_000),
    )
    return SimulationRunner(
        source,
        trade_port=DeterministicProbeTradeProvider(),
        initial_equity=INITIAL_EQUITY,
    ).run()
