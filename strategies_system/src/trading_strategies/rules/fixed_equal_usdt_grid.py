"""Fixed equal-USDT grid order planning as a registered TradingRule."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache

from grid_rule.fixed_equal_usdt import (
    FixedEntryMode,
    FixedCellSpec,
    FixedGridSpec,
    TriggerCell,
    build_fixed_cells,
    order_quantity,
    triggered_entry_ids,
)
from grid_rule.fixed_window import WindowCell, WindowInput, plan_fixed_window

from ..kernel import (
    PositionEffect,
    RuleConfigFieldDefinition,
    RuleIntentMode,
    RuleIntentProposal,
    RuleTransition,
    TradeSide,
    TradingRule,
    TradingRuleDefinition,
)


@lru_cache(maxsize=64)
def _fixed_cells(spec: FixedGridSpec) -> tuple[FixedCellSpec, ...]:
    """Reuse immutable grid geometry across high-frequency observations."""
    return build_fixed_cells(spec)


@dataclass(frozen=True, slots=True)
class FixedEqualUsdtGridRuleConfig:
    spec: FixedGridSpec
    entry_mode: FixedEntryMode
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal
    orders_per_side: int = 10
    rebalance_below: int = 5
    rebalance_above: int = 15
    quantity_unit: str = "BASE"

    def __post_init__(self) -> None:
        if not isinstance(self.spec, FixedGridSpec):
            raise TypeError("spec must be a FixedGridSpec")
        _fixed_cells(self.spec)
        if not isinstance(self.entry_mode, FixedEntryMode):
            raise TypeError("entry_mode must be a FixedEntryMode")
        for name in ("step_size", "min_qty", "min_notional"):
            value = Decimal(str(getattr(self, name)))
            if not value.is_finite() or value < 0 or (name == "step_size" and value == 0):
                raise ValueError(f"{name} must be finite and non-negative")
            object.__setattr__(self, name, value)
        if (self.orders_per_side < 1 or self.rebalance_below < 1
                or self.rebalance_below > self.orders_per_side
                or self.rebalance_above < self.orders_per_side):
            raise ValueError("invalid order window limits")
        if not self.quantity_unit.strip():
            raise ValueError("quantity_unit must not be empty")


@dataclass(frozen=True, slots=True)
class FixedGridObservedCell:
    cell_id: str
    stage: str
    open_qty: Decimal = Decimal("0")
    cycle_count: int = 0
    has_entry_order: bool = False
    has_exit_order: bool = False
    entry_ready: bool = True
    exit_ready: bool = True

    def __post_init__(self) -> None:
        if not self.cell_id:
            raise ValueError("cell_id must not be empty")
        if self.stage not in {
            "untriggered", "pending_entry", "pending_exit", "manual_review"
        }:
            raise ValueError("unknown cell stage")
        quantity = Decimal(str(self.open_qty))
        if not quantity.is_finite() or quantity < 0:
            raise ValueError("open_qty must be finite and non-negative")
        object.__setattr__(self, "open_qty", quantity)
        if self.cycle_count < 0:
            raise ValueError("cycle_count must be non-negative")


@dataclass(frozen=True, slots=True)
class FixedGridObservationInput:
    mark: Decimal
    cells: tuple[FixedGridObservedCell, ...]
    repair_ids: frozenset[str] = frozenset()
    repair_audit_passed: bool = False

    def __post_init__(self) -> None:
        mark = Decimal(str(self.mark))
        if not mark.is_finite() or mark <= 0:
            raise ValueError("mark must be finite and positive")
        object.__setattr__(self, "mark", mark)
        cells = tuple(self.cells)
        if len({cell.cell_id for cell in cells}) != len(cells):
            raise ValueError("observation contains duplicate cells")
        object.__setattr__(self, "cells", cells)
        object.__setattr__(self, "repair_ids", frozenset(self.repair_ids))


@dataclass(frozen=True, slots=True)
class FixedGridFillAcknowledgementInput:
    """Consume one fill identity while a same-frame batch is still pending."""

    fill_id: str

    def __post_init__(self) -> None:
        if not self.fill_id.strip():
            raise ValueError("fill_id must not be empty")


@dataclass(frozen=True, slots=True)
class FixedEqualUsdtGridRuleState:
    armed_ids: frozenset[str] = frozenset()
    entry_ids: frozenset[str] = frozenset()
    exit_ids: frozenset[str] = frozenset()
    initialized: bool = False
    intent_nonce: int = 0
    entry_intent_keys: tuple[tuple[str, str], ...] = ()
    exit_intent_keys: tuple[tuple[str, str], ...] = ()


class FixedEqualUsdtGridRule(
    TradingRule[
        FixedEqualUsdtGridRuleConfig,
        FixedGridObservationInput,
        FixedEqualUsdtGridRuleState,
        None,
    ]
):
    """Pure planner over exchange-confirmed Cell snapshots.

    Geometry, triggering, and order windows delegate to the same grid_rule
    functions used by the live engine. The runtime owns fills and capital.
    """

    rule_type = "fixed-equal-usdt-grid/v1"

    @classmethod
    def definition(cls) -> TradingRuleDefinition:
        return TradingRuleDefinition(
            rule_type=cls.rule_type,
            display_name="固定区间等 U 网格",
            version="v1",
            summary="按等比价格与等 U 预算生成单元格，选择近价买卖挂单。",
            description=(
                "根据已确认的单元格状态和标记价，决定触发、目标挂单和撤单。"
                "支持预建卖侧仓位与零预建仓的触价激活；实际建仓、成交、费用与持仓对账由运行时处理。"
            ),
            family="grid",
            input_types=(
                "FixedGridObservationInput",
                "FixedGridFillAcknowledgementInput",
            ),
            config_fields=(
                RuleConfigFieldDefinition("lower_price", "价格下限", required=True),
                RuleConfigFieldDefinition("upper_price", "价格上限", required=True),
                RuleConfigFieldDefinition("grid_count", "单元格数量", required=True),
                RuleConfigFieldDefinition("grid_budget_usdt", "总预算 U", required=True),
                RuleConfigFieldDefinition("tick_size", "价格精度", required=True),
                RuleConfigFieldDefinition("step_size", "数量精度", required=True),
                RuleConfigFieldDefinition("min_qty", "最小委托数量", required=True),
                RuleConfigFieldDefinition("min_notional", "最小委托名义金额", required=True),
                RuleConfigFieldDefinition(
                    "entry_mode", "启动模式", default="seeded",
                    choices=("seeded", "price_triggered"),
                ),
                RuleConfigFieldDefinition(
                    "orders_per_side", "每侧挂单目标", default=10,
                ),
                RuleConfigFieldDefinition(
                    "rebalance_below", "单侧补单阈值", default=5,
                ),
                RuleConfigFieldDefinition(
                    "rebalance_above", "单侧清理阈值", default=15,
                ),
            ),
            output_types=("RuleIntentProposal", "RuleTransition"),
            supported_directions=("LONG",),
            supported_product_types=("LINEAR_PERPETUAL",),
            lifecycle=("UNTRIGGERED", "PENDING_ENTRY", "PENDING_EXIT"),
            formulae=(
                "价格边界 p_i = floor_tick(L × (U/L)^(i/N))",
                "每格预算约为总预算 / N；买单数量 = floor_step(格预算 / 买价)",
                "触价激活条件：mark >= 单元格买价；目标窗口优先最近价格",
            ),
            constraints=(
                "边界固定，不随行情移动",
                "Rule 只计划订单，运行时确认成交、撤单、价格带与仓位",
                "预建仓模式的卖侧种子仓位须先由运行时实际建仓",
                "资金费、手续费与自动复投不在本规则内",
            ),
        )

    def initial_state(
        self, rule_config: FixedEqualUsdtGridRuleConfig, context: None,
    ) -> FixedEqualUsdtGridRuleState:
        del context
        if not isinstance(rule_config, FixedEqualUsdtGridRuleConfig):
            raise TypeError("rule_config must be FixedEqualUsdtGridRuleConfig")
        return FixedEqualUsdtGridRuleState()

    def transition(
        self,
        rule_config: FixedEqualUsdtGridRuleConfig,
        state: FixedEqualUsdtGridRuleState,
        input: FixedGridObservationInput | FixedGridFillAcknowledgementInput,
        context: None,
    ) -> RuleTransition[FixedEqualUsdtGridRuleState]:
        del context
        if not isinstance(state, FixedEqualUsdtGridRuleState):
            raise TypeError("state must be FixedEqualUsdtGridRuleState")
        if isinstance(input, FixedGridFillAcknowledgementInput):
            return RuleTransition.unchanged(state)
        if not isinstance(input, FixedGridObservationInput):
            raise TypeError("input must be FixedGridObservationInput")
        specs = _fixed_cells(rule_config.spec)
        spec_by_id = {cell.cell_id: cell for cell in specs}
        observed = {cell.cell_id: cell for cell in input.cells}
        if observed.keys() != spec_by_id.keys():
            raise ValueError("observation must contain exactly the configured cells")

        untriggered = {
            cell.cell_id for cell in input.cells if cell.stage == "untriggered"
        }
        armed = state.armed_ids & untriggered
        newly_armed = frozenset()
        if rule_config.entry_mode == FixedEntryMode.PRICE_TRIGGERED:
            newly_armed = triggered_entry_ids(
                tuple(TriggerCell(cell.cell_id, cell.buy_price)
                      for cell in specs if cell.cell_id in untriggered),
                input.mark,
                already_armed=armed,
            )
            armed |= newly_armed
        else:
            armed = frozenset()

        window_cells = tuple(
            WindowCell(
                cell_id=spec.cell_id,
                index=spec.index,
                buy_price=spec.buy_price,
                sell_price=spec.sell_price,
                stage=("pending_entry"
                       if spec.cell_id in armed else observed[spec.cell_id].stage),
                open_qty=observed[spec.cell_id].open_qty,
                has_entry_order=observed[spec.cell_id].has_entry_order,
                has_exit_order=observed[spec.cell_id].has_exit_order,
                entry_ready=observed[spec.cell_id].entry_ready,
                exit_ready=observed[spec.cell_id].exit_ready,
            )
            for spec in specs
        )
        decision = plan_fixed_window(WindowInput(
            cells=window_cells,
            mark=input.mark,
            prior_entry_ids=state.entry_ids,
            prior_exit_ids=state.exit_ids,
            repair_ids=input.repair_ids,
            repair_audit_passed=input.repair_audit_passed,
            initialized=state.initialized,
            entry_mode=rule_config.entry_mode,
            orders_per_side=rule_config.orders_per_side,
            rebalance_below=rule_config.rebalance_below,
            rebalance_above=rule_config.rebalance_above,
        ))
        intents: list[RuleIntentProposal] = []
        cancellations: list[str] = []
        entry_keys = dict(state.entry_intent_keys)
        exit_keys = dict(state.exit_intent_keys)
        nonce = state.intent_nonce
        for spec in specs:
            cell = observed[spec.cell_id]
            stage = ("pending_entry" if spec.cell_id in armed else cell.stage)
            entry_key = entry_keys.get(spec.cell_id)
            exit_key = exit_keys.get(spec.cell_id)
            if cell.has_entry_order and entry_key is None:
                raise ValueError("active entry order has no Rule intent identity")
            if cell.has_exit_order and exit_key is None:
                raise ValueError("active exit order has no Rule intent identity")
            if not cell.has_entry_order:
                entry_keys.pop(spec.cell_id, None)
            if not cell.has_exit_order:
                exit_keys.pop(spec.cell_id, None)
            if cell.has_entry_order and spec.cell_id not in decision.entry_ids:
                assert entry_key is not None
                cancellations.append(entry_key)
                entry_keys.pop(spec.cell_id, None)
            if cell.has_exit_order and spec.cell_id not in decision.exit_ids:
                assert exit_key is not None
                cancellations.append(exit_key)
                exit_keys.pop(spec.cell_id, None)
            if (stage == "pending_entry" and not cell.has_entry_order
                    and spec.cell_id in decision.entry_ids and cell.entry_ready):
                quantity = order_quantity(
                    spec,
                    step_size=rule_config.step_size,
                    min_qty=rule_config.min_qty,
                    min_notional=rule_config.min_notional,
                )
                if quantity > 0:
                    nonce += 1
                    entry_key = self._intent_key(
                        spec.cell_id, cell.cycle_count, "entry", nonce,
                    )
                    entry_keys[spec.cell_id] = entry_key
                    intents.append(self._intent(
                        entry_key, TradeSide.BUY, quantity, spec.buy_price,
                        rule_config.quantity_unit, spec.cell_id, cell.cycle_count,
                    ))
            if (stage == "pending_exit" and not cell.has_exit_order
                    and spec.cell_id in decision.exit_ids and cell.exit_ready
                    and cell.open_qty > 0):
                nonce += 1
                exit_key = self._intent_key(
                    spec.cell_id, cell.cycle_count, "exit", nonce,
                )
                exit_keys[spec.cell_id] = exit_key
                intents.append(self._intent(
                    exit_key, TradeSide.SELL, cell.open_qty, spec.sell_price,
                    rule_config.quantity_unit, spec.cell_id, cell.cycle_count,
                ))
        return RuleTransition(
            next_state=FixedEqualUsdtGridRuleState(
                armed_ids=frozenset(armed),
                entry_ids=decision.entry_ids,
                exit_ids=decision.exit_ids,
                initialized=True,
                intent_nonce=nonce,
                entry_intent_keys=tuple(sorted(entry_keys.items())),
                exit_intent_keys=tuple(sorted(exit_keys.items())),
            ),
            intents=tuple(intents),
            cancellations=tuple(cancellations),
            observations={
                "newly_armed": len(newly_armed),
                "entry_target": len(decision.entry_ids),
                "exit_target": len(decision.exit_ids),
                "rebalance": decision.rebalance,
            },
        )

    @staticmethod
    def _intent_key(
        cell_id: str, cycle_count: int, side: str, nonce: int,
    ) -> str:
        return f"{cell_id}:cycle:{cycle_count}:{side}:attempt:{nonce}"

    @staticmethod
    def _intent(
        key: str, side: TradeSide, quantity: Decimal, price: Decimal,
        unit: str, cell_id: str, cycle_count: int,
    ) -> RuleIntentProposal:
        reducing = side == TradeSide.SELL
        return RuleIntentProposal(
            intent_key=key,
            side=side,
            quantity=quantity,
            quantity_unit=unit,
            position_effect=(PositionEffect.REDUCE if reducing
                             else PositionEffect.INCREASE),
            intent_mode=RuleIntentMode.PASSIVE,
            target_price=price,
            reduce_only=reducing,
            role="grid_exit" if reducing else "grid_entry",
            tags={"cell_id": cell_id, "cycle_count": cycle_count},
        )
