from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Callable, cast

from market_protocol import MarketFrame, MarketSource

from .fees import (
    FeeModel,
    FeeResult,
    ZeroFeeModel,
    default_liquidity_role,
)
from .funding import (
    FundingModel,
    FundingSettlement,
    ZeroFundingModel,
)
from .ledger import LinearLedger, SimulationLedger
from .margin import (
    LiquidationEvent,
    MarkPriceSampling,
    MarginModel,
    MarginSnapshot,
    NoMarginModel,
)
from .models import (
    EquitySnapshot,
    IntentRecord,
    IntentSnapshot,
    IntentStatus,
    OrderSide,
    SimFill,
    SimulationResult,
    SimulationTerminationReason,
    TradeInstruction,
)
from .slippage import (
    NoSlippageModel,
    SlippageModel,
)
from .trace import SimulationTracePort
from .trade import SimulationTradePort


@dataclass(slots=True)
class _IntentRecordState:
    intent: IntentSnapshot
    active_from_sequence: int
    active_to_sequence: int | None = None
    status: IntentStatus = IntentStatus.WAITING


class ReduceOnlyViolationError(ValueError):
    """A reduce-only instruction would not strictly reduce a position."""


class InsufficientMarginError(RuntimeError):
    """A fill that opens exposure would exceed available margin."""

    def __init__(
        self,
        instruction: TradeInstruction,
        projected_snapshot: MarginSnapshot,
    ) -> None:
        self.instruction = instruction
        self.projected_snapshot = projected_snapshot
        super().__init__(
            "insufficient margin for instruction: "
            f"instruction_key={instruction.instruction_key}, "
            f"source_intent_key={instruction.source_intent_key}, "
            f"instrument={instruction.instrument}, "
            "projected_margin_balance="
            f"{projected_snapshot.margin_balance}, "
            "projected_position_initial_margin="
            f"{projected_snapshot.position_initial_margin}, "
            "projected_available_balance="
            f"{projected_snapshot.available_balance}"
        )


class SimulationRunner:
    """Apply explicit current-frame trades and record their account effects."""

    def __init__(
        self,
        source: MarketSource,
        trade_port: SimulationTradePort,
        *,
        trace_port: SimulationTracePort | None = None,
        initial_equity: Decimal = Decimal("0"),
        ledger_factory: Callable[[], SimulationLedger] | None = None,
        fee_model: FeeModel | None = None,
        slippage_model: SlippageModel | None = None,
        funding_model: FundingModel | None = None,
        margin_model: MarginModel | None = None,
        mark_price_sampling: MarkPriceSampling = (
            MarkPriceSampling.CLOSE_ONLY
        ),
    ) -> None:
        self.source = source
        self.trade_port = trade_port
        if (
            trace_port is None
            and callable(getattr(trade_port, "visible_intents", None))
        ):
            trace_port = cast(SimulationTracePort, trade_port)
        self.trace_port = trace_port
        self.initial_equity = Decimal(initial_equity)
        if ledger_factory is not None and self.initial_equity != 0:
            raise ValueError(
                "initial_equity must be zero when ledger_factory is supplied"
            )
        self.ledger_factory = ledger_factory
        self.fee_model = fee_model or ZeroFeeModel()
        self.slippage_model = slippage_model or NoSlippageModel()
        self.funding_model = funding_model or ZeroFundingModel()
        self.margin_model = margin_model or NoMarginModel()
        if not isinstance(mark_price_sampling, MarkPriceSampling):
            raise TypeError(
                "mark_price_sampling must be a MarkPriceSampling"
            )
        self.mark_price_sampling = mark_price_sampling

    def run(
        self,
        *,
        seed: int | None = None,
        max_frames: int | None = None,
    ) -> SimulationResult:
        if max_frames is not None and max_frames < 1:
            raise ValueError("max_frames must be >= 1")

        first = self.source.reset(seed)
        frames = [first]
        fills: list[SimFill] = []
        marks = {first.instrument: first.price}
        ledger = (
            self.ledger_factory()
            if self.ledger_factory is not None
            else LinearLedger(self.initial_equity)
        )
        used_instruction_keys: set[str] = set()
        all_instructions: list[TradeInstruction] = []
        active_intents: dict[str, IntentSnapshot] = {}
        intent_states: list[_IntentRecordState] = []
        intent_state_by_key: dict[str, _IntentRecordState] = {}
        margin_snapshots: list[MarginSnapshot] = []
        account_events: list[LiquidationEvent] = []
        funding_events: list[FundingSettlement] = []
        liquidation_event: LiquidationEvent | None = None

        first_margin: MarginSnapshot | None = None
        if self.mark_price_sampling == MarkPriceSampling.ADVERSE_EXTREME:
            opening_margin = self._margin_snapshot(
                first,
                ledger,
                mark_price=first.open,
            )
            if (
                opening_margin is not None
                and opening_margin.liquidation_triggered
            ):
                first_margin = opening_margin
            else:
                adverse_margin = self._adverse_margin_snapshot(
                    first,
                    ledger,
                )
                if (
                    adverse_margin is not None
                    and adverse_margin.liquidation_triggered
                ):
                    first_margin = adverse_margin
        if first_margin is None:
            first_margin = self._margin_snapshot(first, ledger)
        if (
            first_margin is not None
            and first_margin.liquidation_triggered
        ):
            margin_snapshots.append(first_margin)
            liquidation_event = self._liquidation_event(first_margin)
            account_events.append(liquidation_event)
            marks[first.instrument] = first_margin.mark_price

        if liquidation_event is None:
            first_funding = self._settle_funding(
                first,
                ledger,
                marks,
            )
            if first_funding is not None:
                funding_events.append(first_funding)
                first_margin = self._margin_snapshot(first, ledger)
            if first_margin is not None:
                margin_snapshots.append(first_margin)
                if first_margin.liquidation_triggered:
                    liquidation_event = self._liquidation_event(
                        first_margin
                    )
                    account_events.append(liquidation_event)
                    marks[first.instrument] = first_margin.mark_price

        if liquidation_event is None:
            self.trade_port.initialize(first)
            if self.trace_port is not None:
                active_intents = self._synchronize_intents(
                    active_intents,
                    tuple(self.trace_port.visible_intents()),
                    current_sequence=first.sequence,
                    filled_intent_keys=set(),
                    intent_states=intent_states,
                    intent_state_by_key=intent_state_by_key,
                )
        equity_curve = [self._snapshot(first, marks, ledger)]

        while (
            liquidation_event is None
            and not self.source.done
            and (
                max_frames is None
                or len(frames) < max_frames
            )
        ):
            current = self.source.next()
            frames.append(current)
            marks[current.instrument] = current.price

            if self.mark_price_sampling == MarkPriceSampling.ADVERSE_EXTREME:
                opening_margin = self._margin_snapshot(
                    current,
                    ledger,
                    mark_price=current.open,
                )
                if (
                    opening_margin is not None
                    and opening_margin.liquidation_triggered
                ):
                    margin_snapshots.append(opening_margin)
                    liquidation_event = self._liquidation_event(
                        opening_margin
                    )
                    account_events.append(liquidation_event)
                    marks[current.instrument] = opening_margin.mark_price
                    equity_curve.append(
                        self._snapshot(current, marks, ledger)
                    )
                    break

            pre_fill_adverse_margin = (
                self._adverse_margin_snapshot(current, ledger)
                if (
                    self.mark_price_sampling
                    == MarkPriceSampling.ADVERSE_EXTREME
                )
                else None
            )
            instructions = self._prepare_instructions(
                current,
                tuple(self.trade_port.instructions_for(current)),
                used_instruction_keys,
            )
            if self.trace_port is not None:
                self._validate_instruction_sources(
                    instructions,
                    active_intents,
                )
            if (
                pre_fill_adverse_margin is not None
                and pre_fill_adverse_margin.liquidation_triggered
            ):
                margin_snapshots.append(pre_fill_adverse_margin)
                liquidation_event = self._liquidation_event(
                    pre_fill_adverse_margin,
                    intrabar_ordering_ambiguous=bool(instructions),
                )
                account_events.append(liquidation_event)
                marks[current.instrument] = (
                    pre_fill_adverse_margin.mark_price
                )
                equity_curve.append(
                    self._snapshot(current, marks, ledger)
                )
                break

            bar_fills: list[SimFill] = []
            latest_margin: MarginSnapshot | None = None
            margin_evaluated = False
            for instruction in instructions:
                self._validate_instruction_reduce_only(
                    instruction,
                    ledger,
                )
                reference_price = instruction.price
                effective_price = Decimal(
                    self.slippage_model.apply(
                        instruction,
                        reference_price,
                        current,
                    )
                )
                if not effective_price.is_finite():
                    raise ValueError(
                        "slippage model returned a non-finite price"
                    )
                if effective_price <= 0:
                    raise ValueError(
                        "slippage model returned a non-positive price"
                    )
                slippage_amount = effective_price - reference_price
                slippage_bps = (
                    slippage_amount
                    / reference_price
                    * Decimal("10000")
                )
                provisional_fill = SimFill(
                    fill_id=(
                        f"{instruction.instruction_key}@"
                        f"{current.sequence}"
                    ),
                    instruction_key=instruction.instruction_key,
                    source_intent_key=instruction.source_intent_key,
                    intent_mode=instruction.intent_mode,
                    instrument=instruction.instrument,
                    side=instruction.side,
                    price=effective_price,
                    quantity=instruction.quantity,
                    sequence=current.sequence,
                    timestamp=current.timestamp,
                    liquidity_role=default_liquidity_role(
                        instruction.intent_mode
                    ),
                    fee_rate=Decimal("0"),
                    fee_amount=Decimal("0"),
                    fee_asset=ledger.equity_asset,
                    reduce_only=instruction.reduce_only,
                    reference_price=reference_price,
                    slippage_amount=slippage_amount,
                    slippage_bps=slippage_bps,
                    tags=instruction.tags,
                )
                fee = self.fee_model.calculate(
                    instruction,
                    provisional_fill,
                )
                if not isinstance(fee, FeeResult):
                    raise TypeError(
                        "fee_model.calculate must return FeeResult"
                    )
                fill = replace(
                    provisional_fill,
                    liquidity_role=fee.liquidity_role,
                    fee_rate=fee.fee_rate,
                    fee_amount=fee.fee_amount,
                    fee_asset=fee.fee_asset,
                )
                self._validate_instruction_margin(
                    instruction,
                    fill,
                    current,
                    ledger,
                )
                ledger.apply(fill)
                all_instructions.append(instruction)
                bar_fills.append(fill)
                latest_margin = self._margin_snapshot(
                    current,
                    ledger,
                )
                margin_evaluated = True
                if (
                    latest_margin is not None
                    and latest_margin.liquidation_triggered
                ):
                    margin_snapshots.append(latest_margin)
                    liquidation_event = self._liquidation_event(
                        latest_margin
                    )
                    account_events.append(liquidation_event)
                    break

                if (
                    self.mark_price_sampling
                    == MarkPriceSampling.ADVERSE_EXTREME
                ):
                    adverse_margin = self._adverse_margin_snapshot(
                        current,
                        ledger,
                    )
                    if (
                        adverse_margin is not None
                        and adverse_margin.liquidation_triggered
                    ):
                        latest_margin = adverse_margin
                        margin_snapshots.append(adverse_margin)
                        liquidation_event = self._liquidation_event(
                            adverse_margin,
                            intrabar_ordering_ambiguous=True,
                        )
                        account_events.append(liquidation_event)
                        marks[current.instrument] = (
                            adverse_margin.mark_price
                        )
                        break

            if liquidation_event is None:
                funding = self._settle_funding(
                    current,
                    ledger,
                    marks,
                )
                if funding is not None:
                    funding_events.append(funding)
                    latest_margin = self._margin_snapshot(
                        current,
                        ledger,
                    )
                    margin_evaluated = True

            if not margin_evaluated:
                latest_margin = self._margin_snapshot(
                    current,
                    ledger,
                )
            if (
                liquidation_event is None
                and latest_margin is not None
            ):
                margin_snapshots.append(latest_margin)
                if latest_margin.liquidation_triggered:
                    liquidation_event = self._liquidation_event(
                        latest_margin
                    )
                    account_events.append(liquidation_event)

            if liquidation_event is not None:
                if bar_fills:
                    fills.extend(bar_fills)
                equity_curve.append(
                    self._snapshot(current, marks, ledger)
                )
                break

            if bar_fills:
                batch = tuple(bar_fills)
                fills.extend(batch)
                self.trade_port.on_fills(batch)
                if self.trace_port is not None:
                    active_intents = self._synchronize_intents(
                        active_intents,
                        tuple(self.trace_port.visible_intents()),
                        current_sequence=current.sequence,
                        filled_intent_keys={
                            fill.source_intent_key for fill in batch
                        },
                        intent_states=intent_states,
                        intent_state_by_key=intent_state_by_key,
                    )
            self.trade_port.on_market(current)
            if self.trace_port is not None:
                active_intents = self._synchronize_intents(
                    active_intents,
                    tuple(self.trace_port.visible_intents()),
                    current_sequence=current.sequence,
                    filled_intent_keys=set(),
                    intent_states=intent_states,
                    intent_state_by_key=intent_state_by_key,
                )
            equity_curve.append(self._snapshot(current, marks, ledger))

        positions = {
            instrument: quantity
            for instrument, quantity in ledger.positions.items()
            if quantity != 0
        }
        return SimulationResult(
            frames=tuple(frames),
            fills=tuple(fills),
            equity_curve=tuple(equity_curve),
            initial_equity=ledger.initial_equity,
            final_cash=ledger.cash,
            gross_realized_pnl=ledger.gross_realized_pnl,
            total_fees=ledger.total_fees,
            net_realized_pnl=ledger.net_realized_pnl,
            total_funding=ledger.total_funding,
            net_pnl_after_fees_and_funding=(
                ledger.net_pnl_after_fees_and_funding
            ),
            realized_pnl=ledger.realized_pnl,
            final_equity=ledger.equity(marks),
            intents=tuple(
                IntentRecord(
                    intent=state.intent,
                    active_from_sequence=state.active_from_sequence,
                    active_to_sequence=state.active_to_sequence,
                    status=state.status,
                )
                for state in intent_states
            ),
            instructions=tuple(all_instructions),
            final_positions=positions,
            final_average_costs=ledger.average_costs,
            equity_asset=ledger.equity_asset,
            final_account_metrics=ledger.account_metrics(marks),
            completed=liquidation_event is None,
            liquidated=liquidation_event is not None,
            bankrupt=(
                liquidation_event.bankrupt
                if liquidation_event is not None
                else False
            ),
            termination_reason=(
                SimulationTerminationReason.LIQUIDATION
                if liquidation_event is not None
                else None
            ),
            termination_sequence=(
                liquidation_event.sequence
                if liquidation_event is not None
                else None
            ),
            margin_snapshots=tuple(margin_snapshots),
            account_events=tuple(account_events),
            funding_enabled=self.funding_model.enabled,
            funding_source=self.funding_model.source,
            funding_market_conditioned=(
                self.funding_model.market_conditioned
            ),
            funding_events=tuple(funding_events),
            slippage_enabled=self.slippage_model.enabled,
            slippage_source=self.slippage_model.source,
        )

    @staticmethod
    def _validate_instruction_sources(
        instructions: tuple[TradeInstruction, ...],
        active_intents: dict[str, IntentSnapshot],
    ) -> None:
        missing = {
            instruction.source_intent_key
            for instruction in instructions
            if instruction.source_intent_key not in active_intents
        }
        if missing:
            raise ValueError(
                "instructions must reference visible intents: "
                + ", ".join(sorted(missing))
            )

    @staticmethod
    def _synchronize_intents(
        active: dict[str, IntentSnapshot],
        visible_intents: tuple[IntentSnapshot, ...],
        *,
        current_sequence: int,
        filled_intent_keys: set[str],
        intent_states: list[_IntentRecordState],
        intent_state_by_key: dict[str, _IntentRecordState],
    ) -> dict[str, IntentSnapshot]:
        visible: dict[str, IntentSnapshot] = {}
        for intent in visible_intents:
            if not isinstance(intent, IntentSnapshot):
                raise TypeError(
                    "visible_intents must return IntentSnapshot values"
                )
            if intent.intent_key in visible:
                raise ValueError(
                    f"duplicate visible intent key: {intent.intent_key}"
                )
            visible[intent.intent_key] = intent

        unknown_fills = filled_intent_keys - set(active)
        if unknown_fills:
            raise ValueError(
                "fills must reference visible intents: "
                + ", ".join(sorted(unknown_fills))
            )
        still_visible = filled_intent_keys & set(visible)
        if still_visible:
            raise ValueError(
                "filled intents must leave the visible set: "
                + ", ".join(sorted(still_visible))
            )

        removed_keys = set(active) - set(visible)
        for intent_key in removed_keys:
            state = intent_state_by_key[intent_key]
            state.active_to_sequence = current_sequence
            state.status = (
                IntentStatus.FILLED
                if intent_key in filled_intent_keys
                else IntentStatus.CANCELLED
            )

        synchronized: dict[str, IntentSnapshot] = {}
        for intent_key, intent in visible.items():
            existing = active.get(intent_key)
            if existing is not None:
                if existing != intent:
                    raise ValueError(
                        "visible intent changed without a new key: "
                        f"{intent_key}"
                    )
                synchronized[intent_key] = existing
                continue
            if intent_key in intent_state_by_key:
                raise ValueError(
                    "closed intent keys must not be reused: "
                    f"{intent_key}"
                )
            state = _IntentRecordState(
                intent=intent,
                active_from_sequence=current_sequence,
            )
            intent_states.append(state)
            intent_state_by_key[intent_key] = state
            synchronized[intent_key] = intent
        return synchronized

    @staticmethod
    def _prepare_instructions(
        frame: MarketFrame,
        instructions: tuple[TradeInstruction, ...],
        used_instruction_keys: set[str],
    ) -> tuple[TradeInstruction, ...]:
        instruction_keys = [
            instruction.instruction_key
            for instruction in instructions
        ]
        seen: set[str] = set()
        duplicates: set[str] = set()
        for instruction_key in instruction_keys:
            if instruction_key in seen:
                duplicates.add(instruction_key)
            seen.add(instruction_key)
        if duplicates:
            raise ValueError(
                "duplicate instruction keys: "
                + ", ".join(sorted(duplicates))
            )

        repeated = set(instruction_keys) & used_instruction_keys
        if repeated:
            raise ValueError(
                "instruction keys must not be reused: "
                + ", ".join(sorted(repeated))
            )

        for instruction in instructions:
            if instruction.frame_sequence != frame.sequence:
                raise ValueError(
                    "instruction frame_sequence must match current frame: "
                    f"instruction_key={instruction.instruction_key}, "
                    f"frame_sequence={instruction.frame_sequence}, "
                    f"current_sequence={frame.sequence}"
                )
            if instruction.instrument != frame.instrument:
                raise ValueError(
                    "instruction instrument must match current frame: "
                    f"instruction_key={instruction.instruction_key}, "
                    f"instrument={instruction.instrument}, "
                    f"current_instrument={frame.instrument}"
                )

        used_instruction_keys.update(instruction_keys)
        return tuple(
            sorted(
                instructions,
                key=lambda instruction: instruction.instruction_key,
            )
        )

    @staticmethod
    def _snapshot(
        frame: MarketFrame,
        marks: dict[str, Decimal],
        ledger: SimulationLedger,
    ) -> EquitySnapshot:
        positions = {
            instrument: quantity
            for instrument, quantity in ledger.positions.items()
            if quantity != 0
        }
        return EquitySnapshot(
            sequence=frame.sequence,
            timestamp=frame.timestamp,
            cash=ledger.cash,
            positions=positions,
            average_costs=ledger.average_costs,
            marks=marks,
            gross_realized_pnl=ledger.gross_realized_pnl,
            total_fees=ledger.total_fees,
            net_realized_pnl=ledger.net_realized_pnl,
            total_funding=ledger.total_funding,
            net_pnl_after_fees_and_funding=(
                ledger.net_pnl_after_fees_and_funding
            ),
            realized_pnl=ledger.realized_pnl,
            equity=ledger.equity(marks),
            equity_asset=ledger.equity_asset,
            account_metrics=ledger.account_metrics(marks),
        )

    def _settle_funding(
        self,
        frame: MarketFrame,
        ledger: SimulationLedger,
        marks: dict[str, Decimal],
    ) -> FundingSettlement | None:
        settlement = self.funding_model.settle(
            frame,
            ledger,
            marks,
        )
        if settlement is None:
            return None
        if not isinstance(settlement, FundingSettlement):
            raise TypeError(
                "funding_model.settle must return "
                "FundingSettlement or None"
            )
        if settlement.sequence != frame.sequence:
            raise ValueError(
                "funding settlement sequence must match current frame"
            )
        if settlement.instrument != frame.instrument:
            raise ValueError(
                "funding settlement instrument must match current frame"
            )
        ledger.apply_funding(settlement)
        return settlement

    def _margin_snapshot(
        self,
        frame: MarketFrame,
        ledger: SimulationLedger,
        *,
        mark_price: Decimal | None = None,
    ) -> MarginSnapshot | None:
        sampled_price = (
            frame.price if mark_price is None else Decimal(mark_price)
        )
        snapshot = self.margin_model.snapshot(
            ledger,
            mark_price=sampled_price,
            frame=frame,
            mark_price_source="market_ohlc_proxy",
        )
        if snapshot is not None and not isinstance(
            snapshot,
            MarginSnapshot,
        ):
            raise TypeError(
                "margin_model.snapshot must return "
                "MarginSnapshot or None"
            )
        return snapshot

    def _adverse_margin_snapshot(
        self,
        frame: MarketFrame,
        ledger: SimulationLedger,
    ) -> MarginSnapshot | None:
        position = ledger.positions.get(
            frame.instrument,
            Decimal("0"),
        )
        if position > 0:
            mark_price = frame.low
        elif position < 0:
            mark_price = frame.high
        else:
            return None
        return self._margin_snapshot(
            frame,
            ledger,
            mark_price=mark_price,
        )

    def _liquidation_event(
        self,
        snapshot: MarginSnapshot,
        *,
        intrabar_ordering_ambiguous: bool = False,
    ) -> LiquidationEvent:
        return LiquidationEvent(
            snapshot=snapshot,
            mark_price_sampling=self.mark_price_sampling,
            maintenance_schedule_version=(
                self.margin_model.maintenance_schedule_version
            ),
            intrabar_ordering_ambiguous=(
                intrabar_ordering_ambiguous
            ),
        )

    @staticmethod
    def _validate_instruction_reduce_only(
        instruction: TradeInstruction,
        ledger: SimulationLedger,
    ) -> None:
        if not instruction.reduce_only:
            return

        current_position = ledger.positions.get(
            instruction.instrument,
            Decimal("0"),
        )
        if not SimulationRunner._is_valid_reduce_only(
            instruction.side,
            instruction.quantity,
            current_position,
        ):
            raise ReduceOnlyViolationError(
                "reduce_only instruction cannot be filled: "
                f"instruction_key={instruction.instruction_key}, "
                f"source_intent_key={instruction.source_intent_key}, "
                f"instrument={instruction.instrument}, "
                f"current_position={current_position}, "
                f"side={instruction.side.value}, "
                f"quantity={instruction.quantity}"
            )

    def _validate_instruction_margin(
        self,
        instruction: TradeInstruction,
        fill: SimFill,
        frame: MarketFrame,
        ledger: SimulationLedger,
    ) -> None:
        current_position = ledger.positions.get(
            instruction.instrument,
            Decimal("0"),
        )
        if not self._opens_new_exposure(
            instruction.side,
            instruction.quantity,
            current_position,
        ):
            return

        projected = self.margin_model.projected_snapshot(
            ledger,
            fill=fill,
            mark_price=instruction.price,
            frame=frame,
            mark_price_source="fill_price_proxy",
        )
        if projected is None:
            return
        if not isinstance(projected, MarginSnapshot):
            raise TypeError(
                "margin_model.projected_snapshot must return "
                "MarginSnapshot or None"
            )
        if projected.available_balance < 0:
            raise InsufficientMarginError(
                instruction,
                projected,
            )

    @staticmethod
    def _opens_new_exposure(
        side: OrderSide,
        quantity: Decimal,
        current_position: Decimal,
    ) -> bool:
        signed_quantity = (
            quantity if side == OrderSide.BUY else -quantity
        )
        if current_position == 0:
            return True
        if current_position * signed_quantity > 0:
            return True
        return abs(signed_quantity) > abs(current_position)

    @staticmethod
    def _is_valid_reduce_only(
        side: OrderSide,
        quantity: Decimal,
        current_position: Decimal,
    ) -> bool:
        if current_position > 0:
            return (
                side == OrderSide.SELL
                and quantity <= current_position
            )
        if current_position < 0:
            return (
                side == OrderSide.BUY
                and quantity <= abs(current_position)
            )
        return False
