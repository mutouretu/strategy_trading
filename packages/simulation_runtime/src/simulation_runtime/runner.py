from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from market_protocol import MarketFrame, MarketSource

from .decision import SimulationDecisionPort
from .execution import BarTouchExecutionModel
from .ledger import LinearLedger, SimulationLedger
from .models import (
    ActiveOrder,
    EquitySnapshot,
    OrderRecord,
    OrderStatus,
    SimOrder,
    SimulationResult,
)


@dataclass(slots=True)
class _OrderRecordState:
    order: SimOrder
    active_from_sequence: int
    active_to_sequence: int | None = None
    status: OrderStatus = OrderStatus.ACTIVE


class SimulationRunner:
    """Compose a market source, decision port, execution model, and ledger."""

    def __init__(
        self,
        source: MarketSource,
        decision_port: SimulationDecisionPort,
        *,
        execution: BarTouchExecutionModel | None = None,
        initial_equity: Decimal = Decimal("0"),
        ledger_factory: Callable[[], SimulationLedger] | None = None,
    ) -> None:
        self.source = source
        self.decision_port = decision_port
        self.execution = execution or BarTouchExecutionModel()
        self.initial_equity = Decimal(initial_equity)
        if ledger_factory is not None and self.initial_equity != 0:
            raise ValueError(
                "initial_equity must be zero when ledger_factory is supplied"
            )
        self.ledger_factory = ledger_factory

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
        fills = []
        marks = {first.instrument: first.price}
        ledger = (
            self.ledger_factory()
            if self.ledger_factory is not None
            else LinearLedger(self.initial_equity)
        )
        active: dict[str, ActiveOrder] = {}
        retired_order_keys: set[str] = set()
        order_states: list[_OrderRecordState] = []
        order_state_by_key: dict[str, _OrderRecordState] = {}
        initial_decision = self.decision_port.initialize(first)
        active = self._synchronize(
            active,
            initial_decision.desired_orders,
            first.sequence,
            retired_order_keys,
            order_states,
            order_state_by_key,
        )
        equity_curve = [
            self._snapshot(first, marks, ledger)
        ]

        while not self.source.done and (
            max_frames is None or len(frames) < max_frames
        ):
            current = self.source.next()
            frames.append(current)
            marks[current.instrument] = current.price

            matched = self.execution.match(current, active.values())
            if matched:
                for fill in matched:
                    ledger.apply(fill)
                    active.pop(fill.order_key, None)
                    retired_order_keys.add(fill.order_key)
                    state = order_state_by_key[fill.order_key]
                    state.active_to_sequence = current.sequence
                    state.status = OrderStatus.FILLED
                fills.extend(matched)
                fill_decision = self.decision_port.on_fills(matched)
                self._reject_retired(
                    fill_decision.desired_orders,
                    retired_order_keys,
                )
                active = self._synchronize(
                    active,
                    fill_decision.desired_orders,
                    current.sequence,
                    retired_order_keys,
                    order_states,
                    order_state_by_key,
                )

            market_decision = self.decision_port.on_market(current)
            self._reject_retired(
                market_decision.desired_orders,
                retired_order_keys,
            )
            active = self._synchronize(
                active,
                market_decision.desired_orders,
                current.sequence,
                retired_order_keys,
                order_states,
                order_state_by_key,
            )
            equity_curve.append(self._snapshot(current, marks, ledger))
        positions = {
            instrument: quantity
            for instrument, quantity in ledger.positions.items()
            if quantity != 0
        }
        average_costs = ledger.average_costs
        return SimulationResult(
            frames=tuple(frames),
            orders=tuple(
                OrderRecord(
                    order=state.order,
                    active_from_sequence=state.active_from_sequence,
                    active_to_sequence=state.active_to_sequence,
                    status=state.status,
                )
                for state in order_states
            ),
            fills=tuple(fills),
            equity_curve=tuple(equity_curve),
            initial_equity=ledger.initial_equity,
            final_cash=ledger.cash,
            final_positions=positions,
            final_average_costs=average_costs,
            realized_pnl=ledger.realized_pnl,
            final_equity=ledger.equity(marks),
            equity_asset=ledger.equity_asset,
            final_account_metrics=ledger.account_metrics(marks),
        )

    @staticmethod
    def _reject_retired(
        desired_orders: tuple[SimOrder, ...],
        retired_order_keys: set[str],
    ) -> None:
        repeated = {
            order.order_key for order in desired_orders
        } & retired_order_keys
        if repeated:
            raise ValueError(
                "closed order keys must be retired: "
                + ", ".join(sorted(repeated))
            )

    @staticmethod
    def _synchronize(
        active: dict[str, ActiveOrder],
        desired_orders: tuple[SimOrder, ...],
        sequence: int,
        retired_order_keys: set[str],
        order_states: list[_OrderRecordState],
        order_state_by_key: dict[str, _OrderRecordState],
    ) -> dict[str, ActiveOrder]:
        desired: dict[str, SimOrder] = {}
        for order in desired_orders:
            if order.order_key in desired:
                raise ValueError(f"duplicate desired order key: {order.order_key}")
            desired[order.order_key] = order

        removed_keys = set(active) - set(desired)
        for order_key in removed_keys:
            state = order_state_by_key[order_key]
            state.active_to_sequence = sequence
            state.status = OrderStatus.CANCELLED
            retired_order_keys.add(order_key)

        repeated = set(desired) & retired_order_keys
        if repeated:
            raise ValueError(
                "closed order keys must be retired: "
                + ", ".join(sorted(repeated))
            )

        synchronized: dict[str, ActiveOrder] = {}
        for order_key, order in desired.items():
            existing = active.get(order_key)
            if existing is not None:
                if existing.order != order:
                    raise ValueError(
                        f"desired order changed without a new key: {order_key}"
                    )
                synchronized[order_key] = existing
            else:
                synchronized[order_key] = ActiveOrder(order, sequence)
                state = _OrderRecordState(order, sequence)
                order_states.append(state)
                order_state_by_key[order_key] = state
        return synchronized

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
            realized_pnl=ledger.realized_pnl,
            equity=ledger.equity(marks),
            equity_asset=ledger.equity_asset,
            account_metrics=ledger.account_metrics(marks),
        )
