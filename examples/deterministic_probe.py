from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence

from market_protocol import MarketFrame
from market_simulator import FixedBarMarketSource
from simulation_runtime import (
    OrderSide,
    OrderType,
    SimFill,
    SimOrder,
    SimulationDecision,
    SimulationResult,
    SimulationRunner,
)


INSTRUMENT = "BTCUSD"
START_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
INITIAL_EQUITY = Decimal("1000")

# This path is deliberately small and human-verifiable. It exercises a
# cancelled limit, a replacement limit, a take-profit limit, and a market exit.
BARS = (
    ("100", "102", "98", "101"),
    ("101", "103", "97", "100"),
    ("100", "104", "98", "103"),
    ("103", "109", "101", "108"),
    ("108", "109", "96", "97"),
    ("96", "100", "94", "99"),
)


class DeterministicProbeDecisionProvider:
    """A tiny state machine used only to verify the runtime."""

    def __init__(self) -> None:
        self._desired: tuple[SimOrder, ...] = ()
        self._entry_filled = False
        self._partial_exit_filled = False
        self._market_exit_submitted = False

    def initialize(self, frame: MarketFrame) -> SimulationDecision:
        self._desired = (
            self._limit(
                "probe:entry:original",
                frame.instrument,
                OrderSide.BUY,
                price="95",
                quantity="2",
                step="original-entry",
            ),
        )
        return SimulationDecision(self._desired)

    def on_market(self, frame: MarketFrame) -> SimulationDecision:
        if frame.sequence == 1 and not self._entry_filled:
            # Omitting the original key cancels it; the new key is its replacement.
            self._desired = (
                self._limit(
                    "probe:entry:replacement",
                    frame.instrument,
                    OrderSide.BUY,
                    price="99",
                    quantity="2",
                    step="replacement-entry",
                ),
            )
        elif (
            self._partial_exit_filled
            and not self._market_exit_submitted
            and frame.close <= Decimal("98")
        ):
            self._market_exit_submitted = True
            self._desired = (
                SimOrder(
                    order_key="probe:exit:market",
                    instrument=frame.instrument,
                    side=OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    quantity=Decimal("1"),
                    tags={"probe_step": "market-exit"},
                ),
            )
        return SimulationDecision(self._desired)

    def on_fills(
        self,
        fills: Sequence[SimFill],
    ) -> SimulationDecision:
        for fill in fills:
            if fill.order_key == "probe:entry:replacement":
                self._entry_filled = True
                self._desired = (
                    self._limit(
                        "probe:exit:take-profit",
                        fill.instrument,
                        OrderSide.SELL,
                        price="108",
                        quantity="1",
                        step="take-profit",
                    ),
                )
            elif fill.order_key == "probe:exit:take-profit":
                self._partial_exit_filled = True
                self._desired = ()
            elif fill.order_key == "probe:exit:market":
                self._desired = ()
        return SimulationDecision(self._desired)

    @staticmethod
    def _limit(
        order_key: str,
        instrument: str,
        side: OrderSide,
        *,
        price: str,
        quantity: str,
        step: str,
    ) -> SimOrder:
        return SimOrder(
            order_key=order_key,
            instrument=instrument,
            side=side,
            order_type=OrderType.LIMIT,
            limit_price=Decimal(price),
            quantity=Decimal(quantity),
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
        DeterministicProbeDecisionProvider(),
        initial_equity=INITIAL_EQUITY,
    ).run()
