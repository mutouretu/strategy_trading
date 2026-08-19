"""Generic bridge from the market simulator to StrategyApplication."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import Enum
from typing import Protocol

from market_protocol import MarketFrame
from simulation_runtime import (
    FundingSettlement,
    IntentSnapshot,
    OrderSide,
    SimFill,
    SimulationResult,
    TradeInstruction,
    TradeIntentMode,
)
from strategy_application import (
    ApplicationIntentOwner,
    AuthoritativeAccountSnapshot,
    StrategyAccountingFill,
    StrategyApplication,
    StrategyFundingSettlement,
)
from trading_strategies.kernel import RuleIntentMode, TradeSide


class StrategyApplicationEventRouter(Protocol):
    """Translate strategy-specific market/fill facts into Rule inputs."""

    def inputs_before_instructions(
        self,
        application: StrategyApplication,
        frame: MarketFrame,
    ) -> Mapping[str, Mapping[str, object]]: ...

    def inputs_for_fill(
        self,
        application: StrategyApplication,
        fill: SimFill,
        owner: ApplicationIntentOwner,
    ) -> Mapping[str, object]: ...


class StrategyApplicationSimulationAdapter:
    """Execute any StrategyApplication without teaching Runner its domain."""

    def __init__(
        self,
        application: StrategyApplication,
        *,
        event_router: StrategyApplicationEventRouter,
        contract_size: Decimal,
        settlement_asset: str,
        external_strategy_type: str,
        instruction_tags: Mapping[str, str] | None = None,
    ) -> None:
        self.application = application
        self.event_router = event_router
        self.contract_size = Decimal(contract_size)
        self.settlement_asset = settlement_asset.strip().upper()
        self.external_strategy_type = external_strategy_type.strip()
        self.instruction_tags = dict(instruction_tags or {})
        if self.contract_size <= 0:
            raise ValueError("contract_size must be > 0")
        if not self.settlement_asset:
            raise ValueError("settlement_asset must not be empty")
        if not self.external_strategy_type:
            raise ValueError("external_strategy_type must not be empty")
        bindings = {
            (
                instance.spec.binding.instrument,
                instance.spec.binding.product_type,
            )
            for instance in application.instances.values()
        }
        if len(bindings) != 1:
            raise ValueError(
                "v1 Simulation Adapter requires one instrument/product binding"
            )
        self.instrument, self.product_type = next(iter(bindings))
        self._instruction_counter = 0
        self._issued_active_intents: set[str] = set()

    def initialize(self, frame: MarketFrame) -> None:
        self._validate_frame(frame)
        self.application.start()

    def instructions_for(self, frame: MarketFrame) -> tuple[TradeInstruction, ...]:
        self._validate_frame(frame)
        routed = self.event_router.inputs_before_instructions(
            self.application,
            frame,
        )
        if routed:
            self.application.dispatch_batch(routed)
        instructions: list[TradeInstruction] = []
        for intent in self.application.active_intents:
            mode = TradeIntentMode(intent.intent_mode.value)
            if mode == TradeIntentMode.ACTIVE:
                if intent.intent_key in self._issued_active_intents:
                    continue
                self._issued_active_intents.add(intent.intent_key)
                price = intent.target_price or frame.open
            else:
                assert intent.target_price is not None
                if not frame.low <= intent.target_price <= frame.high:
                    continue
                price = intent.target_price
            self._instruction_counter += 1
            instructions.append(
                TradeInstruction(
                    instruction_key=(
                        f"{intent.intent_key}:instruction:"
                        f"{self._instruction_counter}"
                    ),
                    source_intent_key=intent.intent_key,
                    instrument=self.instrument,
                    side=OrderSide(intent.side.value),
                    quantity=intent.quantity,
                    price=price,
                    frame_sequence=frame.sequence,
                    intent_mode=mode,
                    reduce_only=intent.reduce_only,
                    tags=self._intent_tags(intent),
                )
            )
        return tuple(instructions)

    def on_fills(self, fills: Sequence[SimFill]) -> None:
        self._route_fills(fills)

    def on_accounting(
        self,
        fills: Sequence[SimFill],
        funding: FundingSettlement | None,
    ) -> None:
        self._route_fills(fills)
        if funding is not None:
            self.on_funding(funding)

    def _route_fills(self, fills: Sequence[SimFill]) -> None:
        for fill in fills:
            owner = self.application.resolve_intent(fill.source_intent_key)
            inputs = self.event_router.inputs_for_fill(
                self.application,
                fill,
                owner,
            )
            self.application.route_fill(
                fill.source_intent_key,
                inputs,
                accounting_fill=StrategyAccountingFill(
                    fill_id=fill.fill_id,
                    intent_key=fill.source_intent_key,
                    instrument=fill.instrument,
                    side=TradeSide(fill.side.value),
                    price=fill.price,
                    quantity=fill.quantity,
                    quantity_unit=fill.tags["quantity_unit"],
                    settlement_asset=fill.fee_asset,
                    fee_amount=fill.fee_amount,
                    contract_size=self.contract_size,
                    sequence=fill.sequence,
                    timestamp=fill.timestamp,
                ),
            )

    def on_funding(self, settlement: FundingSettlement) -> None:
        self.application.attribute_funding(
            StrategyFundingSettlement(
                settlement_id=settlement.settlement_id,
                instrument=settlement.instrument,
                product_type=self.product_type,
                settlement_asset=settlement.settlement_asset,
                wallet_delta=settlement.wallet_delta,
                position_quantity=settlement.position_quantity,
                mark_price=settlement.mark_price,
                sequence=settlement.sequence,
                timestamp=settlement.timestamp,
            )
        )

    def on_market(self, frame: MarketFrame) -> None:
        self._validate_frame(frame)

    def visible_intents(self) -> tuple[IntentSnapshot, ...]:
        return tuple(
            IntentSnapshot(
                intent_key=intent.intent_key,
                instrument=self.instrument,
                side=OrderSide(intent.side.value),
                quantity=intent.quantity,
                intent_mode=TradeIntentMode(intent.intent_mode.value),
                target_price=(
                    intent.target_price
                    if intent.intent_mode == RuleIntentMode.PASSIVE
                    else None
                ),
                reduce_only=intent.reduce_only,
                tags=self._intent_tags(intent),
            )
            for intent in self.application.active_intents
        )

    def application_document(self, result: SimulationResult) -> dict[str, object]:
        marks = dict(result.equity_curve[-1].marks)
        strategies: list[dict[str, object]] = []
        for launch in self.application.spec.strategy_launches:
            instance = self.application.instances[launch.strategy_instance_id]
            snapshot = self.application.attribution_snapshot(
                launch.strategy_instance_id,
                marks,
            )
            rules = []
            for slot in instance.spec.rule_slots:
                rule_instance = instance.rule_instances[slot.rule_key]
                rules.append(
                    {
                        "rule_key": slot.rule_key,
                        "rule_type": slot.rule.rule_type,
                        "rule_instance_id": rule_instance.rule_instance_id,
                        "lifecycle": rule_instance.lifecycle.value,
                        "permissions": [item.value for item in slot.permissions],
                        "subscriptions": list(slot.subscriptions),
                        "config": _json_value(slot.rule.config),
                    }
                )
            strategies.append(
                {
                    "strategy_instance_id": instance.strategy_instance_id,
                    "strategy_spec_id": instance.spec.strategy_spec_id,
                    "strategy_type": instance.spec.strategy_type,
                    "display_name": instance.spec.display_name,
                    "lifecycle": instance.lifecycle.value,
                    "parameters": _json_value(instance.spec.parameters),
                    "coordination_policy": (
                        instance.spec.coordination_policy.to_document()
                    ),
                    "allocation_id": instance.allocation_id,
                    "position_owner_id": instance.position_owner_id,
                    "binding": {
                        "instrument": instance.spec.binding.instrument,
                        "direction": instance.spec.binding.direction.value,
                        "product_type": instance.spec.binding.product_type.value,
                    },
                    "allocation": {
                        "settlement_asset": snapshot.settlement_asset,
                        "amount": str(snapshot.allocated_capital),
                    },
                    "attribution": _attribution_document(snapshot),
                    "rules": rules,
                }
            )
        account_unrealized = self._account_unrealized(result, marks)
        reconciliation = self.application.reconcile_attribution(
            AuthoritativeAccountSnapshot(
                settlement_asset=result.equity_asset,
                initial_equity=result.initial_equity,
                positions=result.final_positions,
                gross_realized_pnl=result.gross_realized_pnl,
                total_fees=result.total_fees,
                total_funding=result.total_funding,
                unrealized_pnl=account_unrealized,
            ),
            marks,
        )
        return {
            "application_id": self.application.application_id,
            "lifecycle": self.application.lifecycle.value,
            "strategy_count": len(strategies),
            "strategies": strategies,
            "reconciliation": {
                "balanced": reconciliation.balanced,
                "position_residuals": {
                    key: str(value)
                    for key, value in reconciliation.position_residuals.items()
                },
                "allocation_residual": str(
                    reconciliation.allocation_residual
                ),
                "gross_realized_pnl_residual": str(
                    reconciliation.gross_realized_pnl_residual
                ),
                "fee_residual": str(reconciliation.fee_residual),
                "funding_residual": str(reconciliation.funding_residual),
                "unrealized_pnl_residual": str(
                    reconciliation.unrealized_pnl_residual
                ),
            },
        }

    def _intent_tags(self, intent) -> dict[str, str]:
        tags = {
            key: str(value) for key, value in self.instruction_tags.items()
        }
        tags.update(
            {key: str(value) for key, value in intent.tags.items()}
        )
        tags.update(
            {
                "application_id": self.application.application_id,
                "strategy_type": self.external_strategy_type,
                "core_strategy_type": str(intent.tags["strategy_type"]),
                "role": intent.role,
                "quantity_unit": intent.quantity_unit,
                "contract_size": str(self.contract_size),
            }
        )
        return tags

    def _validate_frame(self, frame: MarketFrame) -> None:
        if frame.instrument != self.instrument:
            raise ValueError("market and StrategyApplication instruments must match")

    def _account_unrealized(
        self,
        result: SimulationResult,
        marks: Mapping[str, Decimal],
    ) -> Decimal:
        total = Decimal("0")
        for instrument, quantity in result.final_positions.items():
            amount = Decimal(quantity)
            average = Decimal(result.final_average_costs[instrument])
            mark = Decimal(marks[instrument])
            direction = Decimal("1") if amount > 0 else Decimal("-1")
            if self.product_type.value == "INVERSE_PERPETUAL":
                total += (
                    direction
                    * abs(amount)
                    * self.contract_size
                    * (Decimal("1") / average - Decimal("1") / mark)
                )
            else:
                total += (
                    direction
                    * abs(amount)
                    * self.contract_size
                    * (mark - average)
                )
        return total


def _attribution_document(snapshot) -> dict[str, object]:
    return {
        "gross_realized_pnl": str(snapshot.gross_realized_pnl),
        "total_fees": str(snapshot.total_fees),
        "net_realized_pnl": str(snapshot.net_realized_pnl),
        "total_funding": str(snapshot.total_funding),
        "net_pnl_after_fees_and_funding": str(
            snapshot.net_pnl_after_fees_and_funding
        ),
        "unrealized_pnl": str(snapshot.unrealized_pnl),
        "attributed_equity": str(snapshot.attributed_equity),
        "fill_count": snapshot.fill_count,
        "funding_event_count": snapshot.funding_event_count,
        "positions": [
            {
                "position_owner_id": item.position_owner_id,
                "instrument": item.instrument,
                "product_type": item.product_type.value,
                "quantity": str(item.quantity),
                "quantity_unit": item.quantity_unit,
                "average_entry_price": str(item.average_entry_price),
                "contract_size": str(item.contract_size),
            }
            for item in snapshot.positions
        ],
    }


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_json_value(item) for item in value]
    return str(value)
