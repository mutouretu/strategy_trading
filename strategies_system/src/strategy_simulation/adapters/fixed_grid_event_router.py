"""Translate simulator fills into facts for the fixed-grid Strategy's Rules."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Sequence

from grid_rule.fixed_equal_usdt import (
    FixedEntryMode,
    FixedCellSpec,
    initial_sell_allocations,
)
from market_protocol import MarketFrame
from simulation_runtime import OrderSide, SimFill
from strategy_application import ApplicationIntentOwner, StrategyApplication
from trading_strategies.fixed_equal_usdt_grid_strategy import (
    GRID_RULE_KEY,
    SEED_RULE_KEY,
    FixedEqualUsdtGridStrategyParameters,
)
from trading_strategies.kernel import TradeSide
from trading_strategies.rules import (
    FixedGridFillAcknowledgementInput,
    FixedGridObservationInput,
    FixedGridObservedCell,
    InitialEntryFillInput,
    InitialEntryStartInput,
    SizedPosition,
)

from .strategy_application import StrategyApplicationEventRouter


@dataclass(slots=True)
class _CellPosition:
    stage: str
    open_qty: Decimal = Decimal("0")
    entry_price: Decimal = Decimal("0")
    cycle_count: int = 0
    seed_entry: bool = False


class FixedGridSeedSizer:
    """Size the seed Rule from the shared equal-U grid geometry."""

    def __init__(
        self,
        parameters: FixedEqualUsdtGridStrategyParameters,
        cells: tuple[FixedCellSpec, ...],
    ) -> None:
        self.parameters = parameters
        self.cells = cells
        self.allocations: dict[str, Decimal] = {}

    def size_entry(self, *, reference_price: Decimal) -> SizedPosition:
        self.allocations = initial_sell_allocations(
            self.cells,
            reference_price,
            step_size=self.parameters.step_size,
            min_qty=self.parameters.min_qty,
            min_notional=self.parameters.min_notional,
        )
        quantity = sum(self.allocations.values(), Decimal("0"))
        if quantity <= 0:
            raise ValueError("seeded grid has no sell-side position at this price")
        return SizedPosition(quantity, "BASE")


class FixedGridEventRouter(StrategyApplicationEventRouter):
    """Keep confirmed Cell facts; all grid order decisions stay in the Rule."""

    def __init__(
        self,
        parameters: FixedEqualUsdtGridStrategyParameters,
        seed_sizer: FixedGridSeedSizer,
    ) -> None:
        self.parameters = parameters
        self.strategy_instance_id = parameters.strategy_id
        self.seed_sizer = seed_sizer
        self.specs: tuple[FixedCellSpec, ...] = seed_sizer.cells
        self.cells = {
            spec.cell_id: _CellPosition(
                "untriggered"
                if parameters.entry_mode == FixedEntryMode.PRICE_TRIGGERED
                else "pending_entry"
            )
            for spec in self.specs
        }
        self.seed_filled = parameters.entry_mode == FixedEntryMode.PRICE_TRIGGERED
        self.seed_quantity = Decimal("0")
        self.seed_notional = Decimal("0")
        self.grid_profit = Decimal("0")
        self.seed_exit_profit = Decimal("0")
        self.grid_round_trips = 0
        self.seed_exits = 0
        self.peak_position_notional = Decimal("0")
        self.last_mark = Decimal("0")
        self._remaining_batch_fills = 0

    def begin_fill_batch(self, fills: Sequence[SimFill]) -> None:
        self._remaining_batch_fills = len(fills)

    def inputs_on_start(
        self, application: StrategyApplication, frame: MarketFrame,
    ) -> Mapping[str, Mapping[str, object]]:
        self._check_instrument(frame.instrument)
        self.last_mark = frame.close
        if self.parameters.entry_mode == FixedEntryMode.SEEDED:
            return {
                self.strategy_instance_id: {
                    SEED_RULE_KEY: InitialEntryStartInput(frame.close),
                }
            }
        return {
            self.strategy_instance_id: {
                GRID_RULE_KEY: self._observation(application, frame.close),
            }
        }

    def inputs_before_instructions(
        self, application: StrategyApplication, frame: MarketFrame,
    ) -> Mapping[str, Mapping[str, object]]:
        del application
        self._check_instrument(frame.instrument)
        return {}

    def inputs_after_market(
        self, application: StrategyApplication, frame: MarketFrame,
    ) -> Mapping[str, Mapping[str, object]]:
        self._check_instrument(frame.instrument)
        self.last_mark = frame.close
        position_qty = sum(
            (cell.open_qty for cell in self.cells.values()), Decimal("0"),
        )
        self.peak_position_notional = max(
            self.peak_position_notional,
            position_qty * frame.close,
        )
        if not self.seed_filled:
            return {}
        return {
            self.strategy_instance_id: {
                GRID_RULE_KEY: self._observation(application, frame.close),
            }
        }

    def inputs_for_fill(
        self,
        application: StrategyApplication,
        fill: SimFill,
        owner: ApplicationIntentOwner,
    ) -> Mapping[str, object]:
        self._check_instrument(fill.instrument)
        if self._remaining_batch_fills < 1:
            raise RuntimeError("grid fill arrived outside its accounting batch")
        self._remaining_batch_fills -= 1
        if owner.rule_key == SEED_RULE_KEY:
            if self.seed_filled or fill.side != OrderSide.BUY:
                raise ValueError("unexpected grid seed fill")
            self.seed_filled = True
            self.seed_quantity = fill.quantity
            self.seed_notional = fill.price * fill.quantity
            if fill.quantity != sum(self.seed_sizer.allocations.values(), Decimal("0")):
                raise ValueError("grid seed quantity does not match allocations")
            for cell_id, quantity in self.seed_sizer.allocations.items():
                cell = self.cells[cell_id]
                cell.stage = "pending_exit"
                cell.open_qty = quantity
                cell.entry_price = fill.price
                cell.seed_entry = True
            return {
                SEED_RULE_KEY: InitialEntryFillInput(
                    fill_id=fill.fill_id,
                    intent_key=owner.local_intent_key,
                    side=TradeSide.BUY,
                    price=fill.price,
                    quantity=fill.quantity,
                    actual_position=SizedPosition(fill.quantity, "BASE"),
                ),
                GRID_RULE_KEY: self._observation(
                    application, fill.price,
                    consumed_intent_key=fill.source_intent_key,
                ),
            }
        if owner.rule_key != GRID_RULE_KEY:
            raise ValueError("fill belongs to an unknown fixed-grid Rule slot")
        intent = next(
            (item for item in application.active_intents
             if item.intent_key == fill.source_intent_key),
            None,
        )
        if intent is None:
            raise ValueError("grid fill has no active Rule intent")
        cell = self.cells[str(intent.tags["cell_id"])]
        if fill.side == OrderSide.BUY:
            if cell.stage not in {"pending_entry", "untriggered"} or cell.open_qty:
                raise ValueError("buy fill conflicts with Cell state")
            cell.stage = "pending_exit"
            cell.open_qty = fill.quantity
            cell.entry_price = fill.price
            cell.seed_entry = False
        else:
            if cell.stage != "pending_exit" or fill.quantity != cell.open_qty:
                raise ValueError("sell fill conflicts with Cell position")
            profit = (fill.price - cell.entry_price) * fill.quantity
            if cell.seed_entry:
                self.seed_exit_profit += profit
                self.seed_exits += 1
            else:
                self.grid_profit += profit
                self.grid_round_trips += 1
            cell.stage = "pending_entry"
            cell.open_qty = Decimal("0")
            cell.entry_price = Decimal("0")
            cell.seed_entry = False
            cell.cycle_count += 1
        if self._remaining_batch_fills:
            return {
                GRID_RULE_KEY: FixedGridFillAcknowledgementInput(fill.fill_id),
            }
        return {
            GRID_RULE_KEY: self._observation(
                application, fill.price,
                consumed_intent_key=fill.source_intent_key,
            ),
        }

    def final_unrealized(self, mark: Decimal) -> Decimal:
        return sum(
            ((mark - cell.entry_price) * cell.open_qty
             for cell in self.cells.values() if cell.open_qty),
            Decimal("0"),
        )

    def _observation(
        self,
        application: StrategyApplication,
        mark: Decimal,
        *,
        consumed_intent_key: str | None = None,
    ) -> FixedGridObservationInput:
        remaining = (
            intent for intent in application.active_intents
            if intent.intent_key != consumed_intent_key
            and intent.tags["rule_key"] == GRID_RULE_KEY
        )
        entry_ids: set[str] = set()
        exit_ids: set[str] = set()
        for intent in remaining:
            target = entry_ids if intent.side == TradeSide.BUY else exit_ids
            target.add(str(intent.tags["cell_id"]))
        return FixedGridObservationInput(
            mark=mark,
            cells=tuple(
                FixedGridObservedCell(
                    spec.cell_id,
                    self.cells[spec.cell_id].stage,
                    open_qty=self.cells[spec.cell_id].open_qty,
                    cycle_count=self.cells[spec.cell_id].cycle_count,
                    has_entry_order=spec.cell_id in entry_ids,
                    has_exit_order=spec.cell_id in exit_ids,
                )
                for spec in self.specs
            ),
        )

    def _check_instrument(self, instrument: str) -> None:
        if instrument != self.parameters.instrument:
            raise ValueError("fixed-grid event instrument does not match Strategy")
