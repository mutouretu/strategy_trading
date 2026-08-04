from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SIMULATOR_ROOT = PROJECT_ROOT.parent / "market_simulator"
if not SIMULATOR_ROOT.exists():
    raise unittest.SkipTest("sibling market_simulator project is not available")
for package_path in (
    PROJECT_ROOT,
    SIMULATOR_ROOT / "packages" / "market_protocol" / "src",
    SIMULATOR_ROOT / "packages" / "market_simulator" / "src",
    SIMULATOR_ROOT / "packages" / "simulation_runtime" / "src",
):
    sys.path.insert(0, str(package_path))

from grid_rule import (  # noqa: E402
    GridMode,
    GridOrderIntent,
    GridOrderRole,
    GridOrderSide,
    GridRuleConfig,
)
from grid_rule.adapters import (  # noqa: E402
    GridRuleSimulationAdapter,
    PassiveGridIntentBook,
    bar_covers_price,
)
from market_protocol import MarketFrame  # noqa: E402
from market_simulator import FixedBarMarketSource  # noqa: E402
from simulation_runtime import (  # noqa: E402
    IntentStatus,
    SimulationRunner,
    TradeIntentMode,
)


def frame(
    sequence: int,
    *,
    open_price: str,
    high: str,
    low: str,
    close: str,
    instrument: str = "BTCUSDT",
) -> MarketFrame:
    return MarketFrame(
        sequence=sequence,
        timestamp=sequence,
        instrument=instrument,
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
    )


def intent(
    key: str,
    *,
    side: GridOrderSide = GridOrderSide.BUY,
    role: GridOrderRole = GridOrderRole.ENTRY,
    price: str = "100",
) -> GridOrderIntent:
    return GridOrderIntent(
        order_key=key,
        instrument="BTCUSDT",
        side=side,
        role=role,
        price=Decimal(price),
        quantity=Decimal("1"),
        cell_id=f"cell:{key}",
        cycle=0,
    )


def rule_config(
    *,
    grid_id: str = "trade-port-grid",
    move_grid: bool = False,
    mode: GridMode = GridMode.LONG,
) -> GridRuleConfig:
    return GridRuleConfig(
        grid_id=grid_id,
        instrument="BTCUSDT",
        mode=mode,
        anchor_price=Decimal(
            "110" if mode == GridMode.LONG else "100"
        ),
        grid_ratio=Decimal("0.10"),
        grid_count=1,
        order_notional=Decimal("100"),
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        move_grid=move_grid,
    )


class PassiveIntentResolutionTests(unittest.TestCase):
    def test_bar_coverage_is_inclusive_and_does_not_infer_gap_trades(
        self,
    ) -> None:
        current = frame(
            1,
            open_price="110",
            high="120",
            low="105",
            close="115",
        )

        self.assertTrue(bar_covers_price(current, Decimal("105")))
        self.assertTrue(bar_covers_price(current, Decimal("120")))
        self.assertFalse(bar_covers_price(current, Decimal("100")))

    def test_book_waits_resolves_both_sides_and_issues_only_once(
        self,
    ) -> None:
        book = PassiveGridIntentBook()
        entry = intent("b-entry")
        exit_intent = intent(
            "a-exit",
            side=GridOrderSide.SELL,
            role=GridOrderRole.EXIT,
            price="110",
        )
        book.synchronize((entry, exit_intent), current_sequence=0)
        tags_for = lambda item: {"role": item.role.value}

        self.assertEqual(
            book.instructions_for(
                frame(
                    0,
                    open_price="105",
                    high="115",
                    low="95",
                    close="105",
                ),
                tags_for=tags_for,
            ),
            (),
        )
        self.assertEqual(
            book.instructions_for(
                frame(
                    1,
                    open_price="105",
                    high="109",
                    low="101",
                    close="105",
                ),
                tags_for=tags_for,
            ),
            (),
        )

        instructions = book.instructions_for(
            frame(
                2,
                open_price="105",
                high="110",
                low="100",
                close="105",
            ),
            tags_for=tags_for,
        )

        self.assertEqual(
            [instruction.source_intent_key for instruction in instructions],
            ["a-exit", "b-entry"],
        )
        self.assertEqual(
            [instruction.side.value for instruction in instructions],
            ["SELL", "BUY"],
        )
        self.assertEqual(
            [instruction.price for instruction in instructions],
            [Decimal("110"), Decimal("100")],
        )
        self.assertEqual(
            [instruction.reduce_only for instruction in instructions],
            [True, False],
        )
        self.assertEqual(
            {instruction.intent_mode for instruction in instructions},
            {TradeIntentMode.PASSIVE},
        )
        self.assertEqual(
            book.instructions_for(
                frame(
                    3,
                    open_price="105",
                    high="110",
                    low="100",
                    close="105",
                ),
                tags_for=tags_for,
            ),
            (),
        )

    def test_cancelled_intent_cannot_fill_or_reuse_its_key(self) -> None:
        book = PassiveGridIntentBook()
        cancelled = intent("cancelled")
        book.synchronize((cancelled,), current_sequence=0)
        book.synchronize((), current_sequence=1)

        self.assertEqual(
            book.instructions_for(
                frame(
                    2,
                    open_price="100",
                    high="100",
                    low="100",
                    close="100",
                ),
                tags_for=lambda item: {},
            ),
            (),
        )
        with self.assertRaisesRegex(
            ValueError,
            "retired grid intent keys must not be reused",
        ):
            book.synchronize((cancelled,), current_sequence=2)


class GridTradePortTests(unittest.TestCase):
    def test_fill_created_exit_waits_until_the_next_bar(self) -> None:
        adapter = GridRuleSimulationAdapter(rule_config())
        source = FixedBarMarketSource(
            "BTCUSDT",
            [
                ("105", "106", "104", "105"),
                ("105", "120", "99", "101"),
                ("110", "111", "109", "110"),
            ],
        )

        result = SimulationRunner(
            source,
            trade_port=adapter,
            initial_equity=Decimal("1000"),
        ).run()

        self.assertEqual(
            [
                (fill.side.value, fill.price, fill.sequence)
                for fill in result.fills
            ],
            [
                ("BUY", Decimal("100"), 1),
                ("SELL", Decimal("110"), 2),
            ],
        )
        self.assertEqual(
            [record.status for record in result.intents],
            [
                IntentStatus.FILLED,
                IntentStatus.FILLED,
                IntentStatus.WAITING,
            ],
        )
        self.assertEqual(len(result.instructions), 2)
        self.assertEqual(adapter.engine.completed_cycles, 1)
        self.assertEqual(
            result.fills[0].intent_mode,
            TradeIntentMode.PASSIVE,
        )
        self.assertEqual(
            result.fills[0].tags["role"],
            GridOrderRole.ENTRY.value,
        )
        self.assertEqual(
            result.fills[1].tags["role"],
            GridOrderRole.EXIT.value,
        )

if __name__ == "__main__":
    unittest.main()
