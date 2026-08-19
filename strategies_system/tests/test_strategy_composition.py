from __future__ import annotations

import unittest
from decimal import Decimal

from strategy_application import (
    CoinMLongTakeProfitLadderStrategy,
    RuleInstanceLifecycle,
    StrategyInstance,
    StrategyInstanceLifecycle,
)
from trading_strategies.btc_accumulation import (
    CoinMLongTakeProfitLadderConfig,
    CoinMLongTakeProfitLadderStrategyDefinition,
    EntrySizingMode,
    PositionPlan,
    StrategyFill,
    StrategyOrderSide,
    StrategyRole,
)
from trading_strategies.catalog import build_strategy_definition_registry
from trading_strategies.entry_then_ladder_exit import (
    EntryThenLadderExitParameters,
    EntryThenLadderExitStrategyDefinition,
)
from trading_strategies.kernel import TradeSide, TradingRuleSpec
from trading_strategies.rules import (
    InitialEntryPhase,
    InitialEntryRule,
    InitialEntryRuleConfig,
    InitialEntryRuleContext,
    InitialEntryStartInput,
    LadderPositionOpenedInput,
    build_ladder_take_profit_levels,
    SizedPosition,
    build_trading_rule_registry,
)
from trading_strategies.strategy import (
    CapitalAllocationSpec,
    InstrumentBinding,
    RulePermission,
    StrategyCoordinationPolicy,
    StrategyDirection,
    StrategyProductType,
    StrategyRuleSlotSpec,
    StrategySpec,
)


class StaticSizer:
    def size_long(self, **_kwargs) -> PositionPlan:
        return self._plan()

    def size_long_by_effective_leverage(self, **_kwargs) -> PositionPlan:
        return self._plan()

    def evaluate_long(self, **_kwargs) -> PositionPlan:
        return self._plan()

    @staticmethod
    def _plan() -> PositionPlan:
        return PositionPlan(
            quantity=Decimal("12"),
            quantity_unit="contracts",
            estimated_liquidation_price=Decimal("20000"),
            initial_margin=Decimal("0.01"),
            maintenance_margin=Decimal("0.001"),
            margin_buffer=Decimal("0.009"),
            model_version="test/v1",
        )


class RuleSizer:
    def size_entry(self, *, reference_price: Decimal) -> SizedPosition:
        del reference_price
        return SizedPosition(Decimal("12"), "contracts")


def ladder_config(
    *,
    strategy_id: str = "ladder",
    end_price: Decimal = Decimal("120000"),
) -> CoinMLongTakeProfitLadderConfig:
    return CoinMLongTakeProfitLadderConfig(
        strategy_id=strategy_id,
        instrument="BTCUSD_PERP",
        entry_sizing_mode=EntrySizingMode.EFFECTIVE_LEVERAGE,
        entry_effective_leverage=Decimal("1.5"),
        first_take_profit_ratio=Decimal("1.1"),
        take_profit_end_price=end_price,
        take_profit_count=3,
        tick_size=Decimal("0.1"),
        quantity_step=Decimal("1"),
    )


class StrategyCompositionTests(unittest.TestCase):
    def test_strategy_catalog_is_independent_of_experiment_storage(self) -> None:
        registry = build_strategy_definition_registry()
        descriptor = registry.definitions[0]

        self.assertEqual(
            registry.strategy_types,
            ("entry-then-ladder-exit/v1",),
        )
        self.assertIsInstance(
            registry.get("initial-entry-ladder-take-profit/v1"),
            EntryThenLadderExitStrategyDefinition,
        )
        self.assertEqual(descriptor.to_document()["kind"], "strategy-definition")
        self.assertEqual(descriptor.display_name, "初始建仓&阶梯止盈")
        self.assertEqual(
            [item.rule_type for item in descriptor.rule_composition],
            ["initial-entry/v1", "ladder-take-profit/v1"],
        )

    def test_generic_ladder_strategy_binds_short_linear_direction(self) -> None:
        spec = EntryThenLadderExitStrategyDefinition().bind(
            EntryThenLadderExitParameters(
                strategy_id="eth-short",
                instrument="ETHUSDT",
                direction=StrategyDirection.SHORT,
                product_type=StrategyProductType.LINEAR_PERPETUAL,
                entry_sizing_policy="FIXED_CONTRACTS",
                entry_sizing_parameters={"contracts": "12"},
                first_exit_ratio=Decimal("1.05"),
                exit_end_price=Decimal("2000"),
                exit_level_count=4,
                tick_size=Decimal("0.01"),
                quantity_step=Decimal("0.001"),
                quantity_unit="contracts",
            )
        )

        self.assertEqual(spec.strategy_type, "entry-then-ladder-exit/v1")
        self.assertEqual(spec.display_name, "初始建仓&阶梯止盈")
        self.assertEqual(spec.binding.direction, StrategyDirection.SHORT)
        self.assertEqual(spec.rule_slots[0].rule.config.side, TradeSide.SELL)
        self.assertEqual(spec.rule_slots[1].rule.config.side, TradeSide.BUY)
        levels = build_ladder_take_profit_levels(
            spec.rule_slots[1].rule.config,
            LadderPositionOpenedInput(
                entry_price=Decimal("2500"),
                position_quantity=Decimal("12"),
            ),
        )
        self.assertEqual(len(levels), 4)
        self.assertTrue(
            all(
                right.target_price < left.target_price
                for left, right in zip(levels, levels[1:])
            )
        )

    def test_capital_allocation_is_expressed_in_settlement_asset(self) -> None:
        allocation = CapitalAllocationSpec("btc", Decimal("1.1"))

        self.assertEqual(allocation.settlement_asset, "BTC")
        self.assertEqual(allocation.amount, Decimal("1.1"))

    def test_definition_resolves_two_rule_slots_and_coordination(self) -> None:
        spec = CoinMLongTakeProfitLadderStrategyDefinition().bind(
            ladder_config()
        )

        self.assertEqual(
            [slot.rule_key for slot in spec.rule_slots],
            ["entry", "take-profit"],
        )
        self.assertEqual(
            spec.rule_slots[0].rule.rule_type,
            "initial-entry/v1",
        )
        self.assertEqual(
            spec.rule_slots[1].rule.rule_type,
            "ladder-take-profit/v1",
        )
        self.assertEqual(
            spec.coordination_policy.exit_controller_rule_key,
            "take-profit",
        )
        self.assertEqual(
            spec.coordination_policy.to_document(),
            {
                "proposal_batch": "ATOMIC_PER_EVENT",
                "conflict_resolution": "FAIL_FAST",
                "exit_controller_rule_key": "take-profit",
            },
        )
        self.assertEqual(
            spec.binding.product_type,
            StrategyProductType.INVERSE_PERPETUAL,
        )

    def test_instance_qualifies_exact_contract_intent_identity(self) -> None:
        strategy = CoinMLongTakeProfitLadderStrategy(
            ladder_config(),
            StaticSizer(),
        )
        strategy.initialize()
        plan = strategy.plan_entry(Decimal("60000"))
        intent = strategy.strategy_instance.active_intents[0]

        self.assertEqual(plan.intent_key, "ladder:entry:1")
        self.assertEqual(intent.quantity, Decimal("12"))
        self.assertEqual(intent.quantity_unit, "contracts")
        self.assertEqual(intent.tags["strategy_instance_id"], "ladder")
        self.assertEqual(intent.tags["rule_key"], "entry")
        self.assertEqual(intent.tags["rule_type"], "initial-entry/v1")
        self.assertEqual(
            intent.tags["rule_instance_id"],
            "ladder:entry",
        )

    def test_entry_fill_activates_ladder_and_routes_fills_by_owner(self) -> None:
        strategy = CoinMLongTakeProfitLadderStrategy(
            ladder_config(),
            StaticSizer(),
        )
        strategy.initialize()
        plan = strategy.plan_entry(Decimal("60000"))
        strategy.on_fill(
            StrategyFill(
                fill_id="entry-fill",
                intent_key=plan.intent_key,
                role=StrategyRole.ENTRY,
                side=StrategyOrderSide.BUY,
                price=Decimal("61000"),
                quantity=Decimal("12"),
            ),
            actual_position_plan=plan.position,
        )

        self.assertEqual(len(strategy.take_profit_levels), 3)
        self.assertEqual(len(strategy.strategy_instance.active_intents), 3)
        self.assertEqual(strategy.strategy_instance.event_sequence, 2)
        self.assertEqual(
            strategy.strategy_instance.rule_instances["entry"].lifecycle,
            RuleInstanceLifecycle.COMPLETED,
        )
        self.assertEqual(
            strategy.strategy_instance.rule_instances[
                "entry"
            ].last_event_sequence,
            2,
        )
        first = strategy.visible_take_profit_levels[0]
        owner = strategy.strategy_instance.owner_for_intent(first.intent_key)
        self.assertEqual(owner.rule_key, "take-profit")
        self.assertEqual(owner.local_intent_key, "1")

        strategy.on_fill(
            StrategyFill(
                fill_id="exit-fill",
                intent_key=first.intent_key,
                role=StrategyRole.TAKE_PROFIT,
                side=StrategyOrderSide.SELL,
                price=first.target_price,
                quantity=first.quantity,
            )
        )
        self.assertEqual(len(strategy.visible_take_profit_levels), 2)

    def test_multi_rule_event_is_atomic_when_ladder_rejects(self) -> None:
        strategy = CoinMLongTakeProfitLadderStrategy(
            ladder_config(end_price=Decimal("65000")),
            StaticSizer(),
        )
        strategy.initialize()
        plan = strategy.plan_entry(Decimal("60000"))
        before_entry = strategy.strategy_instance.rule_state("entry")
        before_ladder = strategy.strategy_instance.rule_state("take-profit")

        with self.assertRaisesRegex(ValueError, "end_price"):
            strategy.on_fill(
                StrategyFill(
                    fill_id="entry-fill",
                    intent_key=plan.intent_key,
                    role=StrategyRole.ENTRY,
                    side=StrategyOrderSide.BUY,
                    price=Decimal("61000"),
                    quantity=Decimal("12"),
                ),
                actual_position_plan=plan.position,
            )

        self.assertIs(
            strategy.strategy_instance.rule_state("entry"),
            before_entry,
        )
        self.assertIs(
            strategy.strategy_instance.rule_state("take-profit"),
            before_ladder,
        )
        self.assertEqual(len(strategy.strategy_instance.active_intents), 1)
        self.assertEqual(strategy.strategy_instance.event_sequence, 1)
        self.assertEqual(
            strategy.strategy_instance.lifecycle,
            StrategyInstanceLifecycle.ACTIVE,
        )

    def test_opposing_open_intent_fails_before_state_commit(self) -> None:
        slot = StrategyRuleSlotSpec(
            rule_key="entry",
            rule=TradingRuleSpec(
                rule_type=InitialEntryRule.rule_type,
                config=InitialEntryRuleConfig(
                    intent_key="1",
                    side=TradeSide.SELL,
                ),
            ),
            permissions=(RulePermission.OPEN,),
            subscriptions=(
                "InitialEntryStartInput",
                "InitialEntryFillInput",
            ),
        )
        spec = StrategySpec(
            strategy_spec_id="bad-long:spec",
            strategy_type="test/v1",
            display_name="Bad Long",
            parameters={},
            binding=InstrumentBinding(
                "BTCUSD_PERP",
                StrategyDirection.LONG,
                StrategyProductType.INVERSE_PERPETUAL,
            ),
            rule_slots=(slot,),
            coordination_policy=StrategyCoordinationPolicy(),
        )
        instance = StrategyInstance(
            strategy_instance_id="bad-long",
            spec=spec,
            rules={"entry": InitialEntryRule()},
            contexts={
                "entry": InitialEntryRuleContext(RuleSizer())
            },
        )
        instance.start()

        with self.assertRaisesRegex(ValueError, "direction"):
            instance.apply_event(
                {
                    "entry": InitialEntryStartInput(
                        reference_price=Decimal("60000")
                    )
                }
            )

        state = instance.rule_state("entry")
        self.assertEqual(state.phase, InitialEntryPhase.WAITING_START)
        self.assertEqual(instance.active_intents, ())

    def test_rule_catalog_is_independent_of_experiment_storage(self) -> None:
        registry = build_trading_rule_registry()

        self.assertEqual(
            registry.rule_types,
            ("initial-entry/v1", "ladder-take-profit/v1"),
        )
        self.assertEqual(
            {definition.display_name for definition in registry.definitions},
            {"初始建仓", "阶梯止盈"},
        )

    def test_strategy_spec_enforces_a_single_exit_controller(self) -> None:
        config = InitialEntryRuleConfig("1", TradeSide.BUY)
        with self.assertRaisesRegex(ValueError, "exactly the exit controller"):
            StrategySpec(
                strategy_spec_id="invalid:spec",
                strategy_type="invalid/v1",
                display_name="Invalid",
                parameters={},
                binding=InstrumentBinding(
                    "BTCUSD_PERP",
                    StrategyDirection.LONG,
                    StrategyProductType.INVERSE_PERPETUAL,
                ),
                rule_slots=(
                    StrategyRuleSlotSpec(
                        rule_key="entry",
                        rule=TradingRuleSpec(
                            InitialEntryRule.rule_type,
                            config,
                        ),
                        permissions=(),
                        subscriptions=("InitialEntryStartInput",),
                    ),
                ),
                coordination_policy=StrategyCoordinationPolicy(
                    exit_controller_rule_key="entry"
                ),
            )


if __name__ == "__main__":
    unittest.main()
