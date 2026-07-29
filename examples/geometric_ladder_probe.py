from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

from examples.intent_adapter_support import (
    ExampleTradeIntentBook,
    PassiveTradeIntent,
)
from market_protocol import MarketFrame
from market_simulator import AnchoredGBMMarketSource
from simulation_runtime import (
    IntentSnapshot,
    OrderSide,
    SimFill,
    SimulationResult,
    SimulationRunner,
    TradeInstruction,
)


INSTRUMENT = "BTCUSD"
ANCHORS = (
    ("2026-01-01", "65000"),
    ("2026-07-01", "40000"),
    ("2027-01-01", "115000"),
    ("2027-07-01", "55000"),
    ("2028-01-01", "200000"),
    ("2028-07-01", "45000"),
    ("2029-01-01", "160000"),
)
SEED = 42
ANNUAL_VOLATILITY = Decimal("0.60")
PRICE_FLOOR = Decimal("40000")
PRICE_CEILING = Decimal("200000")
STEP_RATIO = Decimal("1.04")
LEVEL_COUNT = 5
ORDER_QUANTITY = Decimal("0.01")
INITIAL_EQUITY = Decimal("10000")
PRICE_QUANTUM = Decimal("0.01")


@dataclass(slots=True)
class _LevelState:
    index: int
    buy_price: Decimal
    sell_price: Decimal
    cycle: int = 0
    intent: PassiveTradeIntent | None = None


class GeometricLadderTradeProvider:
    """Long-only passive ladder used to exercise the generic runtime.

    Buy prices are spaced geometrically below the first close. After one level
    buys, that level offers the same quantity one geometric step higher. A
    completed sell rearms the original buy level with a fresh logical key.

    This deliberately small example is neither a production grid rule engine
    nor a complete trading strategy.
    """

    def __init__(
        self,
        *,
        step_ratio: Decimal = STEP_RATIO,
        level_count: int = LEVEL_COUNT,
        quantity: Decimal = ORDER_QUANTITY,
        price_quantum: Decimal = PRICE_QUANTUM,
    ) -> None:
        self.step_ratio = Decimal(step_ratio)
        self.level_count = level_count
        self.quantity = Decimal(quantity)
        self.price_quantum = Decimal(price_quantum)
        if self.step_ratio <= 1:
            raise ValueError("step_ratio must be > 1")
        if self.level_count < 1:
            raise ValueError("level_count must be >= 1")
        if self.quantity <= 0:
            raise ValueError("quantity must be > 0")
        if self.price_quantum <= 0:
            raise ValueError("price_quantum must be > 0")
        self._instrument = ""
        self._levels: dict[int, _LevelState] = {}
        self._book = ExampleTradeIntentBook()

    def initialize(self, frame: MarketFrame) -> None:
        self._instrument = frame.instrument
        self._levels = {}
        for index in range(1, self.level_count + 1):
            buy_price = self._price(frame.close / (self.step_ratio**index))
            sell_price = self._price(buy_price * self.step_ratio)
            state = _LevelState(index, buy_price, sell_price)
            state.intent = self._intent(state, OrderSide.BUY)
            self._levels[index] = state
        self._book.synchronize_passive(
            self._passive_intents(),
            current_sequence=frame.sequence,
        )

    def instructions_for(
        self,
        frame: MarketFrame,
    ) -> tuple[TradeInstruction, ...]:
        if frame.instrument != self._instrument:
            raise ValueError(
                f"unexpected instrument {frame.instrument}; expected {self._instrument}"
            )
        return self._book.instructions_for(frame)

    def visible_intents(self) -> tuple[IntentSnapshot, ...]:
        return self._book.visible_intents()

    def on_market(self, frame: MarketFrame) -> None:
        if frame.instrument != self._instrument:
            raise ValueError(
                f"unexpected instrument {frame.instrument}; expected {self._instrument}"
            )

    def on_fills(
        self,
        fills: Sequence[SimFill],
    ) -> None:
        current_sequence = self._fill_sequence(fills)
        self._book.on_fills(fills)
        for fill in fills:
            level_index = int(fill.tags["ladder_level"])
            state = self._levels[level_index]
            if (
                state.intent is None
                or fill.source_intent_key != state.intent.intent_key
            ):
                raise ValueError(
                    f"unexpected fill: {fill.source_intent_key}"
                )
            if fill.side == OrderSide.BUY:
                state.intent = self._intent(state, OrderSide.SELL)
            else:
                state.cycle += 1
                state.intent = self._intent(state, OrderSide.BUY)
        self._book.synchronize_passive(
            self._passive_intents(),
            current_sequence=current_sequence,
        )

    def _intent(
        self,
        state: _LevelState,
        side: OrderSide,
    ) -> PassiveTradeIntent:
        role = side.value.lower()
        return PassiveTradeIntent(
            intent_key=f"ladder:{state.index}:{role}:{state.cycle}",
            instrument=self._instrument,
            side=side,
            quantity=self.quantity,
            target_price=(
                state.buy_price if side == OrderSide.BUY else state.sell_price
            ),
            reduce_only=side == OrderSide.SELL,
            tags={
                "probe": "geometric-ladder",
                "ladder_level": str(state.index),
                "cycle": str(state.cycle),
                "role": role,
            },
        )

    def _passive_intents(self) -> tuple[PassiveTradeIntent, ...]:
        return tuple(
            state.intent
            for state in self._levels.values()
            if state.intent is not None
        )

    @staticmethod
    def _fill_sequence(fills: Sequence[SimFill]) -> int:
        if not fills:
            raise ValueError("fills must not be empty")
        sequences = {fill.sequence for fill in fills}
        if len(sequences) != 1:
            raise ValueError("all fills must belong to the same frame")
        return next(iter(sequences))

    def _price(self, value: Decimal) -> Decimal:
        return value.quantize(self.price_quantum, rounding=ROUND_HALF_UP)


def run_ladder_probe(seed: int = SEED) -> SimulationResult:
    source = AnchoredGBMMarketSource(
        INSTRUMENT,
        ANCHORS,
        annual_volatility=ANNUAL_VOLATILITY,
        intraday_steps=24,
        price_floor=PRICE_FLOOR,
        price_ceiling=PRICE_CEILING,
    )
    return SimulationRunner(
        source,
        trade_port=GeometricLadderTradeProvider(),
        initial_equity=INITIAL_EQUITY,
    ).run(seed=seed)
