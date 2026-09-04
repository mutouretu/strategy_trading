from __future__ import annotations

from collections.abc import Mapping

from experiment_system import ComponentSpec
from strategy_application import (
    StrategyApplication,
    StrategyApplicationSpec,
    StrategyLaunchSpec,
    build_coinm_long_take_profit_ladder_runtime,
)

from trading_strategies.btc_accumulation import (
    CoinMLongTakeProfitLadderConfig,
    EntrySizingMode,
)
from trading_strategies.btc_accumulation.long_take_profit_ladder import (
    ENTRY_RULE_KEY,
    TAKE_PROFIT_RULE_KEY,
    CoinMLongTakeProfitLadderStrategyDefinition,
)
from trading_strategies.entry_then_ladder_exit import (
    ENTRY_THEN_LADDER_EXIT_V1,
)
from trading_strategies.rules import (
    InitialEntryPhase,
    InitialEntryRuleState,
    LadderTakeProfitPhase,
    LadderTakeProfitRuleState,
)
from trading_strategies.strategy import CapitalAllocationSpec

from ..adapters import (
    CoinMLongTakeProfitLadderEventRouter,
    CoinMPositionSizer,
    StrategyApplicationSimulationAdapter,
)
from ..registry import (
    SimulationStrategyBinding,
    SimulationStrategyBuildContext,
)
from ._values import boolean, check_fields, decimal, integer, text


COINM_LONG_TAKE_PROFIT_LADDER_V1 = (
    "coinm-long-take-profit-ladder/v1"
)
LEGACY_COINM_LONG_LADDER_TYPES = (
    "effective-leverage-ladder-long/v1",
    "target-liquidation-ladder-long/v1",
)
_DEFAULTS: dict[str, object] = {
    "strategy_id": "btc-coinm-long-take-profit-ladder",
    "side": "LONG",
    "entry_timing": "NEXT_OPEN",
    "first_take_profit_ratio": "1.10",
    "take_profit_count": 10,
    "take_profit_spacing": "GEOMETRIC",
    "take_profit_quantity_mode": "EQUAL_CONTRACTS",
    "close_all_at_last_level": True,
    "tick_size": "0.1",
    "quantity_step": "1",
    "sizing_safety_buffer_ratio": "0",
}
_FIELDS = {
    "strategy_definition_type",
    "strategy_parameters",
    "strategy_id",
    "instrument",
    "side",
    "entry_timing",
    "entry_sizing_mode",
    "target_liquidation_price",
    "entry_effective_leverage",
    "first_take_profit_ratio",
    "take_profit_end_price",
    "take_profit_count",
    "take_profit_spacing",
    "take_profit_quantity_mode",
    "close_all_at_last_level",
    "tick_size",
    "quantity_step",
    "sizing_safety_buffer_ratio",
}
_OPTIONAL_FIELDS = {
    "strategy_parameters",
    "target_liquidation_price",
    "entry_effective_leverage",
}
_STRATEGY_PARAMETER_FIELDS = {
    "strategy_id",
    "instrument",
    "direction",
    "product_type",
    "entry_sizing_policy",
    "entry_sizing_parameters",
    "first_exit_ratio",
    "exit_end_price",
    "exit_level_count",
    "tick_size",
    "quantity_step",
    "quantity_unit",
}
_SIZING_PARAMETER_FIELDS = {
    "target_liquidation_price",
    "entry_effective_leverage",
    "sizing_safety_buffer_ratio",
}


def _nested_strategy_parameters(
    parameters: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    raw = parameters.get("strategy_parameters")
    if not isinstance(raw, Mapping):
        raise ValueError(
            "coinm-long-take-profit-ladder/v1.strategy_parameters "
            "must be an object"
        )
    nested = dict(raw)
    check_fields(
        nested,
        _STRATEGY_PARAMETER_FIELDS,
        context=(
            "coinm-long-take-profit-ladder/v1.strategy_parameters"
        ),
    )
    context = "coinm-long-take-profit-ladder/v1.strategy_parameters"
    direction = text(nested, "direction", context=context).upper()
    if direction != "LONG":
        raise ValueError(f"{context}.direction must be 'LONG'")
    product_type = text(nested, "product_type", context=context).upper()
    if product_type != "INVERSE_PERPETUAL":
        raise ValueError(
            f"{context}.product_type must be 'INVERSE_PERPETUAL'"
        )
    quantity_unit = text(nested, "quantity_unit", context=context)
    if quantity_unit != "contracts":
        raise ValueError(f"{context}.quantity_unit must be 'contracts'")
    sizing = nested["entry_sizing_parameters"]
    if not isinstance(sizing, Mapping):
        raise ValueError(f"{context}.entry_sizing_parameters must be an object")
    sizing_parameters = dict(sizing)
    check_fields(
        sizing_parameters,
        set(),
        optional=_SIZING_PARAMETER_FIELDS,
        context=f"{context}.entry_sizing_parameters",
    )
    translated: dict[str, object] = {
        "strategy_id": nested["strategy_id"],
        "instrument": nested["instrument"],
        "side": direction,
        "entry_sizing_mode": nested["entry_sizing_policy"],
        "first_take_profit_ratio": nested["first_exit_ratio"],
        "take_profit_end_price": nested["exit_end_price"],
        "take_profit_count": nested["exit_level_count"],
        "tick_size": nested["tick_size"],
        "quantity_step": nested["quantity_step"],
    }
    translated.update(sizing_parameters)
    return translated, {
        **nested,
        "direction": direction,
        "product_type": product_type,
        "quantity_unit": quantity_unit,
        "entry_sizing_parameters": sizing_parameters,
    }


class CoinMLongTakeProfitLadderSimulationPlugin:
    strategy_type = COINM_LONG_TAKE_PROFIT_LADDER_V1

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "strategy",
            "type": self.strategy_type,
            "aliases": list(LEGACY_COINM_LONG_LADDER_TYPES),
            "display_name": "COIN-M 阶梯止盈多头",
            "family": "BTC 建仓与退出",
            "version": "v1",
            "strategy_definition_type": ENTRY_THEN_LADDER_EXIT_V1,
            "strategy_parameter_mapping": {
                "strategy_id": "strategy_id",
                "instrument": "instrument",
                "entry_sizing_mode": "entry_sizing_policy",
                "target_liquidation_price": (
                    "entry_sizing_parameters.target_liquidation_price"
                ),
                "entry_effective_leverage": (
                    "entry_sizing_parameters.entry_effective_leverage"
                ),
                "first_take_profit_ratio": "first_exit_ratio",
                "take_profit_end_price": "exit_end_price",
                "take_profit_count": "exit_level_count",
                "tick_size": "tick_size",
                "quantity_step": "quantity_step",
            },
            "research_focus": {
                "primary_rule_key": TAKE_PROFIT_RULE_KEY,
                "primary_rule_type": "ladder-take-profit/v1",
            },
            "rule_composition": [
                {
                    "rule_key": ENTRY_RULE_KEY,
                    "rule_type": "initial-entry/v1",
                    "role": "配合规则",
                },
                {
                    "rule_key": TAKE_PROFIT_RULE_KEY,
                    "rule_type": "ladder-take-profit/v1",
                    "role": "核心规则",
                },
            ],
            "description": (
                "首次开立一个 COIN-M 多仓，再按几何价格阶梯逐级 "
                "reduce-only 止盈；建仓规模可按目标有效杠杆率或目标"
                "强平价确定。"
            ),
            "formulae": [
                "mode = EFFECTIVE_LEVERAGE: Q* = max{Q | L_eff(Q) ≤ L_target}",
                "mode = TARGET_LIQUIDATION_PRICE: Q* = max{Q | P_liq(Q) ≤ P_target}",
                "Pᵢ = P₀ × (P_end / P₀)^(i / (n - 1))",
            ],
            "parameters": [
                {
                    "key": "entry_sizing_mode",
                    "name": "建仓定量方式",
                    "required": True,
                },
                {
                    "key": "entry_effective_leverage",
                    "name": "目标有效杠杆率",
                    "conditional": "EFFECTIVE_LEVERAGE",
                },
                {
                    "key": "target_liquidation_price",
                    "name": "目标强平价",
                    "conditional": "TARGET_LIQUIDATION_PRICE",
                },
                {
                    "key": "first_take_profit_ratio",
                    "name": "首档止盈倍数",
                    "default": "1.10",
                },
                {
                    "key": "take_profit_end_price",
                    "name": "末档止盈价",
                    "required": True,
                },
                {
                    "key": "take_profit_count",
                    "name": "止盈档位数",
                    "default": 10,
                },
            ],
            "constraints": [
                "只开 COIN-M 多仓",
                "两种建仓定量方式必须且只能选择一种",
                "所有退出均为 reduce-only，最后一档清空余量",
                "不补仓、不重开",
            ],
            "flow": [
                {
                    "title": "确定仓位",
                    "detail": "按配置的有效杠杆率或目标强平价计算合约数",
                },
                {"title": "主动建仓", "detail": "在第一个可成交 open 买入"},
                {"title": "布置阶梯", "detail": "按成交价生成几何止盈意图"},
                {
                    "title": "逐级退出",
                    "detail": "K 线覆盖档位后 reduce-only 卖出",
                },
            ],
        }

    def resolve(self, component: ComponentSpec) -> ComponentSpec:
        raw = dict(component.parameters)
        if "strategy_definition_type" not in raw:
            raise ValueError(
                f"{self.strategy_type} requires strategy_definition_type"
            )
        declared_type = text(
            raw,
            "strategy_definition_type",
            context=self.strategy_type,
        )
        if "strategy_parameters" in raw:
            legacy_fields = (
                set(raw)
                - {"strategy_definition_type", "strategy_parameters"}
            )
            if legacy_fields:
                raise ValueError(
                    f"{self.strategy_type} cannot mix strategy_parameters "
                    f"with legacy parameters: {sorted(legacy_fields)}"
                )
            translated, strategy_parameters = _nested_strategy_parameters(raw)
            resolved = {
                **_DEFAULTS,
                **translated,
                "strategy_definition_type": declared_type,
                "strategy_parameters": strategy_parameters,
            }
        else:
            resolved = {**_DEFAULTS, **raw}
        return ComponentSpec(
            key=component.key,
            type=component.type,
            parameters=resolved,
        )

    def build(
        self,
        component: ComponentSpec,
        context: SimulationStrategyBuildContext,
    ) -> SimulationStrategyBinding:
        parameters = component.parameters
        check_fields(
            parameters,
            _FIELDS - _OPTIONAL_FIELDS,
            context=self.strategy_type,
            optional=_OPTIONAL_FIELDS,
        )
        for key, expected in (
            ("side", "LONG"),
            ("entry_timing", "NEXT_OPEN"),
            ("take_profit_spacing", "GEOMETRIC"),
            ("take_profit_quantity_mode", "EQUAL_CONTRACTS"),
        ):
            actual = text(parameters, key, context=self.strategy_type).upper()
            if actual != expected:
                raise ValueError(
                    f"{self.strategy_type}.{key} must be {expected!r}"
                )
        if not boolean(
            parameters,
            "close_all_at_last_level",
            context=self.strategy_type,
        ):
            raise ValueError("close_all_at_last_level must be true in v1")
        if context.market_type != "coinm":
            raise ValueError(f"{self.strategy_type} requires a COIN-M account")
        instrument = text(parameters, "instrument", context=self.strategy_type)
        if instrument != context.instrument:
            raise ValueError("strategy and account instruments must match")
        try:
            sizing_mode = EntrySizingMode(
                text(
                    parameters,
                    "entry_sizing_mode",
                    context=self.strategy_type,
                ).upper()
            )
        except ValueError as exc:
            allowed = ", ".join(item.value for item in EntrySizingMode)
            raise ValueError(
                f"{self.strategy_type}.entry_sizing_mode must be one of {allowed}"
            ) from exc
        quantity_step = decimal(
            parameters,
            "quantity_step",
            context=self.strategy_type,
        )
        config = CoinMLongTakeProfitLadderConfig(
            strategy_id=text(
                parameters,
                "strategy_id",
                context=self.strategy_type,
            ),
            instrument=instrument,
            entry_sizing_mode=sizing_mode,
            first_take_profit_ratio=decimal(
                parameters,
                "first_take_profit_ratio",
                context=self.strategy_type,
            ),
            take_profit_end_price=decimal(
                parameters,
                "take_profit_end_price",
                context=self.strategy_type,
            ),
            take_profit_count=integer(
                parameters,
                "take_profit_count",
                context=self.strategy_type,
            ),
            tick_size=decimal(
                parameters,
                "tick_size",
                context=self.strategy_type,
            ),
            quantity_step=quantity_step,
            target_liquidation_price=(
                decimal(
                    parameters,
                    "target_liquidation_price",
                    context=self.strategy_type,
                )
                if "target_liquidation_price" in parameters
                else None
            ),
            entry_effective_leverage=(
                decimal(
                    parameters,
                    "entry_effective_leverage",
                    context=self.strategy_type,
                )
                if "entry_effective_leverage" in parameters
                else None
            ),
            sizing_safety_buffer_ratio=decimal(
                parameters,
                "sizing_safety_buffer_ratio",
                context=self.strategy_type,
            ),
        )
        sizer = CoinMPositionSizer(
            instrument=instrument,
            ledger_factory=context.ledger_factory,
            margin_model=context.margin_model,
            fee_model=context.fee_model,
            quantity_step=quantity_step,
            settlement_asset=context.settlement_asset,
        )
        strategy_spec = CoinMLongTakeProfitLadderStrategyDefinition().bind(
            config
        )
        declared_definition_type = text(
            parameters,
            "strategy_definition_type",
            context=self.strategy_type,
        )
        if declared_definition_type != strategy_spec.strategy_type:
            raise ValueError(
                "declared strategy_definition_type does not match the "
                "StrategySpec produced by the simulation adapter"
            )
        account = context.ledger_factory()
        initial_equity = getattr(account, "initial_equity", None)
        if initial_equity is None:
            raise TypeError("account ledger must expose initial_equity")
        application = StrategyApplication.build(
            StrategyApplicationSpec(
                application_id=f"{config.strategy_id}:application",
                strategy_launches=(
                    StrategyLaunchSpec(
                        strategy_instance_id=config.strategy_id,
                        strategy_spec=strategy_spec,
                        capital_allocation=CapitalAllocationSpec(
                            settlement_asset=context.settlement_asset,
                            amount=initial_equity,
                        ),
                    ),
                ),
            ),
            lambda launch: build_coinm_long_take_profit_ladder_runtime(
                config,
                sizer,
            ),
        )
        sizing_tags = {"entry_sizing_mode": sizing_mode.value}
        if config.entry_effective_leverage is not None:
            sizing_tags["entry_effective_leverage"] = str(
                config.entry_effective_leverage
            )
        if config.target_liquidation_price is not None:
            sizing_tags["target_liquidation_price"] = str(
                config.target_liquidation_price
            )
        adapter = StrategyApplicationSimulationAdapter(
            application,
            event_router=CoinMLongTakeProfitLadderEventRouter(
                strategy_instance_id=config.strategy_id,
                position_sizer=sizer,
            ),
            contract_size=context.contract_size,
            settlement_asset=context.settlement_asset,
            external_strategy_type=self.strategy_type,
            instruction_tags=sizing_tags,
        )

        def summary(result) -> dict[str, object]:
            instance = application.instances[config.strategy_id]
            entry_state = instance.rule_state(ENTRY_RULE_KEY)
            ladder_state = instance.rule_state(TAKE_PROFIT_RULE_KEY)
            if not isinstance(entry_state, InitialEntryRuleState):
                raise TypeError("entry Rule state has an unexpected type")
            if not isinstance(ladder_state, LadderTakeProfitRuleState):
                raise TypeError("ladder Rule state has an unexpected type")
            entry_fill = entry_state.fill
            position_plan = (
                None if entry_fill is None else entry_fill.actual_position
            )
            actual_liquidation = (
                None
                if position_plan is None
                else position_plan.metadata["estimated_liquidation_price"]
            )
            actual_leverage = (
                None
                if position_plan is None
                else position_plan.metadata["effective_leverage"]
            )
            target_liquidation = config.target_liquidation_price
            target_leverage = config.entry_effective_leverage
            return {
                "strategy_type": self.strategy_type,
                "strategy_definition_type": strategy_spec.strategy_type,
                "strategy_id": config.strategy_id,
                "application_id": application.application_id,
                "strategy_instance_id": instance.strategy_instance_id,
                "strategy_spec_id": instance.spec.strategy_spec_id,
                "allocation_id": instance.allocation_id,
                "position_owner_id": instance.position_owner_id,
                "primary_rule_key": TAKE_PROFIT_RULE_KEY,
                "primary_rule_type": "ladder-take-profit/v1",
                "companion_strategy_count": 0,
                "entry_sizing_mode": config.entry_sizing_mode.value,
                "state": ladder_state.phase.value,
                "planned_entry_price": (
                    None
                    if entry_state.reference_price is None
                    else str(entry_state.reference_price)
                ),
                "actual_entry_price": (
                    None if entry_fill is None else str(entry_fill.price)
                ),
                "entry_contracts": (
                    "0" if entry_fill is None else str(entry_fill.quantity)
                ),
                "requested_entry_effective_leverage": (
                    None if target_leverage is None else str(target_leverage)
                ),
                "actual_entry_effective_leverage": (
                    None if actual_leverage is None else str(actual_leverage)
                ),
                "entry_effective_leverage_deviation_rate": (
                    None
                    if actual_leverage is None or target_leverage is None
                    else str(actual_leverage / target_leverage - 1)
                ),
                "target_liquidation_price": (
                    None
                    if target_liquidation is None
                    else str(target_liquidation)
                ),
                "estimated_liquidation_price_after_entry": (
                    None
                    if actual_liquidation is None
                    else str(actual_liquidation)
                ),
                "liquidation_target_deviation_rate": (
                    None
                    if actual_liquidation is None
                    or target_liquidation is None
                    else str(actual_liquidation / target_liquidation - 1)
                ),
                "take_profit_level_count": len(ladder_state.levels),
                "completed_take_profit_level_count": (
                    len(ladder_state.filled_intent_keys)
                ),
                "exited_contracts": str(
                    sum(
                        (
                            level.quantity
                            for level in ladder_state.levels
                            if level.intent_key
                            in ladder_state.filled_intent_keys
                        ),
                        0,
                    )
                ),
                "remaining_contracts": str(
                    0
                    if entry_fill is None
                    else entry_fill.quantity
                    - sum(
                        (
                            level.quantity
                            for level in ladder_state.levels
                            if level.intent_key
                            in ladder_state.filled_intent_keys
                        ),
                        0,
                    )
                ),
                "completed": (
                    ladder_state.phase == LadderTakeProfitPhase.COMPLETED
                ),
                "last_triggered_take_profit_level": (
                    max(
                        (
                            level.level
                            for level in ladder_state.levels
                            if level.intent_key
                            in ladder_state.filled_intent_keys
                        ),
                        default=None,
                    )
                ),
                "intent_count": len(result.intents),
                "instruction_count": len(result.instructions),
                "fill_count": len(result.fills),
                "research_focus": {
                    "primary_strategy_instance_id": instance.strategy_instance_id,
                    "primary_rule_key": TAKE_PROFIT_RULE_KEY,
                    "primary_rule_type": "ladder-take-profit/v1",
                },
                "application": adapter.application_document(result),
            }

        return SimulationStrategyBinding(
            strategy_type=self.strategy_type,
            instrument=instrument,
            trade_port=adapter,
            summary_reader=summary,
            descriptor=self.descriptor(),
        )
