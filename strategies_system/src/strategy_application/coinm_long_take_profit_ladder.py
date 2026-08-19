"""Application-layer compatibility facade for the migrated Ladder Strategy."""

from __future__ import annotations

from decimal import Decimal

from trading_strategies.btc_accumulation.long_take_profit_ladder import (
    ENTRY_RULE_KEY,
    TAKE_PROFIT_RULE_KEY,
    CoinMLongTakeProfitLadderStrategyDefinition,
)
from trading_strategies.btc_accumulation.models import (
    CoinMLongTakeProfitLadderConfig,
    EntryPlan,
    EntrySizingMode,
    LadderState,
    PositionPlan,
    StrategyFill,
    StrategyOrderSide,
    StrategyRole,
    TakeProfitLevel,
)
from trading_strategies.btc_accumulation.ports import CoinMLongPositionSizer
from trading_strategies.kernel import TradeSide
from trading_strategies.rules import (
    InitialEntryFillInput,
    InitialEntryPhase,
    InitialEntryRule,
    InitialEntryRuleContext,
    InitialEntryRuleState,
    InitialEntryStartInput,
    LadderFillInput,
    LadderPositionOpenedInput,
    LadderTakeProfitPhase,
    LadderTakeProfitRule,
    LadderTakeProfitRuleState,
    SizedPosition,
)

from .application import StrategyRuntimeBinding
from .strategy_instance import StrategyInstance


class CoinMLongEntrySizer:
    def __init__(
        self,
        config: CoinMLongTakeProfitLadderConfig,
        position_sizer: CoinMLongPositionSizer,
    ) -> None:
        self._config = config
        self._position_sizer = position_sizer

    def size_entry(self, *, reference_price: Decimal) -> SizedPosition:
        config = self._config
        if config.entry_sizing_mode == EntrySizingMode.EFFECTIVE_LEVERAGE:
            assert config.entry_effective_leverage is not None
            plan = self._position_sizer.size_long_by_effective_leverage(
                entry_price=reference_price,
                effective_leverage=config.entry_effective_leverage,
            )
        else:
            assert config.target_liquidation_price is not None
            if reference_price <= config.target_liquidation_price:
                raise ValueError(
                    "entry_price must be above target_liquidation_price"
                )
            plan = self._position_sizer.size_long(
                entry_price=reference_price,
                target_liquidation_price=config.target_liquidation_price,
                safety_buffer_ratio=config.sizing_safety_buffer_ratio,
            )
        return to_sized_position(plan)


def to_sized_position(plan: PositionPlan) -> SizedPosition:
    return SizedPosition(
        quantity=plan.quantity,
        # Rule contracts use one stable semantic unit; exchange adapters may
        # report the same unit as CONTRACT, contracts, or contract.
        quantity_unit="contracts",
        metadata={
            "estimated_liquidation_price": plan.estimated_liquidation_price,
            "initial_margin": plan.initial_margin,
            "maintenance_margin": plan.maintenance_margin,
            "margin_buffer": plan.margin_buffer,
            "model_version": plan.model_version,
            "effective_leverage": plan.effective_leverage,
        },
    )


def to_position_plan(position: SizedPosition) -> PositionPlan:
    metadata = position.metadata
    return PositionPlan(
        quantity=position.quantity,
        quantity_unit=position.quantity_unit,
        estimated_liquidation_price=Decimal(
            metadata["estimated_liquidation_price"]
        ),
        initial_margin=Decimal(metadata["initial_margin"]),
        maintenance_margin=Decimal(metadata["maintenance_margin"]),
        margin_buffer=Decimal(metadata["margin_buffer"]),
        model_version=str(metadata["model_version"]),
        effective_leverage=(
            None
            if metadata["effective_leverage"] is None
            else Decimal(metadata["effective_leverage"])
        ),
    )


def build_coinm_long_take_profit_ladder_runtime(
    config: CoinMLongTakeProfitLadderConfig,
    position_sizer: CoinMLongPositionSizer,
) -> StrategyRuntimeBinding:
    """Build the Rule algorithms and pure calculation capabilities.

    Both the generic StrategyApplication adapter and the temporary legacy
    facade use this factory, so there is only one executable Rule graph.
    """

    return StrategyRuntimeBinding(
        rules={
            ENTRY_RULE_KEY: InitialEntryRule(),
            TAKE_PROFIT_RULE_KEY: LadderTakeProfitRule(),
        },
        contexts={
            ENTRY_RULE_KEY: InitialEntryRuleContext(
                position_sizer=CoinMLongEntrySizer(config, position_sizer)
            ),
            TAKE_PROFIT_RULE_KEY: None,
        },
    )


class CoinMLongTakeProfitLadderStrategy:
    """Legacy API backed by InitialEntryRule + LadderTakeProfitRule."""

    def __init__(
        self,
        config: CoinMLongTakeProfitLadderConfig,
        position_sizer: CoinMLongPositionSizer,
    ) -> None:
        self.config = config
        self.position_sizer = position_sizer
        self.definition = CoinMLongTakeProfitLadderStrategyDefinition()
        self.strategy_spec = self.definition.bind(config)
        runtime = build_coinm_long_take_profit_ladder_runtime(
            config,
            position_sizer,
        )
        self.strategy_instance = StrategyInstance(
            strategy_instance_id=config.strategy_id,
            spec=self.strategy_spec,
            rules=runtime.rules,
            contexts=runtime.contexts,
        )
        self.entry_plan: EntryPlan | None = None
        self.entry_fill: StrategyFill | None = None
        self.position_plan: PositionPlan | None = None
        self._processed_fill_ids: set[str] = set()
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            raise RuntimeError("strategy is already initialized")
        self.strategy_instance.start()
        self._initialized = True

    def plan_entry(self, entry_price: Decimal) -> EntryPlan:
        if self.state != LadderState.WAITING_ENTRY:
            raise RuntimeError("entry can be planned only while waiting")
        result = self.strategy_instance.apply_event(
            {
                ENTRY_RULE_KEY: InitialEntryStartInput(
                    reference_price=entry_price
                )
            }
        )
        if len(result.intents) != 1:
            raise RuntimeError("InitialEntryRule must produce exactly one intent")
        state = self._entry_state
        if state.planned_position is None or state.reference_price is None:
            raise RuntimeError("InitialEntryRule did not retain its plan")
        self.entry_plan = EntryPlan(
            intent_key=result.intents[0].intent_key,
            reference_price=state.reference_price,
            position=to_position_plan(state.planned_position),
        )
        return self.entry_plan

    def on_fill(
        self,
        fill: StrategyFill,
        *,
        actual_position_plan: PositionPlan | None = None,
    ) -> bool:
        if fill.fill_id in self._processed_fill_ids:
            return False
        if fill.role == StrategyRole.ENTRY:
            self._apply_entry_fill(fill, actual_position_plan)
        else:
            self._apply_take_profit_fill(fill)
        self._processed_fill_ids.add(fill.fill_id)
        return True

    def _apply_entry_fill(
        self,
        fill: StrategyFill,
        actual_position_plan: PositionPlan | None,
    ) -> None:
        if self.state != LadderState.ENTRY_PENDING or self.entry_plan is None:
            raise RuntimeError("entry fill is not expected")
        if fill.intent_key != self.entry_plan.intent_key:
            raise ValueError("entry fill references an unknown intent")
        if fill.side != StrategyOrderSide.BUY:
            raise ValueError("long entry fill must be BUY")
        plan = actual_position_plan or self.entry_plan.position
        if plan.quantity != fill.quantity:
            raise ValueError("actual position plan quantity must match fill")
        local_key = self.strategy_instance.owner_for_intent(
            fill.intent_key
        ).local_intent_key
        self.strategy_instance.apply_event(
            {
                ENTRY_RULE_KEY: InitialEntryFillInput(
                    fill_id=fill.fill_id,
                    intent_key=local_key,
                    side=TradeSide.BUY,
                    price=fill.price,
                    quantity=fill.quantity,
                    actual_position=to_sized_position(plan),
                ),
                TAKE_PROFIT_RULE_KEY: LadderPositionOpenedInput(
                    entry_price=fill.price,
                    position_quantity=fill.quantity,
                ),
            },
            consumed_intent_keys=(fill.intent_key,),
        )
        self.entry_fill = fill
        self.position_plan = plan

    def _apply_take_profit_fill(self, fill: StrategyFill) -> None:
        if fill.side != StrategyOrderSide.SELL:
            raise ValueError("long take-profit fill must be SELL")
        owner = self.strategy_instance.owner_for_intent(fill.intent_key)
        if owner.rule_key != TAKE_PROFIT_RULE_KEY:
            raise ValueError("take-profit fill references an unknown intent")
        self.strategy_instance.apply_event(
            {
                TAKE_PROFIT_RULE_KEY: LadderFillInput(
                    fill_id=fill.fill_id,
                    intent_key=owner.local_intent_key,
                    side=TradeSide.SELL,
                    price=fill.price,
                    quantity=fill.quantity,
                )
            },
            consumed_intent_keys=(fill.intent_key,),
        )

    @property
    def state(self) -> LadderState:
        if not self._initialized:
            return LadderState.NEW
        entry_phase = self._entry_state.phase
        if entry_phase == InitialEntryPhase.WAITING_START:
            return LadderState.WAITING_ENTRY
        if entry_phase == InitialEntryPhase.ENTRY_PENDING:
            return LadderState.ENTRY_PENDING
        ladder_phase = self._ladder_state.phase
        return {
            LadderTakeProfitPhase.WAITING_POSITION: LadderState.WAITING_ENTRY,
            LadderTakeProfitPhase.POSITION_OPEN: LadderState.POSITION_OPEN,
            LadderTakeProfitPhase.PARTIALLY_EXITED: (
                LadderState.PARTIALLY_EXITED
            ),
            LadderTakeProfitPhase.COMPLETED: LadderState.COMPLETED,
            LadderTakeProfitPhase.STOPPED: LadderState.COMPLETED,
        }[ladder_phase]

    @property
    def take_profit_levels(self) -> tuple[TakeProfitLevel, ...]:
        return tuple(
            TakeProfitLevel(
                level=level.level,
                intent_key=self.strategy_instance.global_intent_key(
                    TAKE_PROFIT_RULE_KEY,
                    level.intent_key,
                ),
                target_price=level.target_price,
                quantity=level.quantity,
            )
            for level in self._ladder_state.levels
        )

    @property
    def visible_take_profit_levels(self) -> tuple[TakeProfitLevel, ...]:
        return tuple(
            TakeProfitLevel(
                level=level.level,
                intent_key=self.strategy_instance.global_intent_key(
                    TAKE_PROFIT_RULE_KEY,
                    level.intent_key,
                ),
                target_price=level.target_price,
                quantity=level.quantity,
            )
            for level in self._ladder_state.visible_levels
        )

    @property
    def completed_take_profit_level_count(self) -> int:
        return len(self._ladder_state.filled_intent_keys)

    @property
    def exited_quantity(self) -> Decimal:
        filled = self._ladder_state.filled_intent_keys
        return sum(
            (
                level.quantity
                for level in self._ladder_state.levels
                if level.intent_key in filled
            ),
            Decimal("0"),
        )

    @property
    def remaining_quantity(self) -> Decimal:
        if self.entry_fill is None:
            return Decimal("0")
        return self.entry_fill.quantity - self.exited_quantity

    @property
    def last_triggered_take_profit_level(self) -> int | None:
        filled = self._ladder_state.filled_intent_keys
        levels = [
            level.level
            for level in self._ladder_state.levels
            if level.intent_key in filled
        ]
        return max(levels) if levels else None

    @property
    def _entry_state(self) -> InitialEntryRuleState:
        state = self.strategy_instance.rule_state(ENTRY_RULE_KEY)
        if not isinstance(state, InitialEntryRuleState):
            raise TypeError("entry Rule state has an unexpected type")
        return state

    @property
    def _ladder_state(self) -> LadderTakeProfitRuleState:
        state = self.strategy_instance.rule_state(TAKE_PROFIT_RULE_KEY)
        if not isinstance(state, LadderTakeProfitRuleState):
            raise TypeError("ladder Rule state has an unexpected type")
        return state
