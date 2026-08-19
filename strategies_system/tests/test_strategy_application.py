from __future__ import annotations

import ast
import unittest
from decimal import Decimal
from pathlib import Path

from strategy_application import (
    ApplicationCoordinationPolicy,
    AuthoritativeAccountSnapshot,
    CapitalAllocation,
    StrategyAccountingFill,
    StrategyApplication,
    StrategyApplicationLifecycle,
    StrategyApplicationSpec,
    StrategyFundingSettlement,
    StrategyInstanceLifecycle,
    StrategyLaunchSpec,
    StrategyRuntimeBinding,
    VirtualPositionBook,
)
from trading_strategies.btc_accumulation import (
    CoinMLongTakeProfitLadderConfig,
    CoinMLongTakeProfitLadderStrategyDefinition,
    EntrySizingMode,
)
from trading_strategies.kernel import (
    PositionEffect,
    RuleIntentMode,
    RuleIntentProposal,
    TradeSide,
    TradingRuleSpec,
)
from trading_strategies.rules import (
    InitialEntryFillInput,
    InitialEntryPhase,
    InitialEntryRule,
    InitialEntryRuleConfig,
    InitialEntryRuleContext,
    InitialEntryStartInput,
    LadderFillInput,
    LadderPositionOpenedInput,
    LadderTakeProfitPhase,
    LadderTakeProfitRule,
    SizedPosition,
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


ENTRY_RULE_KEY = "entry"
TAKE_PROFIT_RULE_KEY = "take-profit"


class FixedEntrySizer:
    def size_entry(self, *, reference_price: Decimal) -> SizedPosition:
        del reference_price
        return SizedPosition(
            quantity=Decimal("12"),
            quantity_unit="contracts",
        )


def strategy_spec(*, end_price: str = "120000"):
    return CoinMLongTakeProfitLadderStrategyDefinition().bind(
        CoinMLongTakeProfitLadderConfig(
            strategy_id="ladder-template",
            instrument="BTCUSD_PERP",
            entry_sizing_mode=EntrySizingMode.EFFECTIVE_LEVERAGE,
            entry_effective_leverage=Decimal("1.5"),
            first_take_profit_ratio=Decimal("1.1"),
            take_profit_end_price=Decimal(end_price),
            take_profit_count=3,
            tick_size=Decimal("0.1"),
            quantity_step=Decimal("1"),
        )
    )


def application_spec():
    shared_spec = strategy_spec()
    return StrategyApplicationSpec(
        application_id="ladder-scaling",
        strategy_launches=(
            StrategyLaunchSpec(
                strategy_instance_id="ladder-02",
                strategy_spec=shared_spec,
                capital_allocation=CapitalAllocationSpec(
                    "BTC",
                    Decimal("0.6"),
                ),
            ),
            StrategyLaunchSpec(
                strategy_instance_id="ladder-01",
                strategy_spec=shared_spec,
                capital_allocation=CapitalAllocationSpec(
                    "BTC",
                    Decimal("0.5"),
                ),
            ),
        ),
    )


def runtime_binding(_launch: StrategyLaunchSpec) -> StrategyRuntimeBinding:
    return StrategyRuntimeBinding(
        rules={
            ENTRY_RULE_KEY: InitialEntryRule(),
            TAKE_PROFIT_RULE_KEY: LadderTakeProfitRule(),
        },
        contexts={
            ENTRY_RULE_KEY: InitialEntryRuleContext(FixedEntrySizer()),
            TAKE_PROFIT_RULE_KEY: None,
        },
    )


def entry_only_spec(
    *,
    strategy_spec_id: str,
    strategy_type: str,
    instrument: str,
    direction: StrategyDirection,
) -> StrategySpec:
    side = (
        TradeSide.BUY
        if direction == StrategyDirection.LONG
        else TradeSide.SELL
    )
    return StrategySpec(
        strategy_spec_id=strategy_spec_id,
        strategy_type=strategy_type,
        display_name=strategy_type,
        parameters={"direction": direction.value},
        binding=InstrumentBinding(
            instrument,
            direction,
            StrategyProductType.INVERSE_PERPETUAL,
        ),
        rule_slots=(
            StrategyRuleSlotSpec(
                rule_key=ENTRY_RULE_KEY,
                rule=TradingRuleSpec(
                    rule_type=InitialEntryRule.rule_type,
                    config=InitialEntryRuleConfig(
                        intent_key="1",
                        side=side,
                    ),
                ),
                permissions=(RulePermission.OPEN,),
                subscriptions=(
                    "InitialEntryStartInput",
                    "InitialEntryFillInput",
                ),
            ),
        ),
        coordination_policy=StrategyCoordinationPolicy(),
    )


def entry_only_runtime(
    _launch: StrategyLaunchSpec,
) -> StrategyRuntimeBinding:
    return StrategyRuntimeBinding(
        rules={ENTRY_RULE_KEY: InitialEntryRule()},
        contexts={
            ENTRY_RULE_KEY: InitialEntryRuleContext(FixedEntrySizer())
        },
    )


def route_entry_fill(
    application: StrategyApplication,
    intent_key: str,
    *,
    fill_id: str,
    price: Decimal,
    account: bool = False,
    fee_amount: Decimal = Decimal("0"),
):
    owner = application.resolve_intent(intent_key)
    position = SizedPosition(Decimal("12"), "contracts")
    accounting_fill = (
        StrategyAccountingFill(
            fill_id=fill_id,
            intent_key=intent_key,
            instrument="BTCUSD_PERP",
            side=TradeSide.BUY,
            price=price,
            quantity=Decimal("12"),
            quantity_unit="contracts",
            settlement_asset="BTC",
            fee_amount=fee_amount,
            contract_size=Decimal("100"),
        )
        if account
        else None
    )
    return application.route_fill(
        intent_key,
        {
            ENTRY_RULE_KEY: InitialEntryFillInput(
                fill_id=fill_id,
                intent_key=owner.local_intent_key,
                side=TradeSide.BUY,
                price=price,
                quantity=Decimal("12"),
                actual_position=position,
            ),
            TAKE_PROFIT_RULE_KEY: LadderPositionOpenedInput(
                entry_price=price,
                position_quantity=Decimal("12"),
            ),
        },
        accounting_fill=accounting_fill,
    )


def route_exit_fill(
    application: StrategyApplication,
    intent_key: str,
    *,
    fill_id: str,
    account: bool = False,
    fee_amount: Decimal = Decimal("0"),
):
    owner = application.resolve_intent(intent_key)
    intent = next(
        item
        for item in application.active_intents
        if item.intent_key == intent_key
    )
    accounting_fill = (
        StrategyAccountingFill(
            fill_id=fill_id,
            intent_key=intent_key,
            instrument="BTCUSD_PERP",
            side=TradeSide.SELL,
            price=intent.target_price,
            quantity=intent.quantity,
            quantity_unit="contracts",
            settlement_asset="BTC",
            fee_amount=fee_amount,
            contract_size=Decimal("100"),
        )
        if account
        else None
    )
    return application.route_fill(
        intent_key,
        {
            TAKE_PROFIT_RULE_KEY: LadderFillInput(
                fill_id=fill_id,
                intent_key=owner.local_intent_key,
                side=TradeSide.SELL,
                price=intent.target_price,
                quantity=intent.quantity,
            )
        },
        accounting_fill=accounting_fill,
    )


class StrategyApplicationTests(unittest.TestCase):
    def test_application_layer_does_not_import_simulator_or_web(self) -> None:
        source_root = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "strategy_application"
        )
        forbidden_roots = {
            "experiment_system",
            "grid_server",
            "market_simulator",
            "simulation_runtime",
            "strategy_simulation",
        }
        violations: list[str] = []
        for source_path in source_root.rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                imported_roots: set[str] = set()
                if isinstance(node, ast.Import):
                    imported_roots = {
                        alias.name.split(".", 1)[0]
                        for alias in node.names
                    }
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_roots = {node.module.split(".", 1)[0]}
                for root in imported_roots & forbidden_roots:
                    violations.append(
                        f"{source_path.name}:{node.lineno} imports {root}"
                    )

        self.assertEqual(violations, [])

    def test_builds_same_spec_instances_in_stable_order_with_unique_resources(
        self,
    ) -> None:
        application = StrategyApplication.build(
            application_spec(),
            runtime_binding,
        )

        self.assertEqual(
            tuple(application.instances),
            ("ladder-01", "ladder-02"),
        )
        self.assertIs(
            application.instances["ladder-01"].spec,
            application.instances["ladder-02"].spec,
        )
        self.assertEqual(
            set(application.position_owner_ids),
            {"ladder-01:position", "ladder-02:position"},
        )
        self.assertEqual(
            set(application.allocations),
            {"ladder-01:allocation", "ladder-02:allocation"},
        )
        self.assertEqual(
            application.allocations["ladder-01:allocation"].amount,
            Decimal("0.5"),
        )
        self.assertEqual(
            application.allocations["ladder-02:allocation"].amount,
            Decimal("0.6"),
        )

    def test_routes_fills_to_one_instance_without_state_or_activity_leakage(
        self,
    ) -> None:
        application = StrategyApplication.build(
            application_spec(),
            runtime_binding,
        )
        application.start()
        first_entry = application.dispatch(
            "ladder-01",
            {
                ENTRY_RULE_KEY: InitialEntryStartInput(
                    Decimal("60000")
                )
            },
        ).strategy_result.intents[0]
        second_entry = application.dispatch(
            "ladder-02",
            {
                ENTRY_RULE_KEY: InitialEntryStartInput(
                    Decimal("70000")
                )
            },
        ).strategy_result.intents[0]

        self.assertEqual(first_entry.intent_key, "ladder-01:entry:1")
        self.assertEqual(second_entry.intent_key, "ladder-02:entry:1")
        self.assertEqual(application.event_sequence, 2)
        self.assertEqual(len(application.active_intents), 2)
        self.assertEqual(
            application.resolve_intent(first_entry.intent_key).rule_instance_id,
            "ladder-01:entry",
        )
        self.assertEqual(
            first_entry.tags["allocation_id"],
            "ladder-01:allocation",
        )
        self.assertEqual(
            second_entry.tags["position_owner_id"],
            "ladder-02:position",
        )

        route_entry_fill(
            application,
            first_entry.intent_key,
            fill_id="entry-fill-01",
            price=Decimal("61000"),
        )

        first_instance = application.instances["ladder-01"]
        second_instance = application.instances["ladder-02"]
        self.assertEqual(
            first_instance.rule_state(ENTRY_RULE_KEY).phase,
            InitialEntryPhase.FILLED,
        )
        self.assertEqual(
            first_instance.rule_state(TAKE_PROFIT_RULE_KEY).phase,
            LadderTakeProfitPhase.POSITION_OPEN,
        )
        self.assertEqual(
            second_instance.rule_state(ENTRY_RULE_KEY).phase,
            InitialEntryPhase.ENTRY_PENDING,
        )
        self.assertEqual(
            second_instance.rule_state(TAKE_PROFIT_RULE_KEY).phase,
            LadderTakeProfitPhase.WAITING_POSITION,
        )
        self.assertEqual(len(application.active_intents), 4)

        first_exit = next(
            intent
            for intent in application.active_intents
            if intent.tags["strategy_instance_id"] == "ladder-01"
        )
        route_exit_fill(
            application,
            first_exit.intent_key,
            fill_id="exit-fill-01",
        )

        self.assertEqual(
            first_instance.rule_state(TAKE_PROFIT_RULE_KEY).phase,
            LadderTakeProfitPhase.PARTIALLY_EXITED,
        )
        self.assertEqual(
            second_instance.rule_state(TAKE_PROFIT_RULE_KEY).phase,
            LadderTakeProfitPhase.WAITING_POSITION,
        )
        self.assertEqual(
            application.activity("ladder-01").consumed_fill_count,
            2,
        )
        self.assertEqual(
            application.activity("ladder-01").issued_intent_count,
            4,
        )
        self.assertEqual(
            application.activity("ladder-02").consumed_fill_count,
            0,
        )
        self.assertEqual(
            application.activity("ladder-02").active_intent_count,
            1,
        )
        self.assertEqual(
            application.lifecycle,
            StrategyApplicationLifecycle.ACTIVE,
        )

    def test_application_completes_only_after_every_instance_completes(
        self,
    ) -> None:
        application = StrategyApplication.build(
            application_spec(),
            runtime_binding,
        )
        application.start()
        entry_keys: dict[str, str] = {}
        for instance_id, price in (
            ("ladder-01", Decimal("60000")),
            ("ladder-02", Decimal("70000")),
        ):
            entry = application.dispatch(
                instance_id,
                {
                    ENTRY_RULE_KEY: InitialEntryStartInput(price)
                },
            ).strategy_result.intents[0]
            entry_keys[instance_id] = entry.intent_key
            route_entry_fill(
                application,
                entry.intent_key,
                fill_id=f"{instance_id}:entry-fill",
                price=price,
            )

        for instance_id in ("ladder-01", "ladder-02"):
            exit_keys = [
                intent.intent_key
                for intent in application.active_intents
                if intent.tags["strategy_instance_id"] == instance_id
            ]
            for index, intent_key in enumerate(exit_keys, start=1):
                route_exit_fill(
                    application,
                    intent_key,
                    fill_id=f"{instance_id}:exit-fill:{index}",
                )
            if instance_id == "ladder-01":
                self.assertEqual(
                    application.lifecycle,
                    StrategyApplicationLifecycle.ACTIVE,
                )

        self.assertEqual(application.active_intents, ())
        self.assertEqual(
            application.lifecycle,
            StrategyApplicationLifecycle.COMPLETED,
        )
        self.assertEqual(
            application.activity("ladder-01").consumed_fill_count,
            4,
        )
        self.assertEqual(
            application.activity("ladder-02").consumed_fill_count,
            4,
        )

    def test_unknown_fill_fails_fast_without_guessing_an_instance(self) -> None:
        application = StrategyApplication.build(
            application_spec(),
            runtime_binding,
        )
        application.start()

        with self.assertRaisesRegex(ValueError, "unknown intent"):
            application.route_fill("unknown:intent", {})

        self.assertEqual(
            application.lifecycle,
            StrategyApplicationLifecycle.FAILED,
        )
        self.assertEqual(application.event_sequence, 0)
        self.assertEqual(
            {
                instance.lifecycle
                for instance in application.instances.values()
            },
            {StrategyInstanceLifecycle.FAILED},
        )

    def test_5v2e_attributes_inverse_position_fee_pnl_and_unrealized(
        self,
    ) -> None:
        application = StrategyApplication.build(
            application_spec(),
            runtime_binding,
        )
        application.start()
        entry = application.dispatch(
            "ladder-01",
            {
                ENTRY_RULE_KEY: InitialEntryStartInput(Decimal("60000"))
            },
        ).strategy_result.intents[0]
        route_entry_fill(
            application,
            entry.intent_key,
            fill_id="accounted-entry",
            price=Decimal("60000"),
            account=True,
            fee_amount=Decimal("0.00001"),
        )

        opened = application.attribution_snapshot(
            "ladder-01",
            {"BTCUSD_PERP": Decimal("50000")},
        )
        self.assertEqual(opened.positions[0].quantity, Decimal("12"))
        self.assertEqual(
            opened.unrealized_pnl,
            Decimal("1200")
            * (
                Decimal("1") / Decimal("60000")
                - Decimal("1") / Decimal("50000")
            ),
        )
        self.assertEqual(opened.total_fees, Decimal("0.00001"))

        exit_intent = next(
            intent
            for intent in application.active_intents
            if intent.tags["strategy_instance_id"] == "ladder-01"
        )
        route_exit_fill(
            application,
            exit_intent.intent_key,
            fill_id="accounted-exit",
            account=True,
            fee_amount=Decimal("0.000002"),
        )

        snapshot = application.attribution_snapshot(
            "ladder-01",
            {"BTCUSD_PERP": Decimal("70000")},
        )
        expected_gross = (
            exit_intent.quantity
            * Decimal("100")
            * (
                Decimal("1") / Decimal("60000")
                - Decimal("1") / exit_intent.target_price
            )
        )
        self.assertEqual(snapshot.positions[0].quantity, Decimal("8"))
        self.assertEqual(snapshot.gross_realized_pnl, expected_gross)
        self.assertEqual(snapshot.total_fees, Decimal("0.000012"))
        self.assertEqual(
            snapshot.net_realized_pnl,
            expected_gross - Decimal("0.000012"),
        )
        self.assertEqual(snapshot.fill_count, 2)

    def test_5v2e_owner_reduce_violation_fails_before_strategy_commit(
        self,
    ) -> None:
        application = StrategyApplication.build(
            application_spec(),
            runtime_binding,
        )
        application.start()
        entry = application.dispatch(
            "ladder-01",
            {
                ENTRY_RULE_KEY: InitialEntryStartInput(Decimal("60000"))
            },
        ).strategy_result.intents[0]
        route_entry_fill(
            application,
            entry.intent_key,
            fill_id="entry-before-owner-violation",
            price=Decimal("60000"),
            account=True,
        )
        exit_intent = next(
            intent
            for intent in application.active_intents
            if intent.tags["strategy_instance_id"] == "ladder-01"
        )
        before_sequence = application.instances[
            "ladder-01"
        ].event_sequence
        owner = application.resolve_intent(exit_intent.intent_key)

        with self.assertRaisesRegex(ValueError, "partial accounted fills"):
            application.route_fill(
                exit_intent.intent_key,
                {
                    TAKE_PROFIT_RULE_KEY: LadderFillInput(
                        fill_id="oversized-exit",
                        intent_key=owner.local_intent_key,
                        side=TradeSide.SELL,
                        price=exit_intent.target_price,
                        quantity=exit_intent.quantity,
                    )
                },
                accounting_fill=StrategyAccountingFill(
                    fill_id="oversized-exit",
                    intent_key=exit_intent.intent_key,
                    instrument="BTCUSD_PERP",
                    side=TradeSide.SELL,
                    price=exit_intent.target_price,
                    quantity=Decimal("13"),
                    quantity_unit="contracts",
                    settlement_asset="BTC",
                    contract_size=Decimal("100"),
                ),
            )

        self.assertEqual(
            application.instances["ladder-01"].event_sequence,
            before_sequence,
        )
        self.assertEqual(
            application.position_book.position(
                "ladder-01:position",
                "BTCUSD_PERP",
            ).quantity,
            Decimal("12"),
        )

    def test_5v2e_funding_is_split_by_absolute_notional_exposure(
        self,
    ) -> None:
        application = StrategyApplication.build(
            application_spec(),
            runtime_binding,
        )
        application.start()
        for instance_id, price in (
            ("ladder-01", Decimal("60000")),
            ("ladder-02", Decimal("70000")),
        ):
            entry = application.dispatch(
                instance_id,
                {ENTRY_RULE_KEY: InitialEntryStartInput(price)},
            ).strategy_result.intents[0]
            route_entry_fill(
                application,
                entry.intent_key,
                fill_id=f"{instance_id}:funding-entry",
                price=price,
                account=True,
            )

        allocations = application.attribute_funding(
            StrategyFundingSettlement(
                settlement_id="funding-1",
                instrument="BTCUSD_PERP",
                product_type=StrategyProductType.INVERSE_PERPETUAL,
                settlement_asset="BTC",
                wallet_delta=Decimal("-0.0024"),
                position_quantity=Decimal("24"),
                mark_price=Decimal("65000"),
            )
        )

        self.assertEqual(
            tuple(item.strategy_instance_id for item in allocations),
            ("ladder-01", "ladder-02"),
        )
        self.assertEqual(
            tuple(item.exposure_weight for item in allocations),
            (Decimal("0.5"), Decimal("0.5")),
        )
        self.assertEqual(
            application.attribution_snapshot(
                "ladder-01",
                {"BTCUSD_PERP": Decimal("65000")},
            ).total_funding,
            Decimal("-0.0012"),
        )
        self.assertEqual(
            application.attribution_snapshot(
                "ladder-02",
                {"BTCUSD_PERP": Decimal("65000")},
            ).total_funding,
            Decimal("-0.0012"),
        )

    def test_5v2e_reconciliation_reports_balanced_and_residual_results(
        self,
    ) -> None:
        application = StrategyApplication.build(
            application_spec(),
            runtime_binding,
        )
        application.start()
        entry = application.dispatch(
            "ladder-01",
            {ENTRY_RULE_KEY: InitialEntryStartInput(Decimal("60000"))},
        ).strategy_result.intents[0]
        route_entry_fill(
            application,
            entry.intent_key,
            fill_id="reconcile-entry",
            price=Decimal("60000"),
            account=True,
            fee_amount=Decimal("0.00001"),
        )
        mark = Decimal("65000")
        attributed = application.attribution_snapshot(
            "ladder-01",
            {"BTCUSD_PERP": mark},
        )
        account = AuthoritativeAccountSnapshot(
            settlement_asset="BTC",
            initial_equity=Decimal("1.1"),
            positions={"BTCUSD_PERP": Decimal("12")},
            gross_realized_pnl=Decimal("0"),
            total_fees=Decimal("0.00001"),
            total_funding=Decimal("0"),
            unrealized_pnl=attributed.unrealized_pnl,
        )

        balanced = application.reconcile_attribution(
            account,
            {"BTCUSD_PERP": mark},
        )
        self.assertTrue(balanced.balanced)

        residual = application.reconcile_attribution(
            AuthoritativeAccountSnapshot(
                settlement_asset="BTC",
                initial_equity=Decimal("1.2"),
                positions={"BTCUSD_PERP": Decimal("13")},
                gross_realized_pnl=Decimal("0"),
                total_fees=Decimal("0.00002"),
                total_funding=Decimal("0"),
                unrealized_pnl=attributed.unrealized_pnl,
            ),
            {"BTCUSD_PERP": mark},
        )
        self.assertFalse(residual.balanced)
        self.assertEqual(
            residual.position_residuals["BTCUSD_PERP"],
            Decimal("1"),
        )
        self.assertEqual(residual.allocation_residual, Decimal("0.1"))
        self.assertEqual(residual.fee_residual, Decimal("0.00001"))

    def test_5v2e_linear_contract_uses_price_difference_pnl(self) -> None:
        book = VirtualPositionBook(
            {
                "linear:allocation": CapitalAllocation(
                    allocation_id="linear:allocation",
                    strategy_instance_id="linear",
                    settlement_asset="USDT",
                    amount=Decimal("10000"),
                )
            }
        )

        def intent(
            key: str,
            side: TradeSide,
            quantity: str,
            effect: PositionEffect,
        ) -> RuleIntentProposal:
            return RuleIntentProposal(
                intent_key=key,
                side=side,
                quantity=Decimal(quantity),
                quantity_unit="BTC",
                position_effect=effect,
                intent_mode=RuleIntentMode.ACTIVE,
                reduce_only=effect in {
                    PositionEffect.REDUCE,
                    PositionEffect.CLOSE,
                },
                tags={"instrument": "BTCUSDT"},
            )

        opening = intent(
            "linear:entry:1",
            TradeSide.BUY,
            "0.2",
            PositionEffect.OPEN,
        )
        book.commit_fill(
            book.preview_fill(
                fill=StrategyAccountingFill(
                    fill_id="linear-open",
                    intent_key=opening.intent_key,
                    instrument="BTCUSDT",
                    side=TradeSide.BUY,
                    price=Decimal("50000"),
                    quantity=Decimal("0.2"),
                    quantity_unit="BTC",
                    settlement_asset="USDT",
                    fee_amount=Decimal("2"),
                ),
                intent=opening,
                strategy_instance_id="linear",
                position_owner_id="linear:position",
                product_type=StrategyProductType.LINEAR_PERPETUAL,
            )
        )
        closing = intent(
            "linear:exit:1",
            TradeSide.SELL,
            "0.1",
            PositionEffect.REDUCE,
        )
        book.commit_fill(
            book.preview_fill(
                fill=StrategyAccountingFill(
                    fill_id="linear-reduce",
                    intent_key=closing.intent_key,
                    instrument="BTCUSDT",
                    side=TradeSide.SELL,
                    price=Decimal("60000"),
                    quantity=Decimal("0.1"),
                    quantity_unit="BTC",
                    settlement_asset="USDT",
                    fee_amount=Decimal("1"),
                ),
                intent=closing,
                strategy_instance_id="linear",
                position_owner_id="linear:position",
                product_type=StrategyProductType.LINEAR_PERPETUAL,
            )
        )

        snapshot = book.snapshot(
            "linear",
            {"BTCUSDT": Decimal("55000")},
        )
        self.assertEqual(snapshot.positions[0].quantity, Decimal("0.1"))
        self.assertEqual(snapshot.gross_realized_pnl, Decimal("1000"))
        self.assertEqual(snapshot.total_fees, Decimal("3"))
        self.assertEqual(snapshot.unrealized_pnl, Decimal("500"))
        self.assertEqual(snapshot.attributed_equity, Decimal("11497"))

    def test_5v2e_virtual_book_rejects_owner_over_reduce(self) -> None:
        book = VirtualPositionBook(
            {
                "owner:allocation": CapitalAllocation(
                    allocation_id="owner:allocation",
                    strategy_instance_id="owner",
                    settlement_asset="USDT",
                    amount=Decimal("10000"),
                )
            }
        )

        def intent(
            key: str,
            side: TradeSide,
            quantity: str,
            effect: PositionEffect,
        ) -> RuleIntentProposal:
            return RuleIntentProposal(
                intent_key=key,
                side=side,
                quantity=Decimal(quantity),
                quantity_unit="BTC",
                position_effect=effect,
                intent_mode=RuleIntentMode.ACTIVE,
                reduce_only=effect in {
                    PositionEffect.REDUCE,
                    PositionEffect.CLOSE,
                },
                tags={"instrument": "BTCUSDT"},
            )

        opening = intent(
            "owner:entry:1",
            TradeSide.BUY,
            "0.2",
            PositionEffect.OPEN,
        )
        book.commit_fill(
            book.preview_fill(
                fill=StrategyAccountingFill(
                    fill_id="owner-open",
                    intent_key=opening.intent_key,
                    instrument="BTCUSDT",
                    side=TradeSide.BUY,
                    price=Decimal("50000"),
                    quantity=Decimal("0.2"),
                    quantity_unit="BTC",
                    settlement_asset="USDT",
                ),
                intent=opening,
                strategy_instance_id="owner",
                position_owner_id="owner:position",
                product_type=StrategyProductType.LINEAR_PERPETUAL,
            )
        )
        oversized = intent(
            "owner:exit:1",
            TradeSide.SELL,
            "0.3",
            PositionEffect.REDUCE,
        )

        with self.assertRaisesRegex(ValueError, "exceeds virtual position"):
            book.preview_fill(
                fill=StrategyAccountingFill(
                    fill_id="owner-over-reduce",
                    intent_key=oversized.intent_key,
                    instrument="BTCUSDT",
                    side=TradeSide.SELL,
                    price=Decimal("60000"),
                    quantity=Decimal("0.3"),
                    quantity_unit="BTC",
                    settlement_asset="USDT",
                ),
                intent=oversized,
                strategy_instance_id="owner",
                position_owner_id="owner:position",
                product_type=StrategyProductType.LINEAR_PERPETUAL,
            )

        position = book.position("owner:position", "BTCUSDT")
        assert position is not None
        self.assertEqual(position.quantity, Decimal("0.2"))

    def test_rejects_duplicate_ids_and_missing_allocation(
        self,
    ) -> None:
        shared = strategy_spec()
        allocation = CapitalAllocationSpec("BTC", Decimal("0.5"))
        with self.assertRaisesRegex(ValueError, "allocation"):
            StrategyLaunchSpec("missing-allocation", shared)
        with self.assertRaisesRegex(ValueError, "must be unique"):
            StrategyApplicationSpec(
                "duplicate",
                (
                    StrategyLaunchSpec("same", shared, allocation),
                    StrategyLaunchSpec("same", shared, allocation),
                ),
            )

    def test_5v2d_runs_different_strategy_specs_in_stable_batch_order(
        self,
    ) -> None:
        long_btc = entry_only_spec(
            strategy_spec_id="btc-entry:spec",
            strategy_type="btc-entry/v1",
            instrument="BTCUSD_PERP",
            direction=StrategyDirection.LONG,
        )
        alternate_btc = entry_only_spec(
            strategy_spec_id="btc-entry-alternate:spec",
            strategy_type="btc-entry-alternate/v1",
            instrument="BTCUSD_PERP",
            direction=StrategyDirection.LONG,
        )
        spec = StrategyApplicationSpec(
            "multi-strategy",
            (
                StrategyLaunchSpec(
                    "z-btc-alternate",
                    alternate_btc,
                    CapitalAllocationSpec("BTC", Decimal("0.5")),
                ),
                StrategyLaunchSpec(
                    "a-btc",
                    long_btc,
                    CapitalAllocationSpec("BTC", Decimal("1")),
                ),
            ),
        )
        self.assertEqual(spec.strategy_specs, (long_btc, alternate_btc))
        application = StrategyApplication.build(spec, entry_only_runtime)
        application.start()

        result = application.dispatch_batch(
            {
                "z-btc-alternate": {
                    ENTRY_RULE_KEY: InitialEntryStartInput(Decimal("61000"))
                },
                "a-btc": {
                    ENTRY_RULE_KEY: InitialEntryStartInput(Decimal("60000"))
                },
            }
        )

        self.assertEqual(result.application_event_sequence, 1)
        self.assertEqual(
            tuple(
                item.strategy_instance_id
                for item in result.strategy_results
            ),
            ("a-btc", "z-btc-alternate"),
        )
        self.assertEqual(application.event_sequence, 1)
        self.assertEqual(
            application.instances["a-btc"].event_sequence,
            1,
        )
        self.assertEqual(
            application.instances["z-btc-alternate"].event_sequence,
            1,
        )
        self.assertEqual(
            tuple(intent.intent_key for intent in application.active_intents),
            ("a-btc:entry:1", "z-btc-alternate:entry:1"),
        )

    def test_5v2d_rejects_opposing_intent_against_active_intent(self) -> None:
        long_spec = entry_only_spec(
            strategy_spec_id="long-active:spec",
            strategy_type="long-active/v1",
            instrument="BTCUSD_PERP",
            direction=StrategyDirection.LONG,
        )
        short_spec = entry_only_spec(
            strategy_spec_id="short-later:spec",
            strategy_type="short-later/v1",
            instrument="BTCUSD_PERP",
            direction=StrategyDirection.SHORT,
        )
        application = StrategyApplication.build(
            StrategyApplicationSpec(
                "sequential-opposing",
                (
                    StrategyLaunchSpec(
                        "long",
                        long_spec,
                        CapitalAllocationSpec("BTC", Decimal("1")),
                    ),
                    StrategyLaunchSpec(
                        "short",
                        short_spec,
                        CapitalAllocationSpec("BTC", Decimal("1")),
                    ),
                ),
            ),
            entry_only_runtime,
        )
        application.start()
        application.dispatch(
            "long",
            {
                ENTRY_RULE_KEY: InitialEntryStartInput(Decimal("60000"))
            },
        )

        with self.assertRaisesRegex(ValueError, "opposing opening intents"):
            application.dispatch(
                "short",
                {
                    ENTRY_RULE_KEY: InitialEntryStartInput(
                        Decimal("60000")
                    )
                },
            )

        self.assertEqual(application.event_sequence, 1)
        self.assertEqual(
            application.instances["short"].event_sequence,
            0,
        )

    def test_5v2d_batch_preview_failure_leaves_every_strategy_uncommitted(
        self,
    ) -> None:
        good = entry_only_spec(
            strategy_spec_id="good:spec",
            strategy_type="good/v1",
            instrument="BTCUSD_PERP",
            direction=StrategyDirection.LONG,
        )
        bad = entry_only_spec(
            strategy_spec_id="bad:spec",
            strategy_type="bad/v1",
            instrument="ETHUSD_PERP",
            direction=StrategyDirection.LONG,
        )
        spec = StrategyApplicationSpec(
            "atomic-failure",
            (
                StrategyLaunchSpec(
                    "a-good",
                    good,
                    CapitalAllocationSpec("BTC", Decimal("1")),
                ),
                StrategyLaunchSpec(
                    "z-bad",
                    bad,
                    CapitalAllocationSpec("ETH", Decimal("10")),
                ),
            ),
        )
        application = StrategyApplication.build(spec, entry_only_runtime)
        application.start()

        with self.assertRaisesRegex(TypeError, "does not subscribe"):
            application.dispatch_batch(
                {
                    "a-good": {
                        ENTRY_RULE_KEY: InitialEntryStartInput(
                            Decimal("60000")
                        )
                    },
                    "z-bad": {ENTRY_RULE_KEY: object()},
                }
            )

        self.assertEqual(application.event_sequence, 0)
        self.assertEqual(application.active_intents, ())
        self.assertEqual(
            application.instances["a-good"].event_sequence,
            0,
        )
        self.assertEqual(
            application.instances["a-good"].rule_state(
                ENTRY_RULE_KEY
            ).phase,
            InitialEntryPhase.WAITING_START,
        )
        self.assertEqual(
            application.lifecycle,
            StrategyApplicationLifecycle.FAILED,
        )

    def test_5v2d_rejects_opposing_open_intents_before_commit(self) -> None:
        long_spec = entry_only_spec(
            strategy_spec_id="long:spec",
            strategy_type="long/v1",
            instrument="BTCUSD_PERP",
            direction=StrategyDirection.LONG,
        )
        short_spec = entry_only_spec(
            strategy_spec_id="short:spec",
            strategy_type="short/v1",
            instrument="BTCUSD_PERP",
            direction=StrategyDirection.SHORT,
        )
        spec = StrategyApplicationSpec(
            "opposing",
            (
                StrategyLaunchSpec(
                    "long",
                    long_spec,
                    CapitalAllocationSpec("BTC", Decimal("1")),
                ),
                StrategyLaunchSpec(
                    "short",
                    short_spec,
                    CapitalAllocationSpec("BTC", Decimal("1")),
                ),
            ),
            coordination_policy=ApplicationCoordinationPolicy(),
        )
        application = StrategyApplication.build(spec, entry_only_runtime)
        application.start()

        with self.assertRaisesRegex(ValueError, "opposing opening intents"):
            application.dispatch_batch(
                {
                    "short": {
                        ENTRY_RULE_KEY: InitialEntryStartInput(
                            Decimal("60000")
                        )
                    },
                    "long": {
                        ENTRY_RULE_KEY: InitialEntryStartInput(
                            Decimal("60000")
                        )
                    },
                }
            )

        self.assertEqual(application.event_sequence, 0)
        self.assertEqual(application.active_intents, ())
        self.assertEqual(
            {
                instance.event_sequence
                for instance in application.instances.values()
            },
            {0},
        )


if __name__ == "__main__":
    unittest.main()
