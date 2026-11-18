"""A fixed grid Strategy binds and runs its registered grid Rule."""

from __future__ import annotations

import unittest
from dataclasses import replace
from decimal import Decimal

import strategy_simulation  # noqa: F401 - activates sibling packages

from grid_rule.fixed_equal_usdt import (
    FixedEntryMode,
    build_fixed_cells,
    initial_sell_allocations,
)
from strategy_application import StrategyInstance
from trading_strategies.catalog import build_strategy_definition_registry
from trading_strategies.fixed_equal_usdt_grid_strategy import (
    FIXED_EQUAL_USDT_GRID_STRATEGY_V1,
    FixedEqualUsdtGridStrategyDefinition,
    FixedEqualUsdtGridStrategyParameters,
)
from trading_strategies.kernel import PositionEffect, TradeSide
from trading_strategies.rules import (
    FixedEqualUsdtGridRule,
    FixedGridObservationInput,
    FixedGridObservedCell,
)
from trading_strategies.strategy import CapitalAllocationSpec, StrategyCoordinationPolicy


class FixedEqualUsdtGridStrategyTests(unittest.TestCase):
    @staticmethod
    def parameters(
        mode: FixedEntryMode = FixedEntryMode.SEEDED,
    ) -> FixedEqualUsdtGridStrategyParameters:
        return FixedEqualUsdtGridStrategyParameters(
            strategy_id="soxl-simulation",
            instrument="SOXLUSDT",
            lower_price=Decimal("40"),
            upper_price=Decimal("60"),
            grid_count=4,
            grid_budget_usdt=Decimal("200"),
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.01"),
            min_qty=Decimal("0.01"),
            min_notional=Decimal("5"),
            entry_mode=mode,
            orders_per_side=4,
            rebalance_below=1,
            rebalance_above=6,
        )

    @staticmethod
    def instance(parameters: FixedEqualUsdtGridStrategyParameters) -> StrategyInstance:
        spec = FixedEqualUsdtGridStrategyDefinition().bind(parameters)
        instance = StrategyInstance(
            strategy_instance_id=parameters.strategy_id,
            spec=spec,
            rules={"grid": FixedEqualUsdtGridRule()},
            contexts={"grid": None},
        )
        instance.start()
        return instance

    def test_catalog_and_binding_compose_grid_rule(self) -> None:
        definition = build_strategy_definition_registry().get(
            FIXED_EQUAL_USDT_GRID_STRATEGY_V1
        )
        spec = definition.bind(self.parameters())
        self.assertEqual(spec.strategy_type, FIXED_EQUAL_USDT_GRID_STRATEGY_V1)
        self.assertEqual(len(spec.rule_slots), 1)
        self.assertEqual(spec.rule_slots[0].rule.rule_type, "fixed-equal-usdt-grid/v1")
        self.assertEqual(spec.capital_allocation, CapitalAllocationSpec("USDT", Decimal("200")))
        self.assertTrue(spec.coordination_policy.allow_simultaneous_entry_exit)
        self.assertTrue(
            definition.definition().to_document()["coordination_policy"]
            ["allow_simultaneous_entry_exit"]
        )

    def test_seeded_strategy_routes_buy_and_sell_in_one_event(self) -> None:
        instance = self.instance(self.parameters())
        config = instance.spec.rule_slots[0].rule.config
        cells = build_fixed_cells(config.spec)
        allocations = initial_sell_allocations(
            cells, Decimal("50"), step_size=config.step_size,
            min_qty=config.min_qty, min_notional=config.min_notional,
        )
        observation = FixedGridObservationInput(
            Decimal("50"),
            tuple(
                FixedGridObservedCell(
                    cell.cell_id,
                    "pending_exit" if cell.cell_id in allocations else "pending_entry",
                    open_qty=allocations.get(cell.cell_id, Decimal("0")),
                )
                for cell in cells
            ),
        )
        event = instance.apply_event({"grid": observation})
        self.assertEqual(event.event_sequence, 1)
        self.assertEqual({intent.side for intent in event.intents}, {TradeSide.BUY, TradeSide.SELL})
        self.assertEqual(len(instance.active_intents), len(event.intents))
        self.assertTrue(all(intent.tags["rule_key"] == "grid" for intent in event.intents))
        self.assertEqual(
            {intent.position_effect for intent in event.intents},
            {PositionEffect.INCREASE, PositionEffect.REDUCE},
        )
        self.assertTrue(all(
            intent.reduce_only
            for intent in event.intents if intent.side == TradeSide.SELL
        ))

        working = FixedGridObservationInput(
            Decimal("50"),
            tuple(
                replace(
                    cell,
                    has_entry_order=any(
                        intent.tags["cell_id"] == cell.cell_id and intent.side == TradeSide.BUY
                        for intent in event.intents
                    ),
                    has_exit_order=any(
                        intent.tags["cell_id"] == cell.cell_id and intent.side == TradeSide.SELL
                        for intent in event.intents
                    ),
                )
                for cell in observation.cells
            ),
        )
        repeated = instance.apply_event({"grid": working})
        self.assertEqual(repeated.intents, ())
        self.assertEqual(repeated.cancellations, ())

    def test_triggered_strategy_starts_without_seed_sells(self) -> None:
        instance = self.instance(self.parameters(FixedEntryMode.PRICE_TRIGGERED))
        cells = build_fixed_cells(instance.spec.rule_slots[0].rule.config.spec)
        event = instance.apply_event({"grid": FixedGridObservationInput(
            Decimal("50"),
            tuple(FixedGridObservedCell(cell.cell_id, "untriggered") for cell in cells),
        )})
        self.assertTrue(event.intents)
        self.assertTrue(all(intent.side == TradeSide.BUY for intent in event.intents))

    def test_other_strategies_still_reject_mixed_entry_exit(self) -> None:
        parameters = self.parameters()
        spec = FixedEqualUsdtGridStrategyDefinition().bind(parameters)
        spec = replace(
            spec,
            coordination_policy=StrategyCoordinationPolicy(exit_controller_rule_key="grid"),
        )
        instance = StrategyInstance(
            strategy_instance_id=parameters.strategy_id,
            spec=spec,
            rules={"grid": FixedEqualUsdtGridRule()},
            contexts={"grid": None},
        )
        instance.start()
        config = spec.rule_slots[0].rule.config
        cells = build_fixed_cells(config.spec)
        allocations = initial_sell_allocations(
            cells, Decimal("50"), step_size=config.step_size,
            min_qty=config.min_qty, min_notional=config.min_notional,
        )
        observation = FixedGridObservationInput(
            Decimal("50"),
            tuple(FixedGridObservedCell(
                cell.cell_id,
                "pending_exit" if cell.cell_id in allocations else "pending_entry",
                open_qty=allocations.get(cell.cell_id, Decimal("0")),
            ) for cell in cells),
        )
        with self.assertRaisesRegex(ValueError, "cannot both increase and reduce"):
            instance.apply_event({"grid": observation})
        self.assertEqual(instance.event_sequence, 0)

    def test_budget_must_fit_allocation(self) -> None:
        with self.assertRaisesRegex(ValueError, "allocation must cover"):
            replace(
                self.parameters(),
                capital_allocation=CapitalAllocationSpec("USDT", Decimal("199")),
            )


if __name__ == "__main__":
    unittest.main()
