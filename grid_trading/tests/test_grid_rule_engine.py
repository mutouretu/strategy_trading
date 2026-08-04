from __future__ import annotations

import sys
import unittest
import ast
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RULE_CORE_ROOT = PROJECT_ROOT / "grid_rule"
sys.path.insert(0, str(PROJECT_ROOT))

from grid_rule import (  # noqa: E402
    CellPhase,
    GridFill,
    GridOrderRole,
    GridOrderSide,
    GridRuleConfig,
    GridMarketType,
    GridMode,
    GridRuleEngine,
    build_grid_cells,
)
from grid_server.domain import Mode, StrategyConfig  # noqa: E402
from grid_server.domain.grid import build_cells  # noqa: E402


def config(mode: GridMode = GridMode.LONG) -> GridRuleConfig:
    return GridRuleConfig(
        grid_id=f"engine-{mode.value}",
        instrument="BTCUSDT",
        mode=mode,
        anchor_price=Decimal("110" if mode == GridMode.LONG else "100"),
        grid_ratio=Decimal("0.10"),
        grid_count=1,
        order_notional=Decimal("100"),
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
    )


def fill_for(intent, sequence: int) -> GridFill:
    return GridFill(
        order_key=intent.order_key,
        instrument=intent.instrument,
        side=intent.side,
        price=intent.price,
        quantity=intent.quantity,
        sequence=sequence,
        timestamp=sequence * 86_400_000,
    )


class GridRuleEngineTests(unittest.TestCase):
    def test_rule_core_has_no_web_or_simulator_dependencies(self) -> None:
        forbidden = {
            "grid_server",
            "market_protocol",
            "market_simulator",
            "simulation_runtime",
        }
        violations: list[str] = []
        for path in RULE_CORE_ROOT.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    modules.append(node.module or "")
                for module in modules:
                    if module.split(".", 1)[0] in forbidden:
                        violations.append(
                            f"{path.relative_to(RULE_CORE_ROOT)}:{node.lineno} -> {module}"
                        )
        self.assertEqual(violations, [])

    def test_initial_cells_match_existing_web_grid_formula(self) -> None:
        rule_config = GridRuleConfig(
            grid_id="formula",
            instrument="BTCUSDT",
            mode=GridMode.LONG,
            anchor_price=Decimal("110"),
            grid_ratio=Decimal("0.10"),
            grid_count=3,
            order_notional=Decimal("100"),
            tick_size=Decimal("0.01"),
        )
        web_config = StrategyConfig(
            strategy_id="formula",
            symbol="BTCUSDT",
            mode=Mode.LONG,
            anchor_price=Decimal("110"),
            grid_ratio=Decimal("0.10"),
            grid_count=3,
            order_usdt=Decimal("100"),
        )

        rule_cells = build_grid_cells(rule_config)
        web_cells = build_cells(web_config, Decimal("0.01"))

        self.assertEqual(
            [
                (cell.cell_id, cell.index, cell.buy_price, cell.sell_price)
                for cell in rule_cells
            ],
            [
                (cell.cell_id, cell.index, cell.buy_price, cell.sell_price)
                for cell in web_cells
            ],
        )

    def test_long_cell_completes_cycle_and_rearms_with_new_key(self) -> None:
        engine = GridRuleEngine(config())

        (entry,) = engine.initialize(Decimal("105"))
        self.assertEqual(entry.side, GridOrderSide.BUY)
        self.assertEqual(entry.role, GridOrderRole.ENTRY)
        self.assertEqual(entry.price, Decimal("100"))
        self.assertEqual(entry.quantity, Decimal("1"))

        (exit_order,) = engine.on_fills([fill_for(entry, 1)])
        self.assertEqual(exit_order.side, GridOrderSide.SELL)
        self.assertEqual(exit_order.role, GridOrderRole.EXIT)
        self.assertEqual(exit_order.price, Decimal("110"))
        self.assertEqual(engine.cells[0].phase, CellPhase.EXIT_PENDING)

        self.assertEqual(engine.on_fills([fill_for(exit_order, 2)]), ())
        self.assertEqual(engine.completed_cycles, 1)
        self.assertEqual(engine.cells[0].position_quantity, Decimal("0"))

        (next_entry,) = engine.on_market(Decimal("105"))
        self.assertNotEqual(next_entry.order_key, entry.order_key)
        self.assertEqual(next_entry.cycle, 1)

    def test_short_cell_uses_sell_entry_and_buy_exit(self) -> None:
        engine = GridRuleEngine(config(GridMode.SHORT))

        (entry,) = engine.initialize(Decimal("105"))
        self.assertEqual(entry.side, GridOrderSide.SELL)
        self.assertEqual(entry.price, Decimal("110"))

        (exit_order,) = engine.on_fills([fill_for(entry, 1)])
        self.assertEqual(exit_order.side, GridOrderSide.BUY)
        self.assertEqual(exit_order.price, Decimal("100"))

    def test_entry_waits_until_existing_web_trigger_condition_is_met(self) -> None:
        engine = GridRuleEngine(config())

        self.assertEqual(engine.initialize(Decimal("95")), ())
        self.assertEqual(engine.cells[0].phase, CellPhase.DORMANT)

        (entry,) = engine.on_market(Decimal("100"))
        self.assertEqual(entry.price, Decimal("100"))

    def test_partial_fill_is_rejected_by_first_rule_version(self) -> None:
        engine = GridRuleEngine(config())
        (entry,) = engine.initialize(Decimal("105"))
        partial = GridFill(
            order_key=entry.order_key,
            instrument=entry.instrument,
            side=entry.side,
            price=entry.price,
            quantity=entry.quantity / 2,
            sequence=1,
            timestamp=1,
        )

        with self.assertRaisesRegex(ValueError, "partial fills"):
            engine.on_fills([partial])

    def test_coinm_quantity_matches_live_integer_contract_conversion(self) -> None:
        engine = GridRuleEngine(
            GridRuleConfig(
                grid_id="coinm-contracts",
                instrument="BTCUSD_PERP",
                mode=GridMode.LONG,
                anchor_price=Decimal("110000"),
                grid_ratio=Decimal("0.10"),
                grid_count=1,
                order_notional=Decimal("0"),
                tick_size=Decimal("0.1"),
                quantity_step=Decimal("1"),
                market_type=GridMarketType.COINM,
                order_coin_qty=Decimal("0.0021"),
                contract_size=Decimal("100"),
            )
        )

        (entry,) = engine.initialize(Decimal("105000"))

        self.assertEqual(entry.price, Decimal("100000.0"))
        self.assertEqual(entry.quantity, Decimal("2"))

    def test_long_following_window_moves_up_and_retires_old_entries(self) -> None:
        engine = GridRuleEngine(
            GridRuleConfig(
                grid_id="following-long",
                instrument="BTCUSDT",
                mode=GridMode.LONG,
                anchor_price=Decimal("110"),
                grid_ratio=Decimal("0.10"),
                grid_count=2,
                order_notional=Decimal("100"),
                tick_size=Decimal("0.01"),
                quantity_step=Decimal("0.001"),
                move_grid=True,
            )
        )
        original = engine.initialize(Decimal("105"))

        moved = engine.on_market(Decimal("121"))

        self.assertEqual(
            [(cell.buy_price, cell.sell_price) for cell in engine.cells],
            [
                (Decimal("110"), Decimal("121.00")),
                (Decimal("121.00"), Decimal("133.10")),
            ],
        )
        self.assertEqual(
            [intent.price for intent in moved],
            [Decimal("110"), Decimal("121.00")],
        )
        self.assertTrue(
            {intent.order_key for intent in original}.isdisjoint(
                {intent.order_key for intent in moved}
            )
        )
        self.assertEqual(engine.cells_added, 2)
        self.assertEqual(engine.cells_reclaimed, 2)

    def test_following_window_keeps_position_cell_until_exit_completes(self) -> None:
        engine = GridRuleEngine(
            GridRuleConfig(
                grid_id="following-protected",
                instrument="BTCUSDT",
                mode=GridMode.LONG,
                anchor_price=Decimal("110"),
                grid_ratio=Decimal("0.10"),
                grid_count=2,
                order_notional=Decimal("100"),
                tick_size=Decimal("0.01"),
                quantity_step=Decimal("0.001"),
                move_grid=True,
            )
        )
        entries = engine.initialize(Decimal("105"))
        lowest_entry = entries[0]
        after_entry = engine.on_fills([fill_for(lowest_entry, 1)])
        exit_order = next(
            intent
            for intent in after_entry
            if intent.role == GridOrderRole.EXIT
        )

        engine.on_market(Decimal("121"))

        self.assertEqual(len(engine.cells), 4)
        protected = next(
            cell for cell in engine.cells if cell.cell_id == lowest_entry.cell_id
        )
        self.assertEqual(protected.phase, CellPhase.EXIT_PENDING)
        self.assertGreater(protected.position_quantity, 0)
        self.assertEqual(engine.cells_reclaimed, 0)

        engine.on_fills([fill_for(exit_order, 2)])
        engine.on_market(Decimal("121"))

        self.assertEqual(len(engine.cells), 2)
        self.assertNotIn(
            lowest_entry.cell_id,
            {cell.cell_id for cell in engine.cells},
        )
        self.assertEqual(engine.cells_reclaimed, 2)
        self.assertEqual(engine.completed_cycles, 1)

    def test_short_following_window_moves_down(self) -> None:
        engine = GridRuleEngine(
            GridRuleConfig(
                grid_id="following-short",
                instrument="BTCUSDT",
                mode=GridMode.SHORT,
                anchor_price=Decimal("100"),
                grid_ratio=Decimal("0.10"),
                grid_count=2,
                order_notional=Decimal("100"),
                tick_size=Decimal("0.01"),
                quantity_step=Decimal("0.001"),
                move_grid=True,
            )
        )
        engine.initialize(Decimal("105"))

        moved = engine.on_market(Decimal("90"))

        self.assertEqual(
            [(cell.buy_price, cell.sell_price) for cell in engine.cells],
            [
                (Decimal("82.63"), Decimal("90.90")),
                (Decimal("90.90"), Decimal("100")),
            ],
        )
        self.assertEqual(
            [intent.price for intent in moved],
            [Decimal("90.90"), Decimal("100")],
        )
        self.assertEqual(engine.cells_added, 2)
        self.assertEqual(engine.cells_reclaimed, 2)


if __name__ == "__main__":
    unittest.main()
