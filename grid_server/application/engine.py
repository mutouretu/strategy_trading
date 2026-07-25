from __future__ import annotations

import hashlib
import os
import time
import uuid
from decimal import Decimal, ROUND_HALF_UP

from ..domain import (
    CellStage,
    FuturesMarket,
    GridCell,
    Mode,
    OrderSide,
    OrderSnapshot,
    OrderStatus,
    StrategyStatus,
    SymbolFilters,
)
from ..ports.exchange import (
    Exchange,
    ExchangeExecutionUnknownError,
    OrderNotFoundError,
)
from ..domain.grid import build_cells, next_long_cell, next_short_cell, round_down
from ..infrastructure.sqlite_store import SQLiteStore, utc_now


ENDED_STATUSES = {OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.REJECTED}


class TradingEngine:
    def __init__(self, store: SQLiteStore, exchange: Exchange, strategy_id: str, run_id: str | None = None) -> None:
        self.store = store
        self.exchange = exchange
        self.strategy_id = strategy_id
        self.run_id = run_id or uuid.uuid4().hex
        self.config = None
        self.filters: SymbolFilters | None = None
        self.initialized = False
        self._reported_reclaim_anomalies: set[tuple[str, str]] = set()
        self._retry_attempts: dict[tuple[str, str], int] = {}
        self._retry_not_before: dict[tuple[str, str], float] = {}

    def initialize(self) -> None:
        config = self._config()
        self.filters = self.exchange.get_symbol_filters(config.symbol)
        if config.market_type == FuturesMarket.COINM:
            if self.filters.contract_type != "PERPETUAL":
                raise ValueError("COIN-M grid currently supports PERPETUAL contracts only")
            if self.filters.contract_size <= 0:
                raise ValueError("COIN-M contractSize must be greater than zero")
        self.exchange.set_hedge_mode(True)
        self.exchange.set_leverage(config.symbol, config.leverage)

        cells = self.store.list_cells(self.strategy_id)
        if not cells:
            cells = build_cells(config, self.filters.tick_size)
            self.store.replace_cells(self.strategy_id, cells)
            for cell in cells:
                self._event("CELL_CREATED", cell, {"buy_price": str(cell.buy_price), "sell_price": str(cell.sell_price)})

        self._reconcile_open_orders()
        if self.store.set_status_if_active(
            self.strategy_id,
            StrategyStatus.RUNNING,
        ):
            self.store.heartbeat_if_active(
                self.strategy_id,
                self.run_id,
                os.getpid(),
            )
        self.initialized = True

    def tick(self) -> Decimal:
        if not self.initialized:
            self.initialize()
        config = self._config()
        mark = self.exchange.get_mark_price(config.symbol)
        if config.move_grid:
            self._move_window(mark)

        cells = self.sync_orders_only()
        for cell in cells:
            if cell.stage == CellStage.UNTRIGGERED and self._is_triggered(cell, mark):
                self._place_entry(cell)

        if self.store.set_status_if_active(
            self.strategy_id,
            StrategyStatus.RUNNING,
        ):
            self.store.heartbeat_if_active(
                self.strategy_id,
                self.run_id,
                os.getpid(),
                mark_price=mark,
            )
        return mark

    def sync_orders_only(self) -> list[GridCell]:
        if not self.initialized:
            self.initialize()
        for snapshot in self.store.list_cells(self.strategy_id):
            cell = self._stored_cell(snapshot.cell_id)
            if cell is None:
                continue
            if cell.stage == CellStage.MANUAL_REVIEW:
                self._recover_manual_review_reference(cell)
                cell = self._stored_cell(cell.cell_id)
                if cell is None:
                    continue

            recovery = self._recover_pending_reference(cell)
            if recovery == "unknown":
                continue
            cell = self._stored_cell(cell.cell_id)
            if cell is None:
                continue
            self._sync_cell(cell)
            cell = self._stored_cell(cell.cell_id)
            if cell is None:
                continue
            if cell.stage == CellStage.PENDING_ENTRY and cell.entry_order_id is None:
                # A known canceled/expired/rejected entry was already armed.
                # Restore it regardless of the latest mark price; applying the
                # trigger condition again can lose the order after price crosses.
                if self._retry_ready(cell, "place_entry"):
                    self._place_entry(cell)
            if cell.stage == CellStage.PENDING_EXIT and cell.exit_order_id is None and cell.open_qty > 0:
                if self._retry_ready(cell, "place_exit"):
                    self._ensure_exit(cell)
        return self.store.list_cells(self.strategy_id)

    def ensure_exit(self, cell: GridCell, quantity: Decimal | None = None) -> None:
        self._ensure_exit(cell, quantity)

    def sync_cell(self, cell: GridCell) -> None:
        self._sync_cell(cell)

    def process_cell_actions(self) -> None:
        if not self.initialized:
            self.initialize()
        for action in self.store.list_pending_cell_actions(self.strategy_id):
            try:
                self._process_cell_action(action)
            except Exception as exc:
                self.store.fail_cell_action(action["id"], str(exc))
                self.store.append_event(
                    self.strategy_id,
                    "CELL_ACTION_FAILED",
                    {
                        "action_id": action["id"],
                        "operation": action["operation"],
                        "boundary": action["boundary"],
                        "error": str(exc),
                    },
                    action.get("target_cell_id"),
                    self.run_id,
                )
            finally:
                # A completed adjustment changes grid_count. Never let the
                # cached immutable config make the moving window undo it.
                self.config = None

    def _process_cell_action(self, action: dict) -> None:
        assert self.filters is not None
        config = self._config()
        cells = self.store.list_cells(self.strategy_id)
        if not cells:
            raise ValueError("strategy has no Cells")
        target_cell = next(
            (cell for cell in cells if cell.cell_id == action["target_cell_id"]),
            None,
        )
        # Removing a Cell and marking its queued action complete cannot share
        # the exchange cancellation transaction.  If the process stopped in
        # that narrow gap, the missing frozen target proves the destructive
        # step already finished; complete the SQLite bookkeeping idempotently.
        if action["operation"] == "remove" and target_cell is None:
            self.store.complete_remove_cell_action(
                action["id"],
                self.strategy_id,
                action["target_cell_id"],
            )
            return
        boundary_index = 0 if action["boundary"] == "lower" else -1
        boundary_cell = cells[boundary_index]
        if boundary_cell.cell_id != action["target_cell_id"]:
            raise ValueError("the requested boundary changed; refresh and retry")

        reason = f"manual_{action['operation']}_{action['boundary']}"
        if action["operation"] == "add":
            if action["boundary"] == "lower":
                cell = next_short_cell(config, boundary_cell, self.filters.tick_size)
            else:
                cell = next_long_cell(config, boundary_cell, self.filters.tick_size)
            self.store.complete_add_cell_action(action["id"], cell)
            self._event("CELL_ADDED", cell, {"reason": reason, "action_id": action["id"]})
            return

        if len(cells) <= 1:
            raise ValueError("at least one Cell must remain")
        if not self._reclaim_window_cell(boundary_cell, reason=reason):
            raise ValueError("Cell removal stopped because its order or position is not safely reclaimable")
        self.store.complete_remove_cell_action(
            action["id"],
            self.strategy_id,
            boundary_cell.cell_id,
        )

    def _config(self):
        if self.config is None:
            self.config = self.store.get_strategy(self.strategy_id)
            if self.config is None:
                raise KeyError(self.strategy_id)
        return self.config

    def _is_triggered(self, cell: GridCell, mark: Decimal) -> bool:
        config = self._config()
        if config.mode == Mode.LONG:
            return mark >= cell.buy_price
        return mark <= cell.sell_price

    def _entry_side(self) -> OrderSide:
        return OrderSide.BUY if self._config().mode == Mode.LONG else OrderSide.SELL

    def _exit_side(self) -> OrderSide:
        return OrderSide.SELL if self._config().mode == Mode.LONG else OrderSide.BUY

    def _entry_price(self, cell: GridCell) -> Decimal:
        return cell.buy_price if self._config().mode == Mode.LONG else cell.sell_price

    def _exit_price(self, cell: GridCell) -> Decimal:
        return cell.sell_price if self._config().mode == Mode.LONG else cell.buy_price

    def _position_side(self) -> str:
        return "LONG" if self._config().mode == Mode.LONG else "SHORT"

    def _client_id(self, cell: GridCell, role: str) -> str:
        """Return the legacy deterministic id used by existing live orders."""

        # Generated strategy ids commonly share the same symbol/mode prefix.
        # Hash the full id so 50 groups on one symbol cannot collide at Binance.
        return f"{self._client_prefix()}{cell.cell_id[:8]}-{role}"

    def _new_client_id(self, cell: GridCell, role: str) -> str:
        # A cell can trade many cycles. A per-submission suffix prevents an
        # UNKNOWN response from being confused with an older completed cycle.
        return f"{self._client_prefix()}{cell.cell_id[:8]}-{role}{uuid.uuid4().hex[:6]}"

    def _client_prefix(self) -> str:
        tag = hashlib.sha1(self.strategy_id.encode("utf-8")).hexdigest()[:8]
        return f"wg-{tag}-"

    def _quantity(self, price: Decimal) -> Decimal:
        assert self.filters is not None
        config = self._config()
        if config.market_type == FuturesMarket.COINM:
            if self.filters.contract_size <= 0:
                return Decimal("0")
            # The user configures margin/base-coin quantity. DAPI still
            # accepts integer inverse-contract units, so convert at each
            # cell's order price and choose the closest valid step.
            raw_contracts = (
                config.order_base_quantity * price / self.filters.contract_size
            )
            if self.filters.step_size > 0:
                qty = (
                    raw_contracts / self.filters.step_size
                ).to_integral_value(rounding=ROUND_HALF_UP) * self.filters.step_size
            else:
                qty = raw_contracts
        else:
            qty = round_down(config.order_value_usd / price, self.filters.step_size)
        if qty <= 0 or qty < self.filters.min_qty:
            return Decimal("0")
        notional = (
            qty * self.filters.contract_size
            if config.market_type == FuturesMarket.COINM
            else qty * price
        )
        if self.filters.min_notional > 0 and notional < self.filters.min_notional:
            return Decimal("0")
        return qty

    def _place_entry(self, cell: GridCell) -> None:
        config = self._config()
        price = self._entry_price(cell)
        qty = self._quantity(price)
        if qty <= 0:
            self._event("ENTRY_SKIPPED", cell, {"reason": "min_qty_or_notional", "price": str(price)})
            return
        previous_stage = cell.stage
        previous_client_id = cell.entry_client_id
        client_id = cell.entry_client_id or self._new_client_id(cell, "e")
        cell.stage = CellStage.PENDING_ENTRY
        cell.entry_client_id = client_id
        self.store.save_cell(cell)
        try:
            order_id = self.exchange.place_limit_order(
                config.symbol,
                self._entry_side(),
                self._position_side(),
                qty,
                price,
                client_id,
            )
        except (ExchangeExecutionUnknownError, TimeoutError, ConnectionError) as exc:
            retry_after = self._record_retry(cell, "place_entry")
            self._event(
                "ENTRY_SUBMISSION_UNKNOWN",
                cell,
                {
                    "client_order_id": client_id,
                    "error": str(exc),
                    "retry_after_sec": retry_after,
                },
            )
            return
        except Exception:
            cell.stage = previous_stage
            cell.entry_client_id = previous_client_id
            self.store.save_cell(cell)
            raise
        self._clear_retry(cell, "place_entry")
        cell.entry_order_id = order_id
        self.store.save_cell(cell)
        self._event(
            "ENTRY_PLACED",
            cell,
            {"order_id": order_id, "client_order_id": client_id, "side": self._entry_side().value, "price": str(price), "qty": str(qty)},
        )

    def _ensure_exit(self, cell: GridCell, quantity: Decimal | None = None) -> None:
        config = self._config()
        assert self.filters is not None
        requested = cell.open_qty if quantity is None else min(cell.open_qty, quantity)
        qty = round_down(requested, self.filters.step_size)
        if qty <= 0:
            return
        price = self._exit_price(cell)
        previous_client_id = cell.exit_client_id
        client_id = cell.exit_client_id or self._new_client_id(cell, "x")
        cell.exit_client_id = client_id
        cell.exit_executed_qty = Decimal("0")
        self.store.save_cell(cell)
        try:
            order_id = self.exchange.place_limit_order(
                config.symbol,
                self._exit_side(),
                self._position_side(),
                qty,
                price,
                client_id,
            )
        except (ExchangeExecutionUnknownError, TimeoutError, ConnectionError) as exc:
            retry_after = self._record_retry(cell, "place_exit")
            self._event(
                "EXIT_SUBMISSION_UNKNOWN",
                cell,
                {
                    "client_order_id": client_id,
                    "error": str(exc),
                    "retry_after_sec": retry_after,
                },
            )
            return
        except Exception:
            cell.exit_client_id = previous_client_id
            self.store.save_cell(cell)
            raise
        self._clear_retry(cell, "place_exit")
        cell.exit_order_id = order_id
        self.store.save_cell(cell)
        self._event(
            "EXIT_PLACED",
            cell,
            {"order_id": order_id, "client_order_id": client_id, "side": self._exit_side().value, "price": str(price), "qty": str(qty)},
        )

    def _sync_cell(self, cell: GridCell) -> None:
        config = self._config()
        if cell.stage == CellStage.PENDING_ENTRY and cell.entry_order_id is not None:
            try:
                order = self.exchange.get_order(config.symbol, cell.entry_order_id)
            except OrderNotFoundError:
                self._event("ENTRY_MISSING", cell, {"order_id": cell.entry_order_id})
                # Missing history is ambiguous: the exchange may have accepted
                # and filled an order whose response/local save was lost.  A
                # blind replacement could double the position, unlike a known
                # CANCELED status which is safe to re-arm.
                cell.stage = CellStage.MANUAL_REVIEW
                self.store.save_cell(cell)
                return
            if order.status == OrderStatus.FILLED:
                qty = order.executed_qty or order.original_qty
                cell.open_qty += qty
                cell.entry_filled_at = cell.entry_filled_at or utc_now()
                cell.stage = CellStage.PENDING_EXIT
                self.store.save_cell(cell)
                self._event(
                    "ENTRY_FILLED",
                    cell,
                    {"order_id": order.order_id, "price": str(order.average_price or order.price), "qty": str(qty)},
                )
                self._ensure_exit(cell)
            elif order.status == OrderStatus.PARTIALLY_FILLED:
                # A partially filled entry creates a real position while the
                # remainder is still waiting.  This state cannot be represented
                # safely by one cell stage, so cancel the remainder and protect
                # exactly the final executed quantity with an exit.
                cancel_error: Exception | None = None
                try:
                    self.exchange.cancel_order(config.symbol, order.order_id)
                except Exception as exc:
                    # A fill may race the cancel. Query final state before
                    # deciding whether the cancellation really failed.
                    cancel_error = exc
                final_order = self.exchange.get_order(config.symbol, order.order_id)
                if final_order.status not in ENDED_STATUSES | {OrderStatus.FILLED}:
                    if cancel_error is not None:
                        raise cancel_error
                    raise RuntimeError(
                        f"entry {order.order_id} remained {final_order.status.value} after cancel"
                    )
                executed_qty = final_order.executed_qty
                if executed_qty <= 0:
                    cell.stage = CellStage.PENDING_ENTRY
                    cell.entry_order_id = None
                    cell.entry_client_id = ""
                else:
                    cell.open_qty += executed_qty
                    cell.entry_filled_at = cell.entry_filled_at or utc_now()
                    cell.stage = CellStage.PENDING_EXIT
                self.store.save_cell(cell)
                self._event(
                    "ENTRY_PARTIAL_FINALIZED",
                    cell,
                    {
                        "order_id": final_order.order_id,
                        "status": final_order.status.value,
                        "executed_qty": str(executed_qty),
                        "action": "place_exit" if executed_qty > 0 else "replace_entry",
                    },
                )
                if executed_qty > 0:
                    self._ensure_exit(cell)
            elif order.status in ENDED_STATUSES:
                executed_qty = order.executed_qty
                if executed_qty > 0:
                    # A canceled entry can already be partially filled. Treat that
                    # quantity as an open cell instead of replacing the full entry
                    # and accidentally increasing exposure.
                    cell.open_qty += executed_qty
                    cell.entry_filled_at = cell.entry_filled_at or utc_now()
                    cell.stage = CellStage.PENDING_EXIT
                else:
                    cell.stage = CellStage.PENDING_ENTRY
                    cell.entry_order_id = None
                    cell.entry_client_id = ""
                    cell.entry_filled_at = ""
                self.store.save_cell(cell)
                self._event(
                    "ENTRY_ENDED",
                    cell,
                    {
                        "order_id": order.order_id,
                        "status": order.status.value,
                        "executed_qty": str(executed_qty),
                        "action": "place_exit_for_partial_fill" if executed_qty > 0 else "replace_entry",
                    },
                )
                if executed_qty > 0:
                    self._ensure_exit(cell)

        elif cell.stage == CellStage.PENDING_EXIT and cell.exit_order_id is not None:
            try:
                order = self.exchange.get_order(config.symbol, cell.exit_order_id)
            except OrderNotFoundError:
                self._event("EXIT_MISSING", cell, {"order_id": cell.exit_order_id})
                cell.stage = CellStage.MANUAL_REVIEW
                self.store.save_cell(cell)
                return
            if order.status == OrderStatus.FILLED:
                qty = order.executed_qty or order.original_qty
                delta = max(Decimal("0"), qty - cell.exit_executed_qty)
                cell.open_qty = max(Decimal("0"), cell.open_qty - delta)
                cell.exit_executed_qty = qty
                cell.exit_order_id = None
                cell.exit_client_id = ""
                cell.exit_executed_qty = Decimal("0")
                if cell.open_qty > 0:
                    # The coordinator may intentionally size a grid exit below
                    # the cell's logical position while an external close order
                    # reserves the remainder. Filling that smaller exit must not
                    # falsely close the whole cell.
                    cell.stage = CellStage.MANUAL_REVIEW
                    self.store.save_cell(cell)
                    self._event(
                        "EXIT_TARGET_FILLED",
                        cell,
                        {
                            "order_id": order.order_id,
                            "price": str(order.average_price or order.price),
                            "qty": str(qty),
                            "remaining_open_qty": str(cell.open_qty),
                        },
                    )
                else:
                    cell.cycle_count += 1
                    cell.stage = CellStage.UNTRIGGERED
                    cell.entry_order_id = None
                    cell.entry_client_id = ""
                    cell.entry_filled_at = ""
                    self.store.save_cell(cell)
                    self._event(
                        "CYCLE_CLOSED",
                        cell,
                        {"order_id": order.order_id, "price": str(order.average_price or order.price), "qty": str(qty), "cycle_count": cell.cycle_count},
                    )
            elif order.status == OrderStatus.PARTIALLY_FILLED:
                delta = max(Decimal("0"), order.executed_qty - cell.exit_executed_qty)
                if delta > 0:
                    cell.open_qty = max(Decimal("0"), cell.open_qty - delta)
                    cell.exit_executed_qty = order.executed_qty
                    self.store.save_cell(cell)
                    self._event(
                        "EXIT_PARTIALLY_FILLED",
                        cell,
                        {
                            "order_id": order.order_id,
                            "executed_delta": str(delta),
                            "executed_total": str(order.executed_qty),
                            "remaining_open_qty": str(cell.open_qty),
                        },
                    )
            elif order.status in ENDED_STATUSES:
                delta = max(Decimal("0"), order.executed_qty - cell.exit_executed_qty)
                delta = min(delta, cell.open_qty)
                cell.open_qty -= delta
                cell.exit_executed_qty = order.executed_qty
                cell.exit_order_id = None
                cell.exit_client_id = ""
                if cell.open_qty > 0:
                    cell.stage = CellStage.MANUAL_REVIEW
                else:
                    cell.stage = CellStage.UNTRIGGERED
                    cell.entry_order_id = None
                    cell.entry_client_id = ""
                    cell.exit_executed_qty = Decimal("0")
                    cell.entry_filled_at = ""
                    cell.cycle_count += 1
                self.store.save_cell(cell)
                self._event(
                    "EXIT_ENDED",
                    cell,
                    {
                        "order_id": order.order_id,
                        "status": order.status.value,
                        "executed_qty": str(order.executed_qty),
                        "executed_delta": str(delta),
                        "remaining_open_qty": str(cell.open_qty),
                        "action": "manual_review" if cell.open_qty > 0 else "cycle_closed",
                    },
                )

    def _reconcile_open_orders(self) -> None:
        config = self._config()
        cells = self.store.list_cells(self.strategy_id)
        by_client_id = {}
        by_cell_role: dict[str, list[tuple[GridCell, str]]] = {}
        for cell in cells:
            if cell.entry_client_id:
                by_client_id[cell.entry_client_id] = (cell, "entry")
            if cell.exit_client_id:
                by_client_id[cell.exit_client_id] = (cell, "exit")
            # Keep compatibility with orders created before client ids gained a
            # per-submission suffix and before the id was saved locally.
            by_client_id[self._client_id(cell, "e")] = (cell, "entry")
            by_client_id[self._client_id(cell, "x")] = (cell, "exit")
            by_cell_role.setdefault(f"{cell.cell_id[:8]}-e", []).append(
                (cell, "entry")
            )
            by_cell_role.setdefault(f"{cell.cell_id[:8]}-x", []).append(
                (cell, "exit")
            )

        for order in self.exchange.get_open_orders(config.symbol):
            matched = by_client_id.get(order.client_order_id)
            if matched is None:
                matched = self._match_submission_client_id(
                    order.client_order_id,
                    by_cell_role,
                )
            if matched is None:
                if order.client_order_id.startswith(self._client_prefix()):
                    self.store.append_event(
                        self.strategy_id,
                        "ORPHAN_MANAGED_ORDER",
                        {
                            "order_id": order.order_id,
                            "client_order_id": order.client_order_id,
                            "side": order.side.value,
                            "position_side": order.position_side,
                            "price": str(order.price),
                            "qty": str(order.original_qty),
                        },
                        None,
                        self.run_id,
                    )
                continue
            cell, role = matched
            mismatches = self._recovery_mismatches(cell, role, order)
            if mismatches:
                cell.stage = CellStage.MANUAL_REVIEW
                if role == "entry":
                    cell.entry_order_id = None
                    cell.entry_client_id = ""
                else:
                    cell.exit_order_id = None
                    cell.exit_client_id = ""
                self.store.save_cell(cell)
                self._event(
                    "OPEN_ORDER_MISMATCH",
                    cell,
                    {
                        "order_id": order.order_id,
                        "client_order_id": order.client_order_id,
                        "role": role,
                        "mismatches": mismatches,
                    },
                )
                continue
            if role == "entry":
                cell.stage = CellStage.PENDING_ENTRY
                cell.entry_order_id = order.order_id
                cell.entry_client_id = order.client_order_id
            else:
                executed_delta = max(Decimal("0"), order.executed_qty - cell.exit_executed_qty)
                if cell.open_qty > 0:
                    cell.open_qty = max(Decimal("0"), cell.open_qty - executed_delta)
                else:
                    cell.open_qty = max(Decimal("0"), order.original_qty - order.executed_qty)
                cell.stage = CellStage.PENDING_EXIT
                cell.exit_order_id = order.order_id
                cell.exit_client_id = order.client_order_id
                cell.exit_executed_qty = order.executed_qty
            self.store.save_cell(cell)
            self._event("OPEN_ORDER_RECOVERED", cell, {"order_id": order.order_id, "role": role})

    def _match_submission_client_id(
        self,
        client_order_id: str,
        by_cell_role: dict[str, list[tuple[GridCell, str]]],
    ) -> tuple[GridCell, str] | None:
        """Recover a current order even when both local references were lost.

        New submissions append six hexadecimal characters to the legacy
        ``<cell-prefix>-<role>`` identity.  The stable part still identifies
        the Cell, while the suffix prevents an UNKNOWN response from colliding
        with an older completed cycle.  A truncated Cell prefix collision is
        deliberately treated as ambiguous instead of guessing ownership.
        """

        prefix = self._client_prefix()
        if not client_order_id.startswith(prefix):
            return None
        tail = client_order_id[len(prefix):]
        if len(tail) != 16 or tail[8:9] != "-" or tail[9:10] not in {"e", "x"}:
            return None
        suffix = tail[10:]
        if any(character not in "0123456789abcdef" for character in suffix.lower()):
            return None
        candidates = by_cell_role.get(tail[:10], [])
        if len(candidates) != 1:
            return None
        return candidates[0]

    def _recover_pending_reference(self, cell: GridCell) -> str:
        role = ""
        client_id = ""
        if (
            cell.stage == CellStage.PENDING_ENTRY
            and cell.entry_order_id is None
            and cell.entry_client_id
        ):
            role = "entry"
            client_id = cell.entry_client_id
        elif (
            cell.stage == CellStage.PENDING_EXIT
            and cell.exit_order_id is None
            and cell.exit_client_id
        ):
            role = "exit"
            client_id = cell.exit_client_id
        else:
            return "none"

        try:
            order = self.exchange.get_order_by_client_id(
                self._config().symbol,
                client_id,
            )
        except OrderNotFoundError:
            return "missing"
        except ExchangeExecutionUnknownError as exc:
            retry_after = self._record_retry(cell, f"confirm_{role}")
            self._event(
                "ORDER_CONFIRMATION_UNKNOWN",
                cell,
                {
                    "role": role,
                    "client_order_id": client_id,
                    "error": str(exc),
                    "retry_after_sec": retry_after,
                },
            )
            return "unknown"

        mismatches = self._recovery_mismatches(cell, role, order)
        if mismatches:
            cell.stage = CellStage.MANUAL_REVIEW
            self.store.save_cell(cell)
            self._event(
                "ORDER_CONFIRMATION_MISMATCH",
                cell,
                {
                    "role": role,
                    "order_id": order.order_id,
                    "client_order_id": client_id,
                    "mismatches": mismatches,
                },
            )
            return "mismatch"

        if role == "entry":
            cell.stage = CellStage.PENDING_ENTRY
            cell.entry_order_id = order.order_id
        else:
            cell.stage = CellStage.PENDING_EXIT
            cell.exit_order_id = order.order_id
        self.store.save_cell(cell)
        self._clear_retry(cell, f"confirm_{role}")
        self._clear_retry(cell, f"place_{role}")
        self._event(
            "ORDER_SUBMISSION_CONFIRMED",
            cell,
            {"role": role, "order_id": order.order_id, "client_order_id": client_id},
        )
        return "recovered"

    def _recover_manual_review_reference(self, cell: GridCell) -> None:
        config = self._config()
        candidates = []
        # Once a cell owns position, its historical filled entry is evidence,
        # not a pending operation. Replaying it would count the same fill twice.
        if cell.open_qty <= 0 and cell.entry_order_id is not None:
            candidates.append(("entry", cell.entry_order_id))
        if cell.open_qty > 0 and cell.exit_order_id is not None:
            candidates.append(("exit", cell.exit_order_id))
        for role, order_id in candidates:
            try:
                order = self.exchange.get_order(config.symbol, order_id)
            except (OrderNotFoundError, ExchangeExecutionUnknownError):
                continue
            if self._recovery_mismatches(cell, role, order):
                continue
            cell.stage = (
                CellStage.PENDING_ENTRY if role == "entry" else CellStage.PENDING_EXIT
            )
            self.store.save_cell(cell)
            self._event(
                "MANUAL_REVIEW_ORDER_RECOVERED",
                cell,
                {"role": role, "order_id": order_id, "status": order.status.value},
            )
            self._sync_cell(cell)
            return

    def _recovery_mismatches(
        self,
        cell: GridCell,
        role: str,
        order: OrderSnapshot,
    ) -> list[str]:
        expected_side = self._entry_side() if role == "entry" else self._exit_side()
        expected_price = self._entry_price(cell) if role == "entry" else self._exit_price(cell)
        mismatches: list[str] = []
        if order.side != expected_side:
            mismatches.append(f"side:{order.side.value}!={expected_side.value}")
        if order.position_side != self._position_side():
            mismatches.append(
                f"position_side:{order.position_side}!={self._position_side()}"
            )
        if order.price != expected_price:
            mismatches.append(f"price:{order.price}!={expected_price}")
        if role == "entry":
            expected_qty = self._quantity(expected_price)
            if order.original_qty != expected_qty:
                mismatches.append(f"qty:{order.original_qty}!={expected_qty}")
        elif cell.open_qty > 0:
            remaining = max(Decimal("0"), order.original_qty - order.executed_qty)
            if remaining > cell.open_qty:
                mismatches.append(f"remaining_qty:{remaining}>{cell.open_qty}")
        return mismatches

    def _move_window(self, mark: Decimal) -> None:
        assert self.filters is not None
        config = self._config()
        cells = self.store.list_cells(self.strategy_id)
        if not cells:
            return

        additions = 0
        if config.mode == Mode.LONG:
            while mark >= cells[-1].sell_price and additions < 100:
                cell = next_long_cell(config, cells[-1], self.filters.tick_size)
                self.store.save_cell(cell)
                self._event("CELL_ADDED", cell, {"reason": "move_up"})
                cells.append(cell)
                additions += 1
        else:
            while mark <= cells[0].buy_price and additions < 100:
                cell = next_short_cell(config, cells[0], self.filters.tick_size)
                self.store.save_cell(cell)
                self._event("CELL_ADDED", cell, {"reason": "move_down"})
                cells.insert(0, cell)
                additions += 1

        while len(cells) > config.grid_count:
            # Only reclaim the farthest cell. Skipping a protected far cell and
            # removing a middle cell would create a price gap in the moving window.
            removable = cells[0] if config.mode == Mode.LONG else cells[-1]
            if not self._reclaim_window_cell(removable):
                break
            cells = [cell for cell in cells if cell.cell_id != removable.cell_id]

        for index, cell in enumerate(sorted(cells, key=lambda item: item.buy_price), start=1):
            if cell.index != index:
                cell.index = index
                self.store.save_cell(cell)

    def _reclaim_window_cell(self, cell: GridCell, reason: str = "window_reclaim") -> bool:
        """Safely reclaim an outer Cell without ever deleting owned position."""

        config = self._config()
        if cell.open_qty > 0 or cell.stage in {CellStage.PENDING_EXIT, CellStage.MANUAL_REVIEW}:
            self._report_reclaim_anomaly(
                cell,
                "unexpected_owned_farthest_cell",
                {
                    "stage": cell.stage.value,
                    "open_qty": str(cell.open_qty),
                    "entry_order_id": cell.entry_order_id,
                    "exit_order_id": cell.exit_order_id,
                },
            )
            return False

        if cell.stage == CellStage.PENDING_ENTRY:
            reason = f"{reason}_pending_entry"
            if cell.entry_order_id is None:
                self._report_reclaim_anomaly(
                    cell,
                    "pending_entry_without_order",
                    {
                        "stage": cell.stage.value,
                        "open_qty": str(cell.open_qty),
                    },
                )
                return False

            # Moving the window happens before the normal order-sync pass. Read
            # Binance first so a fill since the previous poll cannot be mistaken
            # for an untouched entry merely because SQLite is one tick behind.
            order_id = cell.entry_order_id
            self._sync_cell(cell)
            refreshed = self._stored_cell(cell.cell_id)
            if refreshed is None:
                return False
            cell = refreshed
            if cell.stage != CellStage.PENDING_ENTRY or cell.open_qty > 0:
                self._report_reclaim_anomaly(
                    cell,
                    "entry_changed_before_reclaim",
                    {
                        "stage": cell.stage.value,
                        "open_qty": str(cell.open_qty),
                        "entry_order_id": cell.entry_order_id,
                        "exit_order_id": cell.exit_order_id,
                    },
                )
                return False

            # A known ended zero-fill order is safe to remove. An open order is
            # canceled, then read again to close the final fill-vs-cancel race.
            if cell.entry_order_id is not None:
                if not self._retry_ready(cell, "cancel_entry"):
                    return False
                try:
                    canceled = self.exchange.cancel_order(config.symbol, order_id)
                except ExchangeExecutionUnknownError as exc:
                    retry_after = self._record_retry(cell, "cancel_entry")
                    self._event(
                        "CELL_RECLAIM_CANCEL_UNKNOWN",
                        cell,
                        {
                            "order_id": order_id,
                            "error": str(exc),
                            "retry_after_sec": retry_after,
                        },
                    )
                    return False
                except Exception as exc:
                    retry_after = self._record_retry(cell, "cancel_entry")
                    self._event(
                        "CELL_RECLAIM_CANCEL_FAILED",
                        cell,
                        {
                            "order_id": order_id,
                            "error": str(exc),
                            "retry_after_sec": retry_after,
                        },
                    )
                    return False

                self._clear_retry(cell, "cancel_entry")

                if canceled is None:
                    self._report_reclaim_anomaly(
                        cell,
                        "cancel_response_missing",
                        {
                            "stage": cell.stage.value,
                            "open_qty": str(cell.open_qty),
                            "entry_order_id": cell.entry_order_id,
                        },
                    )
                    return False

                if canceled.executed_qty > 0:
                    cell.open_qty += canceled.executed_qty
                    cell.entry_filled_at = cell.entry_filled_at or utc_now()
                    cell.stage = CellStage.PENDING_EXIT
                    self.store.save_cell(cell)
                    self._event(
                        "ENTRY_ENDED",
                        cell,
                        {
                            "order_id": canceled.order_id,
                            "status": canceled.status.value,
                            "executed_qty": str(canceled.executed_qty),
                            "action": "place_exit_for_partial_fill",
                        },
                    )
                    self._ensure_exit(cell)
                    self._report_reclaim_anomaly(
                        cell,
                        "cancel_raced_entry_fill",
                        {
                            "stage": cell.stage.value,
                            "open_qty": str(cell.open_qty),
                            "entry_order_id": cell.entry_order_id,
                            "exit_order_id": cell.exit_order_id,
                        },
                    )
                    return False

                cell.entry_order_id = None
                cell.entry_client_id = ""
                cell.entry_filled_at = ""
                self.store.save_cell(cell)
        elif cell.stage != CellStage.UNTRIGGERED:
            return False

        self.store.delete_cell(self.strategy_id, cell.cell_id)
        self._event("CELL_REMOVED", cell, {"reason": reason})
        return True

    def _stored_cell(self, cell_id: str) -> GridCell | None:
        return next(
            (
                item
                for item in self.store.list_cells(self.strategy_id)
                if item.cell_id == cell_id
            ),
            None,
        )

    def _retry_ready(self, cell: GridCell, operation: str) -> bool:
        return time.monotonic() >= self._retry_not_before.get(
            (cell.cell_id, operation),
            0.0,
        )

    def _record_retry(self, cell: GridCell, operation: str) -> float:
        key = (cell.cell_id, operation)
        attempt = self._retry_attempts.get(key, 0) + 1
        self._retry_attempts[key] = attempt
        schedule = (15.0, 30.0, 60.0, 120.0, 300.0)
        delay = schedule[min(attempt - 1, len(schedule) - 1)]
        self._retry_not_before[key] = time.monotonic() + delay
        return delay

    def _clear_retry(self, cell: GridCell, operation: str) -> None:
        key = (cell.cell_id, operation)
        self._retry_attempts.pop(key, None)
        self._retry_not_before.pop(key, None)

    def _report_reclaim_anomaly(self, cell: GridCell, reason: str, payload: dict) -> None:
        key = (cell.cell_id, reason)
        if key in self._reported_reclaim_anomalies:
            return
        self._reported_reclaim_anomalies.add(key)
        self._event("WINDOW_RECLAIM_ANOMALY", cell, {"reason": reason, **payload})

    def _event(self, event_type: str, cell: GridCell, payload: dict) -> None:
        self.store.append_event(self.strategy_id, event_type, payload, cell.cell_id, self.run_id)


def run_loop(engine: TradingEngine, stop_requested=lambda: False) -> None:
    engine.initialize()
    while not stop_requested():
        started = time.monotonic()
        try:
            engine.tick()
        except Exception as exc:
            engine.store.heartbeat(engine.strategy_id, engine.run_id, os.getpid(), last_error=str(exc))
            engine.store.set_status(engine.strategy_id, StrategyStatus.ERROR)
        config = engine._config()
        elapsed = time.monotonic() - started
        time.sleep(max(0, config.poll_interval_sec - elapsed))
