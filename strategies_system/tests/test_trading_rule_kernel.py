from __future__ import annotations

import ast
import json
import sys
import unittest
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import trading_strategies.kernel as kernel
from trading_strategies.kernel import (
    PositionEffect,
    RuleConfigFieldDefinition,
    RuleIntentMode,
    RuleIntentProposal,
    RuleLifecycleRequest,
    RuleTransition,
    TradeSide,
    TradingEvent,
    TradingFillEvent,
    TradingMarketEvent,
    TradingPositionChangedEvent,
    TradingRule,
    TradingRuleDefinition,
    TradingRuleRegistry,
    TradingRuleSpec,
    TradingSignalEvent,
    TradingStartEvent,
)


@dataclass(frozen=True, slots=True)
class ExampleRuleConfig:
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class ExampleRuleState:
    started: bool = False


@dataclass(frozen=True, slots=True)
class ExampleRuleContext:
    reference_price: Decimal


class ExampleRule(
    TradingRule[
        ExampleRuleConfig,
        TradingEvent,
        ExampleRuleState,
        ExampleRuleContext,
    ]
):
    rule_type = "example-rule/v1"

    @classmethod
    def definition(cls) -> TradingRuleDefinition:
        return TradingRuleDefinition(
            rule_type=cls.rule_type,
            display_name="Example Rule",
            version="v1",
            summary="A test-only stateless trading rule.",
            input_types=("TradingStartEvent",),
            config_fields=(
                RuleConfigFieldDefinition(
                    key="quantity",
                    name="Quantity",
                    required=True,
                ),
            ),
            supported_directions=("LONG", "SHORT"),
            supported_product_types=("SPOT",),
            lifecycle=("START", "COMPLETE"),
            formulae=("(s[t+1], y[t]) = T_rule(s[t], x[t]; theta)",),
            aliases=("legacy-example-rule/v1",),
        )

    def initial_state(
        self,
        rule_config: ExampleRuleConfig,
        context: ExampleRuleContext,
    ) -> ExampleRuleState:
        del rule_config, context
        return ExampleRuleState()

    def transition(
        self,
        rule_config: ExampleRuleConfig,
        state: ExampleRuleState,
        input: TradingEvent,
        context: ExampleRuleContext,
    ) -> RuleTransition[ExampleRuleState]:
        del state
        if isinstance(input, TradingStartEvent):
            return RuleTransition(
                next_state=ExampleRuleState(started=True),
                intents=(
                    RuleIntentProposal(
                        intent_key="entry-1",
                        side=TradeSide.BUY,
                        quantity=rule_config.quantity,
                        quantity_unit="contracts",
                        position_effect=PositionEffect.OPEN,
                        intent_mode=RuleIntentMode.ACTIVE,
                        target_price=context.reference_price,
                        role="entry",
                    ),
                ),
            )
        return RuleTransition.unchanged(ExampleRuleState(started=True))


class MismatchedDefinitionRule(ExampleRule):
    rule_type = "mismatched/v1"

    @classmethod
    def definition(cls) -> TradingRuleDefinition:
        return TradingRuleDefinition(
            rule_type="different/v1",
            display_name="Mismatch",
            version="v1",
            summary="Used to verify registry validation.",
            input_types=("TradingStartEvent",),
        )


class IncompleteRule(TradingRule):
    pass


class TradingRuleKernelTests(unittest.TestCase):
    def test_abstract_rule_cannot_be_instantiated(self) -> None:
        with self.assertRaises(TypeError):
            IncompleteRule()

    def test_rule_produces_an_explicit_transition(self) -> None:
        rule = ExampleRule()
        config = ExampleRuleConfig(quantity=Decimal("2"))
        context = ExampleRuleContext(reference_price=Decimal("60000"))
        state = rule.initial_state(config, context)

        transition = rule.transition(
            config,
            state,
            TradingStartEvent("start-1", 0, 0),
            context,
        )

        self.assertTrue(transition.next_state.started)
        self.assertEqual(len(transition.intents), 1)
        self.assertEqual(transition.intents[0].quantity, Decimal("2"))
        self.assertEqual(
            transition.lifecycle_request,
            RuleLifecycleRequest.NONE,
        )
        self.assertFalse(vars(rule))

    def test_definition_declares_rule_config_and_input_contracts(self) -> None:
        definition = ExampleRule.definition()

        self.assertEqual(definition.input_types, ("TradingStartEvent",))
        self.assertEqual(definition.output_types, ("RULE_TRANSITION",))
        self.assertEqual(definition.config_fields[0].key, "quantity")

        with self.assertRaisesRegex(ValueError, "at least one rule input"):
            TradingRuleDefinition(
                rule_type="missing-input/v1",
                display_name="Missing Input",
                version="v1",
                summary="Invalid definition.",
                input_types=(),
            )

    def test_definition_document_contains_only_plain_json_values(self) -> None:
        definition = TradingRuleDefinition(
            rule_type="json-rule/v1",
            display_name="JSON Rule",
            version="v1",
            summary="Verifies catalog serialization.",
            input_types=("StartInput",),
            config_fields=(
                RuleConfigFieldDefinition(
                    key="thresholds",
                    name="Thresholds",
                    default={"low": Decimal("0.10"), "high": (1, 2)},
                ),
            ),
        )

        document = definition.to_document()

        self.assertEqual(
            document["config_fields"][0]["default"],
            {"low": "0.10", "high": [1, 2]},
        )
        json.dumps(document)

    def test_registry_resolves_canonical_types_and_aliases(self) -> None:
        registry = TradingRuleRegistry()
        rule = ExampleRule()
        registry.register(rule)

        self.assertIs(registry.get("example-rule/v1"), rule)
        self.assertIs(registry.get("legacy-example-rule/v1"), rule)
        self.assertEqual(
            registry.canonical_type("legacy-example-rule/v1"),
            "example-rule/v1",
        )
        self.assertEqual(registry.rule_types, ("example-rule/v1",))
        self.assertEqual(
            registry.definitions[0].display_name,
            "Example Rule",
        )

        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register(ExampleRule())
        with self.assertRaisesRegex(ValueError, "not registered"):
            registry.get("missing/v1")

    def test_registry_rejects_invalid_rule_objects_and_metadata(self) -> None:
        registry = TradingRuleRegistry()
        with self.assertRaises(TypeError):
            registry.register(object())  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "must match"):
            registry.register(MismatchedDefinitionRule())

    def test_rule_spec_detaches_and_freezes_resolved_config(self) -> None:
        raw_config = {
            "levels": [1, 2, 3],
            "schedule": {"kind": "GEOMETRIC"},
        }
        raw_references = {"sizing": {"id": "effective-leverage/v1"}}
        spec = TradingRuleSpec(
            rule_type="ladder-take-profit/v1",
            config=raw_config,
            capability_references=raw_references,
        )

        raw_config["levels"].append(4)
        raw_config["schedule"]["kind"] = "LINEAR"
        raw_references["sizing"]["id"] = "changed/v1"

        self.assertEqual(spec.config["levels"], (1, 2, 3))
        self.assertEqual(spec.config["schedule"]["kind"], "GEOMETRIC")
        self.assertEqual(
            spec.capability_references["sizing"]["id"],
            "effective-leverage/v1",
        )
        with self.assertRaises(TypeError):
            spec.config["new"] = 1  # type: ignore[index]

    def test_intent_contract_enforces_price_and_position_effect(self) -> None:
        passive_exit = RuleIntentProposal(
            intent_key="exit-1",
            side=TradeSide.SELL,
            quantity=Decimal("1"),
            quantity_unit="contracts",
            position_effect=PositionEffect.REDUCE,
            intent_mode=RuleIntentMode.PASSIVE,
            target_price=Decimal("70000"),
            reduce_only=True,
            role="take_profit",
        )
        self.assertEqual(passive_exit.target_price, Decimal("70000"))
        self.assertEqual(passive_exit.quantity_unit, "contracts")

        with self.assertRaisesRegex(ValueError, "quantity_unit"):
            RuleIntentProposal(
                intent_key="missing-unit",
                side=TradeSide.BUY,
                quantity=Decimal("1"),
                quantity_unit=" ",
                position_effect=PositionEffect.OPEN,
                intent_mode=RuleIntentMode.ACTIVE,
            )

        with self.assertRaisesRegex(ValueError, "requires target_price"):
            RuleIntentProposal(
                intent_key="missing-price",
                side=TradeSide.SELL,
                quantity=Decimal("1"),
                quantity_unit="contracts",
                position_effect=PositionEffect.REDUCE,
                intent_mode=RuleIntentMode.PASSIVE,
                reduce_only=True,
            )
        with self.assertRaisesRegex(ValueError, "must be reduce_only"):
            RuleIntentProposal(
                intent_key="unsafe-exit",
                side=TradeSide.SELL,
                quantity=Decimal("1"),
                quantity_unit="contracts",
                position_effect=PositionEffect.CLOSE,
                intent_mode=RuleIntentMode.ACTIVE,
            )

    def test_transition_rejects_ambiguous_intent_deltas(self) -> None:
        intent = RuleIntentProposal(
            intent_key="entry-1",
            side=TradeSide.BUY,
            quantity=Decimal("1"),
            quantity_unit="contracts",
            position_effect=PositionEffect.OPEN,
            intent_mode=RuleIntentMode.ACTIVE,
        )
        with self.assertRaisesRegex(ValueError, "must be unique"):
            RuleTransition(
                next_state=ExampleRuleState(),
                intents=(intent, intent),
            )
        with self.assertRaisesRegex(ValueError, "issue and cancel"):
            RuleTransition(
                next_state=ExampleRuleState(),
                intents=(intent,),
                cancellations=(intent.intent_key,),
            )

    def test_trading_event_contracts_are_typed_and_immutable(self) -> None:
        market = TradingMarketEvent(
            event_id="market-1",
            sequence=1,
            timestamp=1_000,
            instrument="BTCUSD_PERP",
            open=Decimal("60000"),
            high=Decimal("62000"),
            low=Decimal("59000"),
            close=Decimal("61000"),
        )
        self.assertEqual(market.open, Decimal("60000"))

        signal_values = {"rsi": "29.5"}
        signal = TradingSignalEvent(
            event_id="signal-1",
            sequence=2,
            timestamp=2_000,
            signal_type="RSI_OVERSOLD",
            values=signal_values,
        )
        signal_values["rsi"] = "90"
        self.assertEqual(signal.values["rsi"], "29.5")

        fill = TradingFillEvent(
            event_id="fill-event-1",
            sequence=3,
            timestamp=3_000,
            fill_id="fill-1",
            source_intent_key="entry-1",
            side=TradeSide.BUY,
            price=Decimal("60500"),
            quantity=Decimal("2"),
        )
        self.assertEqual(fill.quantity, Decimal("2"))

        flat = TradingPositionChangedEvent(
            event_id="position-1",
            sequence=4,
            timestamp=4_000,
            position_owner_id="owner-1",
            quantity=Decimal("0"),
            average_entry_price=None,
            reason="CLOSED",
        )
        self.assertEqual(flat.quantity, Decimal("0"))

        with self.assertRaisesRegex(ValueError, "low must not exceed"):
            TradingMarketEvent(
                event_id="invalid-market",
                sequence=5,
                timestamp=5_000,
                instrument="BTCUSD_PERP",
                open=Decimal("60000"),
                high=Decimal("62000"),
                low=Decimal("61000"),
                close=Decimal("60500"),
            )

    def test_public_kernel_exports_only_rule_domain_contracts(self) -> None:
        self.assertEqual(
            set(kernel.__all__),
            {
                "PositionEffect",
                "RuleConfigFieldDefinition",
                "RuleIntentMode",
                "RuleIntentProposal",
                "RuleLifecycleRequest",
                "RuleTransition",
                "TradeSide",
                "TradingEvent",
                "TradingEventKind",
                "TradingFillEvent",
                "TradingMarketEvent",
                "TradingPositionChangedEvent",
                "TradingRule",
                "TradingRuleDefinition",
                "TradingRuleRegistry",
                "TradingRuleSpec",
                "TradingSignalEvent",
                "TradingStartEvent",
                "TradingStopEvent",
            },
        )

    def test_kernel_imports_only_python_standard_library(self) -> None:
        root = (
            Path(__file__).parents[1]
            / "src"
            / "trading_strategies"
            / "kernel"
        )
        violations: list[str] = []
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level > 0 or node.module is None:
                        continue
                    names = [node.module]
                for name in names:
                    if name.split(".")[0] not in sys.stdlib_module_names:
                        violations.append(f"{path.name}:{name}")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
