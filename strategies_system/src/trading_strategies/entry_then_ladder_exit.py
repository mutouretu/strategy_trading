"""Product-neutral entry followed by ladder-exit StrategyDefinition."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from .kernel import TradeSide, TradingRuleSpec
from .kernel._values import freeze_mapping
from .rules import (
    InitialEntryRule,
    InitialEntryRuleConfig,
    LadderTakeProfitRule,
    LadderTakeProfitRuleConfig,
)
from .strategy import (
    CapitalAllocationSpec,
    ConflictResolution,
    InstrumentBinding,
    ProposalBatchMode,
    RulePermission,
    StrategyCoordinationPolicy,
    StrategyDefinition,
    StrategyDefinitionDescriptor,
    StrategyDirection,
    StrategyParameterDefinition,
    StrategyProductType,
    StrategyRuleCompositionDefinition,
    StrategyRuleSlotSpec,
    StrategySpec,
)
from .strategy.values import positive_decimal, required_text


ENTRY_THEN_LADDER_EXIT_V1 = "entry-then-ladder-exit/v1"
ENTRY_THEN_LADDER_EXIT_DISPLAY_NAME = "&".join(
    (
        InitialEntryRule.definition().display_name,
        LadderTakeProfitRule.definition().display_name,
    )
)
ENTRY_RULE_KEY = "entry"
EXIT_RULE_KEY = "take-profit"


@dataclass(frozen=True, slots=True)
class EntryThenLadderExitParameters:
    """External Strategy parameters before they are resolved to RuleConfig."""

    strategy_id: str
    instrument: str
    direction: StrategyDirection
    product_type: StrategyProductType
    entry_sizing_policy: str
    entry_sizing_parameters: Mapping[str, object] = field(default_factory=dict)
    first_exit_ratio: Decimal = Decimal("1.10")
    exit_end_price: Decimal = Decimal("150000")
    exit_level_count: int = 10
    tick_size: Decimal = Decimal("0.1")
    quantity_step: Decimal = Decimal("1")
    quantity_unit: str = "contracts"
    capital_allocation: CapitalAllocationSpec | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "strategy_id",
            "instrument",
            "entry_sizing_policy",
            "quantity_unit",
        ):
            object.__setattr__(
                self,
                field_name,
                required_text(field_name, getattr(self, field_name)),
            )
        if not isinstance(self.direction, StrategyDirection):
            raise TypeError("direction must be a StrategyDirection")
        if not isinstance(self.product_type, StrategyProductType):
            raise TypeError("product_type must be a StrategyProductType")
        if (
            self.product_type == StrategyProductType.SPOT
            and self.direction == StrategyDirection.SHORT
        ):
            raise ValueError("SPOT does not support a SHORT Strategy")
        object.__setattr__(
            self,
            "entry_sizing_parameters",
            freeze_mapping(self.entry_sizing_parameters),
        )
        for field_name in (
            "first_exit_ratio",
            "exit_end_price",
            "tick_size",
            "quantity_step",
        ):
            object.__setattr__(
                self,
                field_name,
                positive_decimal(field_name, getattr(self, field_name)),
            )
        if self.first_exit_ratio <= 1:
            raise ValueError("first_exit_ratio must be > 1")
        if (
            isinstance(self.exit_level_count, bool)
            or not isinstance(self.exit_level_count, int)
            or self.exit_level_count < 2
        ):
            raise ValueError("exit_level_count must be an integer >= 2")
        if self.capital_allocation is not None and not isinstance(
            self.capital_allocation,
            CapitalAllocationSpec,
        ):
            raise TypeError(
                "capital_allocation must be a CapitalAllocationSpec or None"
            )


class EntryThenLadderExitStrategyDefinition(
    StrategyDefinition[EntryThenLadderExitParameters]
):
    """Compose one initial-entry Rule and one ladder-exit Rule."""

    strategy_type = ENTRY_THEN_LADDER_EXIT_V1

    @classmethod
    def definition(cls) -> StrategyDefinitionDescriptor:
        policy = StrategyCoordinationPolicy(
            proposal_batch=ProposalBatchMode.ATOMIC_PER_EVENT,
            conflict_resolution=ConflictResolution.FAIL_FAST,
            exit_controller_rule_key=EXIT_RULE_KEY,
        )
        return StrategyDefinitionDescriptor(
            strategy_type=cls.strategy_type,
            display_name=ENTRY_THEN_LADDER_EXIT_DISPLAY_NAME,
            version="v1",
            family="position-entry-and-exit",
            summary=(
                "建立一次初始仓位，并在成交价上方或下方生成阶梯式减仓意图。"
            ),
            description=(
                "该 Strategy 只定义一次建仓与逐档退出的协作关系。标的、"
                "多空方向、产品类型、仓位计算方式和阶梯参数均由实例参数绑定；"
                "它不补仓、不重开，也不把 COIN-M 或多头写入策略身份。"
            ),
            parameters=(
                StrategyParameterDefinition(
                    "strategy_id",
                    "策略实例标识",
                    required=True,
                    group="identity",
                    maps_to=("StrategySpec.strategy_spec_id",),
                ),
                StrategyParameterDefinition(
                    "instrument",
                    "交易标的",
                    required=True,
                    group="binding",
                    maps_to=("InstrumentBinding.instrument",),
                ),
                StrategyParameterDefinition(
                    "direction",
                    "方向",
                    required=True,
                    choices=("LONG", "SHORT"),
                    group="binding",
                    maps_to=(
                        "InstrumentBinding.direction",
                        "entry.side",
                        "take-profit.side",
                    ),
                ),
                StrategyParameterDefinition(
                    "product_type",
                    "产品类型",
                    required=True,
                    choices=(
                        "SPOT",
                        "LINEAR_PERPETUAL",
                        "INVERSE_PERPETUAL",
                    ),
                    group="binding",
                    maps_to=("InstrumentBinding.product_type",),
                ),
                StrategyParameterDefinition(
                    "entry_sizing_policy",
                    "建仓定量策略",
                    "由应用层注册的仓位计算器引用。",
                    required=True,
                    group="entry",
                    maps_to=("entry.capabilities.position_sizer",),
                ),
                StrategyParameterDefinition(
                    "entry_sizing_parameters",
                    "建仓定量参数",
                    "由所选仓位计算器解释，不直接下发给 Rule。",
                    group="entry",
                    maps_to=("application.position_sizer.parameters",),
                ),
                StrategyParameterDefinition(
                    "first_exit_ratio",
                    "首档退出倍数",
                    required=True,
                    group="exit",
                    maps_to=("take-profit.first_take_profit_ratio",),
                ),
                StrategyParameterDefinition(
                    "exit_end_price",
                    "末档退出价",
                    required=True,
                    group="exit",
                    maps_to=("take-profit.end_price",),
                ),
                StrategyParameterDefinition(
                    "exit_level_count",
                    "退出档位数",
                    required=True,
                    group="exit",
                    maps_to=("take-profit.level_count",),
                ),
                StrategyParameterDefinition(
                    "tick_size",
                    "价格步长",
                    required=True,
                    group="product",
                    maps_to=("take-profit.tick_size",),
                ),
                StrategyParameterDefinition(
                    "quantity_step",
                    "数量步长",
                    required=True,
                    group="product",
                    maps_to=("take-profit.quantity_step",),
                ),
                StrategyParameterDefinition(
                    "quantity_unit",
                    "数量单位",
                    required=True,
                    group="product",
                    maps_to=("take-profit.quantity_unit",),
                ),
                StrategyParameterDefinition(
                    "capital_allocation",
                    "结算资产额度",
                    "可在 StrategySpec 中提供，也可在 Launch 时覆盖。",
                    group="capital",
                    maps_to=("StrategySpec.capital_allocation",),
                ),
            ),
            rule_composition=(
                StrategyRuleCompositionDefinition(
                    rule_key=ENTRY_RULE_KEY,
                    rule_type=InitialEntryRule.rule_type,
                    role="初始建仓",
                    summary="Strategy 启动后仅生成一次明确数量的 OPEN 意图。",
                    permissions=(RulePermission.OPEN.value,),
                    subscriptions=(
                        "InitialEntryStartInput",
                        "InitialEntryFillInput",
                    ),
                    parameter_mappings={
                        "direction": "side",
                        "entry_sizing_policy": "position_sizer capability",
                    },
                ),
                StrategyRuleCompositionDefinition(
                    rule_key=EXIT_RULE_KEY,
                    rule_type=LadderTakeProfitRule.rule_type,
                    role="阶梯退出",
                    summary=(
                        "建仓成交后生成 n 个 reduce-only 退出意图，并管理退出。"
                    ),
                    permissions=(
                        RulePermission.REDUCE.value,
                        RulePermission.CLOSE.value,
                        RulePermission.MANAGE_EXITS.value,
                    ),
                    subscriptions=(
                        "LadderPositionOpenedInput",
                        "LadderFillInput",
                        "LadderStopInput",
                    ),
                    parameter_mappings={
                        "direction": "side",
                        "first_exit_ratio": "first_take_profit_ratio",
                        "exit_end_price": "end_price",
                        "exit_level_count": "level_count",
                        "tick_size": "tick_size",
                        "quantity_step": "quantity_step",
                        "quantity_unit": "quantity_unit",
                    },
                ),
            ),
            coordination_policy=policy,
            supported_directions=("LONG", "SHORT"),
            supported_product_types=(
                "SPOT",
                "LINEAR_PERPETUAL",
                "INVERSE_PERPETUAL",
            ),
            lifecycle=(
                "START",
                "ENTRY_PENDING",
                "POSITION_OPEN",
                "PARTIALLY_EXITED",
                "COMPLETED",
            ),
            constraints=(
                "只建立一次初始仓位",
                "退出意图全部为 reduce-only",
                "最后一档退出剩余仓位",
                "不补仓、不重开",
                "SPOT 绑定不接受 SHORT 方向",
                "同一仓位池只有 take-profit Rule Slot 可以管理退出",
            ),
            aliases=("initial-entry-ladder-take-profit/v1",),
            primary_rule_key=EXIT_RULE_KEY,
        )

    def bind(self, parameters: EntryThenLadderExitParameters) -> StrategySpec:
        if not isinstance(parameters, EntryThenLadderExitParameters):
            raise TypeError(
                "parameters must be EntryThenLadderExitParameters"
            )
        entry_side = (
            TradeSide.BUY
            if parameters.direction == StrategyDirection.LONG
            else TradeSide.SELL
        )
        exit_side = (
            TradeSide.SELL
            if parameters.direction == StrategyDirection.LONG
            else TradeSide.BUY
        )
        return StrategySpec(
            strategy_spec_id=f"{parameters.strategy_id}:spec",
            strategy_type=self.strategy_type,
            display_name=ENTRY_THEN_LADDER_EXIT_DISPLAY_NAME,
            parameters={
                "strategy_id": parameters.strategy_id,
                "instrument": parameters.instrument,
                "direction": parameters.direction.value,
                "product_type": parameters.product_type.value,
                "entry_sizing_policy": parameters.entry_sizing_policy,
                "entry_sizing_parameters": parameters.entry_sizing_parameters,
                "first_exit_ratio": parameters.first_exit_ratio,
                "exit_end_price": parameters.exit_end_price,
                "exit_level_count": parameters.exit_level_count,
                "tick_size": parameters.tick_size,
                "quantity_step": parameters.quantity_step,
                "quantity_unit": parameters.quantity_unit,
            },
            binding=InstrumentBinding(
                instrument=parameters.instrument,
                direction=parameters.direction,
                product_type=parameters.product_type,
            ),
            rule_slots=(
                StrategyRuleSlotSpec(
                    rule_key=ENTRY_RULE_KEY,
                    rule=TradingRuleSpec(
                        rule_type=InitialEntryRule.rule_type,
                        config=InitialEntryRuleConfig(
                            intent_key="1",
                            side=entry_side,
                        ),
                        capability_references={
                            "position_sizer": parameters.entry_sizing_policy,
                        },
                    ),
                    permissions=(RulePermission.OPEN,),
                    subscriptions=(
                        "InitialEntryStartInput",
                        "InitialEntryFillInput",
                    ),
                ),
                StrategyRuleSlotSpec(
                    rule_key=EXIT_RULE_KEY,
                    rule=TradingRuleSpec(
                        rule_type=LadderTakeProfitRule.rule_type,
                        config=LadderTakeProfitRuleConfig(
                            first_take_profit_ratio=(
                                parameters.first_exit_ratio
                            ),
                            end_price=parameters.exit_end_price,
                            level_count=parameters.exit_level_count,
                            tick_size=parameters.tick_size,
                            quantity_step=parameters.quantity_step,
                            quantity_unit=parameters.quantity_unit,
                            side=exit_side,
                        ),
                    ),
                    permissions=(
                        RulePermission.REDUCE,
                        RulePermission.CLOSE,
                        RulePermission.MANAGE_EXITS,
                    ),
                    subscriptions=(
                        "LadderPositionOpenedInput",
                        "LadderFillInput",
                        "LadderStopInput",
                    ),
                ),
            ),
            coordination_policy=StrategyCoordinationPolicy(
                proposal_batch=ProposalBatchMode.ATOMIC_PER_EVENT,
                conflict_resolution=ConflictResolution.FAIL_FAST,
                exit_controller_rule_key=EXIT_RULE_KEY,
            ),
            capital_allocation=parameters.capital_allocation,
        )
