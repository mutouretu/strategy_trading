"""The registered fixed grid Rule plans orders from confirmed Cell facts."""

from __future__ import annotations

import unittest
from decimal import Decimal

import strategy_simulation  # noqa: F401 - activates sibling packages

from grid_rule.fixed_equal_usdt import (
    FixedEntryMode, FixedGridSpec, build_fixed_cells, initial_sell_allocations,
)
from grid_server.domain import OrderSide, OrderStatus, SymbolFilters
from grid_server.simulation import FixedGridReplay
from market_simulator import FixedBarMarketSource
from trading_strategies.kernel import TradeSide
from trading_strategies.rules import (
    FixedEqualUsdtGridRule,
    FixedEqualUsdtGridRuleConfig,
    FixedGridObservationInput,
    FixedGridObservedCell,
    build_trading_rule_registry,
)


class FixedEqualUsdtGridRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = FixedGridSpec(
            "grid-rule-test", Decimal("40"), Decimal("60"), 4,
            Decimal("200"), Decimal("0.01"),
        )
        self.cells = build_fixed_cells(self.spec)
        self.rule = FixedEqualUsdtGridRule()

    def config(self, mode: FixedEntryMode) -> FixedEqualUsdtGridRuleConfig:
        return FixedEqualUsdtGridRuleConfig(
            self.spec, mode, Decimal("0.01"), Decimal("0.01"),
            Decimal("5"), orders_per_side=4, rebalance_below=1,
            rebalance_above=6,
        )

    def test_triggered_rule_arms_buys_then_pairs_partial_position_exit(self) -> None:
        config = self.config(FixedEntryMode.PRICE_TRIGGERED)
        state = self.rule.initial_state(config, None)
        initial = self.rule.transition(
            config, state,
            FixedGridObservationInput(
                Decimal("50"),
                tuple(FixedGridObservedCell(cell.cell_id, "untriggered")
                      for cell in self.cells),
            ),
            None,
        )
        self.assertTrue(initial.intents)
        self.assertTrue(all(intent.side == TradeSide.BUY for intent in initial.intents))
        self.assertFalse(initial.cancellations)
        bought = initial.intents[-1]
        partial_qty = bought.quantity / 2
        bought_id = bought.tags["cell_id"]
        paired = self.rule.transition(
            config, initial.next_state,
            FixedGridObservationInput(
                Decimal("50.50"),
                tuple(
                    FixedGridObservedCell(
                        cell.cell_id,
                        "pending_exit" if cell.cell_id == bought_id
                        else ("pending_entry" if cell.cell_id in initial.next_state.armed_ids
                              else "untriggered"),
                        open_qty=(partial_qty if cell.cell_id == bought_id
                                  else Decimal("0")),
                        has_entry_order=(cell.cell_id != bought_id
                                         and cell.cell_id in initial.next_state.armed_ids),
                    )
                    for cell in self.cells
                ),
            ),
            None,
        )
        exits = [intent for intent in paired.intents if intent.side == TradeSide.SELL]
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0].quantity, partial_qty)
        self.assertTrue(exits[0].reduce_only)
        self.assertEqual(exits[0].tags["cell_id"], bought_id)

        after_sale = self.rule.transition(
            config, paired.next_state,
            FixedGridObservationInput(
                Decimal("55"),
                tuple(
                    FixedGridObservedCell(
                        cell.cell_id,
                        "untriggered" if cell.cell_id == bought_id
                        else ("pending_entry" if cell.cell_id in initial.next_state.armed_ids
                              else "untriggered"),
                        cycle_count=1 if cell.cell_id == bought_id else 0,
                        has_entry_order=(cell.cell_id != bought_id
                                         and cell.cell_id in initial.next_state.armed_ids),
                    )
                    for cell in self.cells
                ),
            ),
            None,
        )
        self.assertTrue(any(
            intent.tags["cell_id"] == bought_id
            and ":cycle:1:entry" in intent.intent_key
            for intent in after_sale.intents
        ))

    def test_seeded_rule_uses_confirmed_seed_allocations(self) -> None:
        config = self.config(FixedEntryMode.SEEDED)
        allocations = initial_sell_allocations(
            self.cells, Decimal("50"), step_size=config.step_size,
            min_qty=config.min_qty, min_notional=config.min_notional,
        )
        transition = self.rule.transition(
            config, self.rule.initial_state(config, None),
            FixedGridObservationInput(
                Decimal("50"),
                tuple(
                    FixedGridObservedCell(
                        cell.cell_id,
                        "pending_exit" if cell.cell_id in allocations
                        else "pending_entry",
                        open_qty=allocations.get(cell.cell_id, Decimal("0")),
                    )
                    for cell in self.cells
                ),
            ),
            None,
        )
        self.assertTrue(any(intent.side == TradeSide.BUY for intent in transition.intents))
        sell_quantities = {
            intent.tags["cell_id"]: intent.quantity
            for intent in transition.intents if intent.side == TradeSide.SELL
        }
        self.assertEqual(sell_quantities, allocations)

    def test_catalog_exposes_real_rule_definition(self) -> None:
        rule = build_trading_rule_registry().get(FixedEqualUsdtGridRule.rule_type)
        self.assertIsInstance(rule, FixedEqualUsdtGridRule)
        definition = rule.definition().to_document()
        self.assertEqual(definition["family"], "grid")
        self.assertEqual(definition["supported_product_types"], ["LINEAR_PERPETUAL"])
        self.assertIn("FixedGridObservationInput", definition["input_types"])

    def test_triggered_rule_first_window_matches_live_engine_replay(self) -> None:
        spec = FixedGridSpec(
            "grid-rule-parity", Decimal("40"), Decimal("60"), 135,
            Decimal("2000"), Decimal("0.01"),
        )
        config = FixedEqualUsdtGridRuleConfig(
            spec, FixedEntryMode.PRICE_TRIGGERED,
            Decimal("0.01"), Decimal("0.01"), Decimal("5"),
        )
        transition = self.rule.transition(
            config, self.rule.initial_state(config, None),
            FixedGridObservationInput(
                Decimal("50.45"),
                tuple(FixedGridObservedCell(cell.cell_id, "untriggered")
                      for cell in build_fixed_cells(spec)),
            ),
            None,
        )
        source = FixedBarMarketSource("SOXLUSDT", [
            ("50.45", "50.45", "50.45", "50.45"),
        ])
        replay = FixedGridReplay(
            source, symbol="SOXLUSDT",
            lower_price=spec.lower_price, upper_price=spec.upper_price,
            grid_count=spec.grid_count, grid_budget_usdt=spec.grid_budget_usdt,
            filters=SymbolFilters(
                tick_size=Decimal("0.01"), step_size=Decimal("0.01"),
                min_qty=Decimal("0.01"), min_notional=Decimal("5"),
                contract_type="TRADIFI_PERPETUAL",
                percent_price_up=Decimal("1.03"),
                percent_price_down=Decimal("0.97"),
            ),
            entry_mode=FixedEntryMode.PRICE_TRIGGERED,
        ).run(max_frames=1)
        planned = sorted(
            (intent.target_price, intent.quantity)
            for intent in transition.intents if intent.side == TradeSide.BUY
        )
        actual = sorted(
            (order.price, order.quantity)
            for order in replay.orders
            if order.side == OrderSide.BUY and order.status == OrderStatus.NEW
        )
        self.assertEqual(planned, actual)
        self.assertEqual(len(planned), 10)


if __name__ == "__main__":
    unittest.main()
