from __future__ import annotations

import unittest
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
    SimulationDecisionPort,
    SimulationRunner,
)


class ScriptedRoundTripDecisionProvider:
    """Test decision provider that requests one round trip."""

    def __init__(self) -> None:
        self._desired: tuple[SimOrder, ...] = ()

    def initialize(self, frame: MarketFrame) -> SimulationDecision:
        self._desired = (
            SimOrder(
                order_key="example:open:0",
                instrument=frame.instrument,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                limit_price=Decimal("100"),
                quantity=Decimal("1"),
                tags={"test_step": "open"},
            ),
        )
        return SimulationDecision(self._desired)

    def on_market(self, frame: MarketFrame) -> SimulationDecision:
        del frame
        return SimulationDecision(self._desired)

    def on_fills(
        self,
        fills: Sequence[SimFill],
    ) -> SimulationDecision:
        fill = fills[-1]
        if fill.order_key == "example:open:0":
            self._desired = (
                SimOrder(
                    order_key="example:close:0",
                    instrument=fill.instrument,
                    side=OrderSide.SELL,
                    order_type=OrderType.LIMIT,
                    limit_price=Decimal("110"),
                    quantity=fill.quantity,
                    tags={"test_step": "close"},
                ),
            )
        else:
            self._desired = ()
        return SimulationDecision(self._desired)


class RepeatsFilledOrderDecisionProvider(ScriptedRoundTripDecisionProvider):
    def on_fills(
        self,
        fills: Sequence[SimFill],
    ) -> SimulationDecision:
        del fills
        return SimulationDecision(self._desired)


class CloseSignalDecisionProvider:
    def __init__(self) -> None:
        self._desired: tuple[SimOrder, ...] = ()
        self._submitted = False

    def initialize(self, frame: MarketFrame) -> SimulationDecision:
        del frame
        return SimulationDecision()

    def on_market(self, frame: MarketFrame) -> SimulationDecision:
        if not self._submitted and frame.close <= Decimal("90"):
            self._submitted = True
            self._desired = (
                SimOrder(
                    order_key="close-signal-buy",
                    instrument=frame.instrument,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    quantity=Decimal("1"),
                ),
            )
        return SimulationDecision(self._desired)

    def on_fills(
        self,
        fills: Sequence[SimFill],
    ) -> SimulationDecision:
        del fills
        self._desired = ()
        return SimulationDecision()


class SimulationRunnerTests(unittest.TestCase):
    def test_public_api_uses_decision_port_vocabulary(self) -> None:
        self.assertTrue(hasattr(SimulationDecisionPort, "on_market"))
        self.assertTrue(hasattr(SimulationDecision, "desired_orders"))

    def test_fixed_path_closes_a_profitable_linear_cycle(self) -> None:
        source = FixedBarMarketSource(
            "BTCUSD",
            [
                ("105", "106", "104", "105"),
                # Both 100 and 110 are covered, but only the entry existed
                # before this bar. Its newly-created exit starts next bar.
                ("105", "111", "95", "96"),
                ("96", "112", "94", "108"),
            ],
        )
        runner = SimulationRunner(
            source,
            ScriptedRoundTripDecisionProvider(),
            initial_equity=Decimal("1000"),
        )

        result = runner.run(seed=123)

        self.assertEqual(
            [fill.order_key for fill in result.fills],
            ["example:open:0", "example:close:0"],
        )
        self.assertEqual([fill.price for fill in result.fills], [Decimal("100"), Decimal("110")])
        self.assertEqual(result.fills[0].tags, {"test_step": "open"})
        self.assertEqual(result.final_positions, {})
        self.assertEqual(result.final_cash, Decimal("1010"))
        self.assertEqual(result.final_equity, Decimal("1010"))

    def test_filled_logical_order_key_cannot_be_reused(self) -> None:
        runner = SimulationRunner(
            FixedBarMarketSource(
                "BTCUSD",
                [("105", "106", "104", "105"), ("105", "106", "95", "96")],
            ),
            RepeatsFilledOrderDecisionProvider(),
        )

        with self.assertRaisesRegex(ValueError, "closed order keys must be retired"):
            runner.run()

    def test_close_signal_market_order_fills_at_following_open(self) -> None:
        runner = SimulationRunner(
            FixedBarMarketSource(
                "BTCUSD",
                [
                    ("100", "105", "95", "100"),
                    ("100", "102", "79", "80"),
                    ("85", "91", "82", "89"),
                ],
            ),
            CloseSignalDecisionProvider(),
            initial_equity=Decimal("1000"),
        )

        result = runner.run()

        self.assertEqual(len(result.fills), 1)
        self.assertEqual(result.fills[0].order_key, "close-signal-buy")
        self.assertEqual(result.fills[0].price, Decimal("85"))


if __name__ == "__main__":
    unittest.main()
