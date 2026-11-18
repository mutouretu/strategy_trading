"""One fixed-range equal-USDT grid Strategy composed from the grid Rule."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from grid_rule.fixed_equal_usdt import FixedEntryMode, FixedGridSpec

from .kernel import TradeSide, TradingRuleSpec
from .rules import (
    FixedEqualUsdtGridRule,
    FixedEqualUsdtGridRuleConfig,
    InitialEntryRule,
    InitialEntryRuleConfig,
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


FIXED_EQUAL_USDT_GRID_STRATEGY_V1 = "fixed-range-equal-usdt-grid/v1"
GRID_RULE_KEY = "grid"
SEED_RULE_KEY = "seed"


@dataclass(frozen=True, slots=True)
class FixedEqualUsdtGridStrategyParameters:
    strategy_id: str
    instrument: str
    lower_price: Decimal
    upper_price: Decimal
    grid_count: int
    grid_budget_usdt: Decimal
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal
    entry_mode: FixedEntryMode = FixedEntryMode.SEEDED
    orders_per_side: int = 10
    rebalance_below: int = 5
    rebalance_above: int = 15
    capital_allocation: CapitalAllocationSpec | None = None

    def __post_init__(self) -> None:
        for name in ("strategy_id", "instrument"):
            object.__setattr__(self, name, required_text(name, getattr(self, name)))
        for name in ("lower_price", "upper_price", "grid_budget_usdt", "tick_size", "step_size"):
            object.__setattr__(self, name, positive_decimal(name, getattr(self, name)))
        for name in ("min_qty", "min_notional"):
            value = Decimal(str(getattr(self, name)))
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
            object.__setattr__(self, name, value)
        if (isinstance(self.grid_count, bool) or not isinstance(self.grid_count, int)
                or self.grid_count < 1):
            raise ValueError("grid_count must be a positive integer")
        if not isinstance(self.entry_mode, FixedEntryMode):
            raise TypeError("entry_mode must be a FixedEntryMode")
        FixedEqualUsdtGridRuleConfig(
            FixedGridSpec(
                self.strategy_id, self.lower_price, self.upper_price,
                self.grid_count, self.grid_budget_usdt, self.tick_size,
            ),
            self.entry_mode, self.step_size, self.min_qty, self.min_notional,
            self.orders_per_side, self.rebalance_below, self.rebalance_above,
        )
        if self.capital_allocation is not None:
            if not isinstance(self.capital_allocation, CapitalAllocationSpec):
                raise TypeError("capital_allocation must be a CapitalAllocationSpec")
            if (self.capital_allocation.settlement_asset != "USDT"
                    or self.capital_allocation.amount < self.grid_budget_usdt):
                raise ValueError("USDT allocation must cover the grid budget")


class FixedEqualUsdtGridStrategyDefinition(
    StrategyDefinition[FixedEqualUsdtGridStrategyParameters]
):
    """Compose seed-entry and fixed-grid Rules under one fixed budget."""

    strategy_type = FIXED_EQUAL_USDT_GRID_STRATEGY_V1

    @staticmethod
    def _policy() -> StrategyCoordinationPolicy:
        return StrategyCoordinationPolicy(
            proposal_batch=ProposalBatchMode.ATOMIC_PER_EVENT,
            conflict_resolution=ConflictResolution.FAIL_FAST,
            exit_controller_rule_key=GRID_RULE_KEY,
            allow_simultaneous_entry_exit=True,
        )

    @classmethod
    def definition(cls) -> StrategyDefinitionDescriptor:
        fields = (
            ("strategy_id", "策略实例标识", "identity", "StrategySpec.strategy_spec_id"),
            ("instrument", "合约", "binding", "InstrumentBinding.instrument"),
            ("lower_price", "价格下限", "grid", "grid.lower_price"),
            ("upper_price", "价格上限", "grid", "grid.upper_price"),
            ("grid_count", "单元格数量", "grid", "grid.grid_count"),
            ("grid_budget_usdt", "总预算 U", "capital", "grid.grid_budget_usdt"),
            ("tick_size", "价格精度", "product", "grid.tick_size"),
            ("step_size", "数量精度", "product", "grid.step_size"),
            ("min_qty", "最小委托数量", "product", "grid.min_qty"),
            ("min_notional", "最小委托名义金额", "product", "grid.min_notional"),
        )
        return StrategyDefinitionDescriptor(
            strategy_type=cls.strategy_type,
            display_name="固定区间等 U 网格策略",
            version="v1",
            family="fixed-grid",
            summary="用一条固定等 U 网格 Rule 管理长期区间内的低买高卖。",
            description=(
                "Strategy 绑定标的、固定边界、预算与启动模式；初始建仓 Rule 只负责预建仓，"
                "Grid Rule 负责单元格触发和买卖挂单计划。成交和账户由运行时负责。"
            ),
            parameters=(
                *(
                    StrategyParameterDefinition(
                        key, label, required=True, group=group,
                        maps_to=(target,),
                    )
                    for key, label, group, target in fields
                ),
                StrategyParameterDefinition(
                    "entry_mode", "启动模式", default="seeded",
                    choices=("seeded", "price_triggered"),
                    group="grid", maps_to=("grid.entry_mode",),
                ),
                StrategyParameterDefinition(
                    "orders_per_side", "每侧目标挂单数", default=10,
                    group="window", maps_to=("grid.orders_per_side",),
                ),
                StrategyParameterDefinition(
                    "rebalance_below", "单侧补单阈值", default=5,
                    group="window", maps_to=("grid.rebalance_below",),
                ),
                StrategyParameterDefinition(
                    "rebalance_above", "单侧清理阈值", default=15,
                    group="window", maps_to=("grid.rebalance_above",),
                ),
                StrategyParameterDefinition(
                    "capital_allocation", "USDT 资金额度", group="capital",
                    maps_to=("StrategySpec.capital_allocation",),
                ),
            ),
            rule_composition=(
                StrategyRuleCompositionDefinition(
                    rule_key=SEED_RULE_KEY,
                    rule_type=InitialEntryRule.rule_type,
                    role="预建仓",
                    summary="预建仓模式按当前卖侧单元格的总数量建立一次多仓。",
                    permissions=(RulePermission.OPEN.value,),
                    subscriptions=("InitialEntryStartInput", "InitialEntryFillInput"),
                    parameter_mappings={"entry_mode": "seed activation"},
                ),
                StrategyRuleCompositionDefinition(
                    rule_key=GRID_RULE_KEY,
                    rule_type=FixedEqualUsdtGridRule.rule_type,
                    role="单元格买卖规划",
                    summary="从已确认单元格状态生成近价买卖意图和撤单计划。",
                    permissions=(
                        RulePermission.INCREASE.value,
                        RulePermission.REDUCE.value,
                        RulePermission.MANAGE_EXITS.value,
                    ),
                    subscriptions=(
                        "FixedGridObservationInput",
                        "FixedGridFillAcknowledgementInput",
                    ),
                    parameter_mappings={
                        "lower_price": "spec.lower_price",
                        "upper_price": "spec.upper_price",
                        "grid_count": "spec.grid_count",
                        "grid_budget_usdt": "spec.grid_budget_usdt",
                        "entry_mode": "entry_mode",
                    },
                ),
            ),
            coordination_policy=cls._policy(),
            supported_directions=("LONG",),
            supported_product_types=("LINEAR_PERPETUAL",),
            lifecycle=("CREATED", "ACTIVE", "STOPPED"),
            constraints=(
                "网格边界固定",
                "Seed Rule 仅在预建仓模式启动；触发式模式不发送 Seed 事件",
                "Grid Rule 同时负责买入和 reduce-only 卖出",
                "同次观测允许买卖两侧意图并存",
                "仅已确认的 Cell 仓位可生成卖单",
            ),
            primary_rule_key=GRID_RULE_KEY,
        )

    def bind(self, parameters: FixedEqualUsdtGridStrategyParameters) -> StrategySpec:
        if not isinstance(parameters, FixedEqualUsdtGridStrategyParameters):
            raise TypeError("parameters must be FixedEqualUsdtGridStrategyParameters")
        config = FixedEqualUsdtGridRuleConfig(
            FixedGridSpec(
                parameters.strategy_id,
                parameters.lower_price, parameters.upper_price,
                parameters.grid_count, parameters.grid_budget_usdt,
                parameters.tick_size,
            ),
            parameters.entry_mode,
            parameters.step_size,
            parameters.min_qty,
            parameters.min_notional,
            parameters.orders_per_side,
            parameters.rebalance_below,
            parameters.rebalance_above,
        )
        allocation = parameters.capital_allocation or CapitalAllocationSpec(
            "USDT", parameters.grid_budget_usdt,
        )
        return StrategySpec(
            strategy_spec_id=f"{parameters.strategy_id}:spec",
            strategy_type=self.strategy_type,
            display_name=self.definition().display_name,
            parameters={
                "strategy_id": parameters.strategy_id,
                "instrument": parameters.instrument,
                "lower_price": parameters.lower_price,
                "upper_price": parameters.upper_price,
                "grid_count": parameters.grid_count,
                "grid_budget_usdt": parameters.grid_budget_usdt,
                "tick_size": parameters.tick_size,
                "step_size": parameters.step_size,
                "min_qty": parameters.min_qty,
                "min_notional": parameters.min_notional,
                "entry_mode": parameters.entry_mode.value,
                "orders_per_side": parameters.orders_per_side,
                "rebalance_below": parameters.rebalance_below,
                "rebalance_above": parameters.rebalance_above,
            },
            binding=InstrumentBinding(
                parameters.instrument,
                StrategyDirection.LONG,
                StrategyProductType.LINEAR_PERPETUAL,
            ),
            rule_slots=(
                StrategyRuleSlotSpec(
                    rule_key=SEED_RULE_KEY,
                    rule=TradingRuleSpec(
                        InitialEntryRule.rule_type,
                        InitialEntryRuleConfig(
                            intent_key="initial-grid-seed",
                            side=TradeSide.BUY,
                            role="grid_seed",
                        ),
                    ),
                    permissions=(RulePermission.OPEN,),
                    subscriptions=("InitialEntryStartInput", "InitialEntryFillInput"),
                ),
                StrategyRuleSlotSpec(
                    rule_key=GRID_RULE_KEY,
                    rule=TradingRuleSpec(FixedEqualUsdtGridRule.rule_type, config),
                    permissions=(
                        RulePermission.INCREASE,
                        RulePermission.REDUCE,
                        RulePermission.MANAGE_EXITS,
                    ),
                    subscriptions=(
                        "FixedGridObservationInput",
                        "FixedGridFillAcknowledgementInput",
                    ),
                ),
            ),
            coordination_policy=self._policy(),
            capital_allocation=allocation,
        )
