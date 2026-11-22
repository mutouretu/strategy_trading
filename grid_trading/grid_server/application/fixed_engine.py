from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from decimal import Decimal, ROUND_CEILING

from ..domain import CellStage, FixedEntryMode, GridCell, OrderSide, OrderStatus, StrategyStatus
from ..domain.grid import fixed_cell_budgets, round_down
from ..infrastructure.binance import BinanceAPIError, BinanceFuturesExchange
from ..infrastructure.okx import OkxAPIError, OkxSwapExchange
from ..ports.exchange import ExchangeExecutionUnknownError, OrderNotFoundError
from ..shared.config import fixed_grid_endpoint_allowed, okx_fixed_grid_endpoint_allowed
from .engine import TradingEngine
from .grid_performance import strategy_performance, sync_strategy_trades
from .live_entry_policy import is_live_exchange, require_live_triggered_entry


class FixedGridEngine(TradingEngine):
    """Fixed long grid with one exchange position and per-cell logical ownership."""

    ORDERS_PER_SIDE = 10
    REBALANCE_BELOW = 5
    REBALANCE_ABOVE = 15
    MAX_WINDOW_CANCELS_PER_TICK = 10

    @staticmethod
    def _triggered_cash_reserve(config) -> Decimal:
        # Keep a small fee/funding cushion without making tiny test grids
        # require a full 1,000 USDT reserve.
        assert config.grid_budget_usdt is not None
        return min(Decimal("1000"), config.grid_budget_usdt * Decimal("0.01"))

    def _event(self, event_type: str, cell: GridCell, payload: dict) -> None:
        if event_type == "CYCLE_CLOSED" or (
            event_type == "EXIT_ENDED" and payload.get("action") == "cycle_closed"
        ):
            # Capture the committed policy and close event in one transaction.
            # The scheduler may have a cached config during its separate
            # position patrol; reading it here could stamp a stale percent.
            self.store.append_cycle_close_event(
                self.strategy_id, cell.cell_id, event_type, payload,
                cell.cycle_count, self.run_id,
            )
            return
        super()._event(event_type, cell, payload)

    def _sync_cell(self, cell: GridCell) -> None:
        exit_order_id = cell.exit_order_id
        super()._sync_cell(cell)
        if (exit_order_id is not None
                and cell.stage == CellStage.MANUAL_REVIEW
                and cell.open_qty > 0
                and cell.exit_order_id is None):
            # An externally ended exit must wait for the account position audit
            # before its window slot can be refilled.
            self._exit_repair_ids = getattr(self, "_exit_repair_ids", set())
            self._exit_repair_ids.add(cell.cell_id)

    def initialize(self) -> None:
        raw_exchange = getattr(self.exchange, "exchange", self.exchange)
        if isinstance(raw_exchange, BinanceFuturesExchange):
            if not fixed_grid_endpoint_allowed(raw_exchange.base_url):
                raise ValueError("fixed equal-USDT grid requires an approved Binance endpoint")
        elif isinstance(raw_exchange, OkxSwapExchange):
            if not okx_fixed_grid_endpoint_allowed(raw_exchange.base_url, simulated=raw_exchange.simulated):
                raise ValueError("fixed equal-USDT grid requires explicit OKX live opt-in")
        config = self._config()
        require_live_triggered_entry(
            raw_exchange, config.fixed_entry_mode, config.upper_seed_count,
        )
        self.store.ensure_reinvest_cutover(self.strategy_id)
        super().initialize()
        assert self.filters is not None
        if self.filters.contract_type not in {"", "PERPETUAL", "TRADIFI_PERPETUAL"}:
            raise ValueError("fixed grid requires a perpetual contract")

    def _quantity(self, price: Decimal, cell: GridCell | None = None) -> Decimal:
        assert self.filters is not None
        if cell is None:
            raise ValueError("fixed grid sizing requires a cell")
        quantity = round_down(cell.budget_usdt / price, self.filters.step_size)
        if quantity < self.filters.min_qty or quantity * price < self.filters.min_notional:
            return Decimal("0")
        return quantity

    def _submit_limit_order(
        self,
        symbol: str,
        side: OrderSide,
        position_side: str,
        quantity: Decimal,
        price: Decimal,
        client_order_id: str,
    ) -> int:
        assert self.filters is not None
        mark = getattr(self, "_tick_mark", None) or self.exchange.get_mark_price(symbol)
        if (side == OrderSide.BUY and mark <= price) or (
            side == OrderSide.SELL and mark >= price
        ):
            # Mark price can cross a grid line while the executable book has not.
            # Keep the catch-up limit on the favorable side of the grid target
            # while moving it inside the exchange's current price band.
            submitted_price = price
            if isinstance(getattr(self.exchange, "exchange", self.exchange), OkxSwapExchange):
                buy_limit, sell_limit = self.exchange.get_price_limits(symbol)
                if side == OrderSide.BUY and buy_limit is not None:
                    submitted_price = min(submitted_price, round_down(buy_limit, self.filters.tick_size))
                elif side == OrderSide.SELL and sell_limit is not None:
                    tick = self.filters.tick_size
                    legal_minimum = (sell_limit / tick).to_integral_value(rounding=ROUND_CEILING) * tick
                    submitted_price = max(submitted_price, legal_minimum)
            if side == OrderSide.BUY and self.filters.percent_price_up > 0:
                submitted_price = min(
                    submitted_price, round_down(mark * self.filters.percent_price_up, self.filters.tick_size)
                )
            elif side == OrderSide.SELL and self.filters.percent_price_down > 0:
                minimum = mark * self.filters.percent_price_down
                tick = self.filters.tick_size
                legal_minimum = (
                    (minimum / tick).to_integral_value(rounding=ROUND_CEILING) * tick
                    if tick > 0 else minimum
                )
                submitted_price = max(submitted_price, legal_minimum)
            order_id = self.exchange.place_limit_order(
                symbol, side, position_side, quantity, submitted_price, client_order_id,
                post_only=False,
            )
            self.store.append_event(
                self.strategy_id,
                "GRID_CATCHUP_LIMIT_ORDER",
                {
                    "order_id": order_id,
                    "client_order_id": client_order_id,
                    "side": side.value,
                    "target_price": str(price),
                    "submitted_price": str(submitted_price),
                    "observed_mark": str(mark),
                    "quantity": str(quantity),
                },
                run_id=self.run_id,
            )
            return order_id
        return self.exchange.place_limit_order(
            symbol, side, position_side, quantity, price, client_order_id,
            post_only=True,
        )

    def _order_is_ready(self, side: OrderSide, price: Decimal) -> bool:
        assert self.filters is not None
        mark = getattr(self, "_tick_mark", None) or self.exchange.get_mark_price(self._config().symbol)
        if (side == OrderSide.BUY and mark <= price) or (
            side == OrderSide.SELL and mark >= price
        ):
            return True
        if isinstance(getattr(self.exchange, "exchange", self.exchange), OkxSwapExchange):
            limits = getattr(self, "_tick_price_limits", None)
            if limits is None:
                limits = self.exchange.get_price_limits(self._config().symbol)
                self._tick_price_limits = limits
            buy_limit, sell_limit = limits
            if side == OrderSide.BUY:
                return buy_limit is None or price <= buy_limit
            return sell_limit is None or price >= sell_limit
        # Binance's futures PERCENT_PRICE rule is side-specific: a passive
        # BUY can rest far below mark and a passive SELL far above mark.
        if side == OrderSide.BUY:
            return self.filters.percent_price_up <= 0 or price <= mark * self.filters.percent_price_up
        return self.filters.percent_price_down <= 0 or price >= mark * self.filters.percent_price_down

    def is_virtual_exit(self, cell: GridCell, mark: Decimal) -> bool:
        """A held cell outside the ten-order window remains logically covered."""
        window = getattr(self, "_managed_exit_window_ids", None)
        return (
            cell.open_qty > 0
            and cell.exit_order_id is None
            and window is not None
            and cell.cell_id not in window
        )

    @staticmethod
    def _retryable_price_rejection(exc: BinanceAPIError | OkxAPIError) -> bool:
        if isinstance(exc, OkxAPIError):
            return exc.code == "51006"
        return any(code in str(exc) for code in ("-4016", "-4024", "-5022"))

    def _place_entry(self, cell: GridCell) -> None:
        allowed = getattr(self, "_window_entry_ids", None)
        if allowed is not None and cell.cell_id not in allowed:
            return
        if not self._order_is_ready(OrderSide.BUY, cell.buy_price):
            return
        config = self._config()
        cash_before = None
        required = Decimal("0")
        if config.fixed_entry_mode == FixedEntryMode.PRICE_TRIGGERED:
            quantity = self._quantity(cell.buy_price, cell)
            if quantity > 0:
                available = getattr(self, "_tick_free_cash", None)
                if available is None:
                    available = self.exchange.get_available_balance("USDT")
                required = quantity * cell.buy_price
                reserve = self._triggered_cash_reserve(config)
                if available < required + reserve:
                    if not getattr(self, "_triggered_cash_waiting", False):
                        self.store.append_event(
                            self.strategy_id, "TRIGGERED_ENTRY_FUNDS_WAIT", {
                                "available_usdt": str(available),
                                "required_order_usdt": str(required),
                                "reserve_usdt": str(reserve),
                            }, cell.cell_id, self.run_id,
                        )
                    self._triggered_cash_waiting = True
                    self._tick_free_cash = available
                    return
                cash_before = available
                # The account snapshot may not yet reflect orders submitted in
                # this tick. Reserve their full notional locally before trying
                # another cell; an unknown submission remains reserved.
                self._tick_free_cash = available - required
        try:
            super()._place_entry(cell)
        except (BinanceAPIError, OkxAPIError) as exc:
            if cash_before is not None:
                self._tick_free_cash = cash_before
            if (isinstance(exc, BinanceAPIError)
                    and config.fixed_entry_mode == FixedEntryMode.PRICE_TRIGGERED
                    and any(code in str(exc) for code in ("-2018", "-2019"))):
                # The exchange's margin check can be stricter than the last
                # available-balance snapshot. Wait for a fresh next-cycle
                # balance instead of turning the strategy into an error loop.
                if not getattr(self, "_triggered_cash_waiting", False):
                    self.store.append_event(
                        self.strategy_id, "TRIGGERED_ENTRY_FUNDS_WAIT", {
                            "available_usdt": str(cash_before),
                            "required_order_usdt": str(required),
                            "reason": "exchange_margin_rejection",
                        }, cell.cell_id, self.run_id,
                    )
                self._triggered_cash_waiting = True
                self._tick_free_cash = Decimal("0")
                return
            if not self._retryable_price_rejection(exc):
                raise
            return
        except Exception:
            if cash_before is not None:
                self._tick_free_cash = cash_before
            raise
        if cash_before is not None and getattr(self, "_triggered_cash_waiting", False):
            self.store.append_event(
                self.strategy_id, "TRIGGERED_ENTRY_FUNDS_AVAILABLE", {
                    "available_usdt": str(cash_before),
                }, cell.cell_id, self.run_id,
            )
            self._triggered_cash_waiting = False

    def _ensure_exit(self, cell: GridCell, quantity: Decimal | None = None) -> None:
        allowed = getattr(self, "_window_exit_ids", None)
        if allowed is not None and cell.cell_id not in allowed:
            return
        if not self._order_is_ready(OrderSide.SELL, cell.sell_price):
            return
        try:
            super()._ensure_exit(cell, quantity)
        except (BinanceAPIError, OkxAPIError) as exc:
            if not self._retryable_price_rejection(exc):
                raise

    def _recovery_mismatches(self, cell: GridCell, role: str, order) -> list[str]:
        # A top-up changes the desired entry size; a still-open old order is
        # resized after restart instead of being treated as an alien order.
        # Historical market catch-up orders report a zero limit price when
        # recovering by client ID. New catch-up orders use price-protected limits.
        return [
            mismatch
            for mismatch in super()._recovery_mismatches(cell, role, order)
            if not (
                (role == "entry" and mismatch.startswith("qty:"))
                or (
                    mismatch.startswith("price:")
                    and order.price > 0
                    and (
                        (role == "entry" and order.price <= cell.buy_price)
                        or (role == "exit" and order.price >= cell.sell_price)
                    )
                )
                or (
                    order.status == OrderStatus.FILLED
                    and order.price == 0
                    and mismatch.startswith("price:")
                )
            )
        ]

    def sync_orders_only(self) -> list[GridCell]:
        # The scheduler also calls this during its separate position patrol.
        # That patrol may reconcile fills, but only tick() may choose which
        # cells can receive new orders in the managed ten-per-side window.
        if getattr(self, "_window_entry_ids", None) is not None:
            return super().sync_orders_only()
        self._window_entry_ids = set()
        self._window_exit_ids = set()
        try:
            return super().sync_orders_only()
        finally:
            self._window_entry_ids = None
            self._window_exit_ids = None

    def tick(self) -> Decimal:
        if not self.initialized:
            self.initialize()
        self.store.ensure_reinvest_cutover(self.strategy_id)
        # The API can switch reinvestment while the scheduler keeps this engine.
        self.config = None
        config = self._config()
        mark = self.exchange.get_mark_price(config.symbol)
        self._tick_mark = mark
        self._tick_price_limits = None
        self._tick_free_cash = None
        seed_id = f"seed:{self.strategy_id}"
        seed = self.store.get_capital_action(seed_id)
        if seed is None:
            self._preflight_seed()
            if config.fixed_entry_mode == FixedEntryMode.PRICE_TRIGGERED:
                seed = self.store.activate_triggered_fixed_grid(self.strategy_id)
            else:
                assert config.grid_budget_usdt is not None
                seed = self.store.create_capital_action(
                    seed_id, self.strategy_id, "seed", config.grid_budget_usdt
                )

        if seed["status"] == "manual_review":
            raise RuntimeError(f"initial position needs manual review: {seed['message']}")
        self._window_entry_ids = set()
        self._window_exit_ids = set()
        try:
            prior_entries = {
                cell.cell_id: cell.entry_order_id
                for cell in self.store.list_cells(self.strategy_id)
                if cell.stage == CellStage.PENDING_ENTRY and cell.entry_order_id is not None
            }
            before = {
                cell.cell_id: cell.exit_order_id
                for cell in self.store.list_cells(self.strategy_id)
                if cell.exit_order_id is not None
            }
            if seed["status"] == "applied":
                self.sync_orders_only()

            actions = self.store.list_capital_actions(self.strategy_id)
            pending = next(
                (item for item in actions if item["status"] in {"requested", "planned", "submitted"}),
                None,
            )
            if pending is not None:
                self._process_capital_action(pending, mark)
                pending = self.store.get_capital_action(pending["id"])
                if pending["status"] != "applied":
                    self._heartbeat(mark)
                    return mark

            self._reinvest_closed_cells()
            if config.fixed_entry_mode == FixedEntryMode.PRICE_TRIGGERED:
                self.store.arm_triggered_fixed_cells(self.strategy_id, mark)
                self.store.arm_upper_seed_cells(
                    self.strategy_id, mark,
                    defer_until_price_advance=is_live_exchange(self.exchange),
                )
            else:
                self._arm_untriggered()
            self.sync_orders_only()
            self._refresh_order_window(mark, prior_entries, before)
            self.sync_orders_only()
            self._resize_orders()
            self._managed_exit_window_ids = frozenset(self._window_exit_ids)
            self._window_initialized = True
            self._heartbeat(mark)
            return mark
        finally:
            self._window_entry_ids = None
            self._window_exit_ids = None

    def _refresh_order_window(
        self, mark: Decimal, prior_entries: dict[str, int], prior_exits: dict[str, int]
    ) -> None:
        cells = self.store.list_cells(self.strategy_id)
        repair_ids = getattr(self, "_exit_repair_ids", set())
        repair_ids.update(
            cell.cell_id for cell in cells
            if cell.stage == CellStage.MANUAL_REVIEW
            and cell.open_qty > 0 and cell.exit_order_id is None
        )
        repair_ids.intersection_update(
            cell.cell_id for cell in cells
            if cell.open_qty > 0 and cell.exit_order_id is None
            and cell.stage in {CellStage.MANUAL_REVIEW, CellStage.PENDING_EXIT}
        )
        self._exit_repair_ids = repair_ids
        entries = [cell for cell in cells if cell.stage == CellStage.PENDING_ENTRY]
        exits = [cell for cell in cells if cell.stage == CellStage.PENDING_EXIT and cell.open_qty > 0]
        active_entries = {cell.cell_id for cell in entries if cell.entry_order_id is not None}
        active_exits = {cell.cell_id for cell in exits if cell.exit_order_id is not None}
        ready_entry_cells = [
            cell for cell in entries if self._order_is_ready(OrderSide.BUY, cell.buy_price)
        ]
        ready_exit_cells = [
            cell for cell in exits if self._order_is_ready(OrderSide.SELL, cell.sell_price)
        ]
        ready_entries = sum(cell.cell_id in active_entries for cell in ready_entry_cells)
        ready_exits = sum(cell.cell_id in active_exits for cell in ready_exit_cells)
        if self._config().fixed_entry_mode == FixedEntryMode.PRICE_TRIGGERED:
            # A zero-position grid has no sell orders to replenish yet. Do not
            # rebalance every tick solely because that side is intentionally empty.
            sparse_window = (
                bool(ready_entry_cells) and ready_entries < self.REBALANCE_BELOW
            ) or (
                bool(ready_exit_cells) and ready_exits < self.REBALANCE_BELOW
            )
        else:
            sparse_window = (
                min(len(active_entries), len(active_exits)) < self.REBALANCE_BELOW
                or min(ready_entries, ready_exits) == 0
            )
        # ready_entry_cells has already passed the exchange price-band check.
        # A buy line crossed during the separate position patrol has priority
        # within the same ten-order target, even if mark has moved far below it.
        crossed_entry_cells = sorted(
            (
                cell for cell in ready_entry_cells
                if cell.entry_order_id is None
                and cell.cell_id not in prior_exits
                and cell.buy_price >= mark
            ),
            key=lambda cell: (abs(cell.buy_price - mark), cell.index),
        )[:self.ORDERS_PER_SIDE]
        crossed_entry_ids = {cell.cell_id for cell in crossed_entry_cells}
        preferred_entry_cells = crossed_entry_cells + [
            cell for cell in sorted(
                ready_entry_cells, key=lambda item: (abs(item.buy_price - mark), item.index)
            )
            if cell.cell_id not in crossed_entry_ids
        ][:self.ORDERS_PER_SIDE - len(crossed_entry_cells)]
        preferred_entries = {cell.cell_id for cell in preferred_entry_cells}
        preferred_exit_cells = sorted(
            ready_exit_cells, key=lambda cell: (abs(cell.sell_price - mark), cell.index)
        )[:self.ORDERS_PER_SIDE]
        preferred_exits = {cell.cell_id for cell in preferred_exit_cells}
        paired_entries = {
            cell.cell_id for cell in entries
            if cell.entry_order_id is None and cell.cell_id in prior_exits
        }
        paired_exits = {
            cell.cell_id for cell in exits
            if cell.exit_order_id is None and cell.cell_id in prior_entries
        }
        price_priority_changed = (
            bool((preferred_entries - active_entries - paired_entries)
                 and (active_entries - preferred_entries))
            or bool((preferred_exits - active_exits - paired_exits)
                    and (active_exits - preferred_exits))
        )
        repair_candidates = (repair_ids & preferred_exits) - active_exits
        audited_repair_exits = set()
        if repair_candidates:
            config = self._config()
            pool = self.store.get_position_pool(
                config.symbol, self._position_side(), config.market_type,
            )
            if (pool is not None and pool["status"] == "consistent"
                    and Decimal(pool["external_reserved_qty"]) == 0):
                audited_repair_exits = repair_candidates
        # A side may still have 5-9 orders while a closer Cell is unarmed
        # between them. Refill that interior gap; keep the usual threshold
        # for a missing Cell only at the far edge of the window.
        def has_interior_gap(preferred: list[GridCell], active: set[str],
                             paired: set[str], blocked: set[str] | None = None) -> bool:
            blocked_ids = blocked if blocked is not None else set()
            farthest_active = max(
                (index for index, cell in enumerate(preferred) if cell.cell_id in active),
                default=-1,
            )
            return any(
                index < farthest_active and cell.cell_id not in active
                and cell.cell_id not in paired
                and cell.cell_id not in blocked_ids
                for index, cell in enumerate(preferred)
            )

        entry_gap = has_interior_gap(preferred_entry_cells, active_entries, paired_entries)
        exit_gap = has_interior_gap(
            preferred_exit_cells, active_exits, paired_exits,
            repair_ids - audited_repair_exits,
        )
        # Rebuild when one side is sparse, an old window has grown too large,
        # a price gap leaves an entire side outside the executable band, or a
        # closer executable cell is waiting while a farther order uses its slot,
        # or a nearer Cell is missing inside an otherwise populated window.
        # A bounded cleanup can span several ticks and must finish even if
        # its first batch brings the count below the upper threshold.
        if (not getattr(self, "_window_initialized", False)
                or sparse_window
                or max(len(active_entries), len(active_exits)) > self.REBALANCE_ABOVE
                or getattr(self, "_window_cleanup_pending", False)
                or price_priority_changed
                or entry_gap or exit_gap
                or bool(audited_repair_exits)
                or bool(crossed_entry_ids)):
            # A newly completed buy or sell keeps its paired order even when
            # the price-priority window is already full.
            self._window_entry_ids = preferred_entries | paired_entries
            self._window_exit_ids = preferred_exits | paired_exits
            self._window_cleanup_pending = True
            self._window_cleanup_pending = not self._defer_far_orders(
                mark, self._window_entry_ids, self._window_exit_ids
            )
            signature = (
                frozenset(self._window_entry_ids), frozenset(self._window_exit_ids),
                frozenset(active_entries), frozenset(active_exits),
            )
            if signature != getattr(self, "_last_window_signature", None):
                self.store.append_event(
                    self.strategy_id, "ORDER_WINDOW_REBALANCED", {
                        "mark": str(mark),
                        "entries_before": len(active_entries),
                        "exits_before": len(active_exits),
                        "entry_target": self.ORDERS_PER_SIDE,
                        "exit_target": self.ORDERS_PER_SIDE,
                        "price_priority": price_priority_changed,
                        "entry_gap": entry_gap,
                        "exit_gap": exit_gap,
                        "audited_exit_repair": bool(audited_repair_exits),
                        "crossed_entry_priority": bool(crossed_entry_ids),
                    }, run_id=self.run_id,
                )
                self._last_window_signature = signature
        else:
            # Keep existing limits in place. Paired orders take priority over
            # the window target so a newly filled cell can complete its cycle.
            # The next batch rebalance returns both sides to ten.
            self._window_entry_ids = active_entries
            self._window_exit_ids = active_exits
            new_entries = [
                cell for cell in entries
                if cell.entry_order_id is None and cell.cell_id in prior_exits
            ]
            # A fresh buy gets its paired sell. Previously deferred held cells
            # are also caught up after mark crosses their sell target; ordinary
            # far cells wait for the next batch rebalance.
            new_exits = [
                cell for cell in exits
                if cell.exit_order_id is None and cell.cell_id in prior_entries
            ]
            crossed_exits = sorted(
                (
                    cell for cell in exits
                    if cell.exit_order_id is None
                    and cell.cell_id not in prior_entries
                    and cell.sell_price <= mark
                ),
                key=lambda cell: abs(cell.sell_price - mark),
            )[:self.ORDERS_PER_SIDE]
            new_exits.extend(crossed_exits)
            for cell in sorted(new_entries, key=lambda item: abs(item.buy_price - mark)):
                self._window_entry_ids.add(cell.cell_id)
            for cell in sorted(new_exits, key=lambda item: abs(item.sell_price - mark)):
                self._window_exit_ids.add(cell.cell_id)

    def _defer_far_orders(self, mark: Decimal, entry_ids: set[str], exit_ids: set[str]) -> bool:
        """Cancel far limits in bounded batches; return whether cleanup finished."""
        assert self.filters is not None
        config = self._config()
        cancellations = 0
        for cell in self.store.list_cells(self.strategy_id):
            if cell.stage == CellStage.PENDING_ENTRY and cell.entry_order_id is not None:
                if cell.cell_id in entry_ids and self._order_is_ready(OrderSide.BUY, cell.buy_price):
                    continue
                order_id = cell.entry_order_id
                side = "entry"
            elif cell.stage == CellStage.PENDING_EXIT and cell.exit_order_id is not None:
                if cell.cell_id in exit_ids and self._order_is_ready(OrderSide.SELL, cell.sell_price):
                    continue
                order_id = cell.exit_order_id
                side = "exit"
            else:
                continue
            if cancellations >= self.MAX_WINDOW_CANCELS_PER_TICK:
                return False
            order = self.exchange.get_order(config.symbol, order_id)
            if order.status not in {OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED}:
                continue
            final = self.exchange.cancel_order(config.symbol, order_id)
            cancellations += 1
            if final.status not in {OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.FILLED}:
                raise RuntimeError(f"far {side} order cancellation is not confirmed")
            if side == "entry" or final.status == OrderStatus.FILLED:
                # The existing state machine handles any fill that raced the cancel.
                self._sync_cell(cell)
                updated = self._stored_cell(cell.cell_id)
                if (side == "entry" and updated is not None
                        and updated.stage == CellStage.PENDING_EXIT
                        and updated.open_qty > 0 and updated.exit_order_id is None):
                    self._window_exit_ids.add(updated.cell_id)
                    self._ensure_exit(updated)
                continue
            delta = min(
                cell.open_qty,
                max(Decimal("0"), final.executed_qty - cell.exit_executed_qty),
            )
            if delta >= cell.open_qty:
                self._sync_cell(cell)
                continue
            cell.open_qty -= delta
            cell.exit_order_id = None
            cell.exit_client_id = ""
            cell.exit_executed_qty = Decimal("0")
            cell.stage = CellStage.PENDING_EXIT
            self.store.save_cell(cell)
            self.store.append_event(
                self.strategy_id, "FAR_EXIT_DEFERRED", {
                    "order_id": order_id,
                    "executed_qty": str(final.executed_qty),
                    "executed_delta": str(delta),
                    "remaining_open_qty": str(cell.open_qty),
                }, cell.cell_id, self.run_id,
            )
        return True

    def _reinvest_closed_cells(self) -> None:
        config = self._config()
        assert self.filters is not None
        step = self.filters.step_size
        if step <= 0:
            return
        events = self.store.list_events(self.strategy_id)
        cutover_cycles = self.store.reinvest_cutover_cycles(self.strategy_id)
        observed_cycles: dict[str, int] = {}
        closing_events: dict[tuple[str, int], dict] = {}
        expected_fills: dict[tuple[str, int, str, int], Decimal] = {}
        fill_events = {
            "ENTRY_FILLED": ("BUY", "qty"),
            "ENTRY_PARTIAL_FINALIZED": ("BUY", "executed_qty"),
            "ENTRY_ENDED": ("BUY", "executed_qty"),
            "EXIT_PARTIALLY_FILLED": ("SELL", "executed_total"),
            "EXIT_TARGET_FILLED": ("SELL", "qty"),
            "EXIT_ENDED": ("SELL", "executed_qty"),
            "CYCLE_CLOSED": ("SELL", "qty"),
            "FAR_EXIT_DEFERRED": ("SELL", "executed_qty"),
        }
        for event in events:
            cell_id = event["cell_id"]
            if cell_id is None:
                continue
            payload = event["payload"]
            kind = event["event_type"]
            current_cycle = observed_cycles.get(cell_id, 0) + 1
            if kind in fill_events and payload.get("order_id") is not None:
                side, quantity_key = fill_events[kind]
                quantity = Decimal(str(payload.get(quantity_key, "0")))
                key = (cell_id, current_cycle, side, int(payload["order_id"]))
                expected_fills[key] = max(expected_fills.get(key, Decimal("0")), quantity)
            if event["event_type"] != "CYCLE_CLOSED" and not (
                event["event_type"] == "EXIT_ENDED"
                and payload.get("action") == "cycle_closed"
            ):
                continue
            cycle = int(payload.get("cycle_count", current_cycle))
            observed_cycles[cell_id] = max(observed_cycles.get(cell_id, 0), cycle)
            if cycle > cutover_cycles.get(cell_id, 0):
                closing_events[(cell_id, cycle)] = event
        if not closing_events:
            return
        try:
            raw_exchange = getattr(self.exchange, "exchange", self.exchange)
            coverage = sync_strategy_trades(self.store, raw_exchange, config)
            if not coverage:
                return
            performance = strategy_performance(self.store, config, coverage_complete=True)
            profits = performance["cell_net_grid_profit_usdt"]
            cycle_profits = performance["cell_cycle_net_grid_profit_usdt"]
            if not performance["accounting_complete"] or profits is None or cycle_profits is None:
                return
            # A filled order may become visible before its user-trade fees do.
            # Wait for every closing fill before permanently allocating its
            # net profit. A zero-percent cycle is still booked as consumed.
            filled_qty_by_order: dict[tuple[str, int], Decimal] = {}
            for trade in self.store.list_strategy_trades(self.strategy_id):
                key = (trade["side"], int(trade["order_id"]))
                filled_qty_by_order[key] = (
                    filled_qty_by_order.get(key, Decimal("0")) + Decimal(trade["quantity"])
                )
            for (cell_id, cycle), closing in closing_events.items():
                payload = closing["payload"]
                order_id = payload.get("order_id")
                if order_id is None:
                    continue
                # A cycle may contain several partially filled and replaced
                # entry/exit orders. Do not freeze its fee-adjusted profit until
                # every event-confirmed fill from all those orders is present.
                if any(
                    filled_qty_by_order.get((side, oid), Decimal("0")) != quantity
                    for (cid, cy, side, oid), quantity in expected_fills.items()
                    if cid == cell_id and cy == cycle
                ):
                    continue
                cycle_profit = cycle_profits.get(cell_id, {}).get(str(cycle))
                if cycle_profit is None:
                    continue
                self.store.book_cell_reinvest_earnings(
                    self.strategy_id, cell_id, cycle, Decimal(cycle_profit),
                    Decimal(str(payload.get("auto_reinvest_percent", "100"))),
                )

            if not config.auto_reinvest_enabled:
                return
            if any(
                action["status"] in {"requested", "planned", "submitted", "manual_review"}
                for action in self.store.list_capital_actions(self.strategy_id)
            ):
                return
            earned_cycles = {
                (item["cell_id"], int(item["cycle_count"]))
                for item in self.store.list_cell_reinvest_earnings(self.strategy_id)
            }
            cells = [
                cell for cell in self.store.list_cells(self.strategy_id)
                if cell.stage in {CellStage.UNTRIGGERED, CellStage.PENDING_ENTRY}
                and cell.cycle_count > 0 and cell.open_qty == 0
                and (cell.entry_order_id is not None or not cell.entry_client_id)
                and (cell.cell_id, cell.cycle_count) in earned_cycles
            ]
            if not cells:
                return
            available = self.exchange.get_available_balance("USDT")
            for cell in cells:
                amount = cell.buy_price * step
                if available < amount:
                    continue
                profit = Decimal(profits.get(cell.cell_id, "0"))
                if self.store.apply_cell_reinvestment(
                    self.strategy_id, cell.cell_id, cell.cycle_count, step, profit
                ):
                    available -= amount
                    self.config = None
        except Exception as exc:
            # Reinvestment is optional; a delayed trade-history response must
            # not prevent the ordinary next grid buy from being placed.
            self.store.append_event(
                self.strategy_id, "AUTO_REINVEST_DEFERRED",
                {"error": str(exc)}, run_id=self.run_id,
            )

    def _heartbeat(self, mark: Decimal) -> None:
        if self.store.set_status_if_active(self.strategy_id, StrategyStatus.RUNNING):
            self.store.heartbeat_if_active(
                self.strategy_id, self.run_id, os.getpid(), mark_price=mark
            )

    def _preflight_seed(self) -> None:
        config = self._config()
        assert config.grid_budget_usdt is not None
        assert config.lower_price is not None and config.upper_price is not None
        for other in self.store.list_strategies():
            if (
                other.strategy_id != self.strategy_id
                and other.market_type == config.market_type
                and other.symbol == config.symbol
                and other.has_started
                and other.status in {StrategyStatus.STARTING, StrategyStatus.RUNNING, StrategyStatus.ERROR}
            ):
                raise ValueError("a fixed grid requires exclusive use of its symbol")
        mark = self.exchange.get_mark_price(config.symbol)
        if not config.lower_price <= mark <= config.upper_price:
            raise ValueError("current price is outside fixed grid boundaries")
        available = self.exchange.get_available_balance("USDT")
        if config.fixed_entry_mode == FixedEntryMode.PRICE_TRIGGERED:
            # Only buy levels at or below the starting mark are exposed on a
            # direct decline. Require enough cash for that path, then gate
            # every later buy order against live free margin.
            cells = self.store.list_cells(self.strategy_id)
            downside_budget = sum(
                (cell.budget_usdt for cell in cells if cell.buy_price <= mark), Decimal("0")
            )
            upper_seed_cells = (
                [] if is_live_exchange(self.exchange) else
                [cell for cell in cells if cell.buy_price > mark][:config.upper_seed_count]
            )
            upper_seed_budget = sum((cell.budget_usdt for cell in upper_seed_cells), Decimal("0"))
            required = min(
                config.grid_budget_usdt,
                downside_budget + upper_seed_budget + self._triggered_cash_reserve(config),
            )
            if available < required:
                raise ValueError(
                    f"available USDT {available} is below triggered downside "
                    f"requirement {required}"
                )
        elif available < config.grid_budget_usdt:
            raise ValueError("available USDT is below the grid budget")
        if any(position.symbol == config.symbol for position in self.exchange.get_positions()):
            raise ValueError("existing position on the symbol must be resolved before seeding")
        if self.exchange.get_open_orders(config.symbol):
            raise ValueError("existing open orders on the symbol must be resolved before seeding")

    def _process_capital_action(self, action: dict, mark: Decimal) -> None:
        if action["status"] == "requested":
            action = self._plan_capital_action(action, mark)
        if action["status"] == "rejected":
            return
        if action["status"] == "planned":
            if Decimal(action["target_qty"]) == 0:
                allocations = self._planned_allocations(action, Decimal("0"))
                self.store.apply_capital_action(action["id"], allocations, Decimal("0"))
                self.config = None
                return
            # Persist the client identity before the network write. An unknown
            # response must be queried, never blindly submitted a second time.
            self.store.mark_capital_action_submitted(action["id"])
            try:
                order_id = self.exchange.place_market_order(
                    self._config().symbol,
                    OrderSide.BUY,
                    "LONG",
                    Decimal(action["target_qty"]),
                    action["client_order_id"],
                )
            except ExchangeExecutionUnknownError:
                return
            except Exception as exc:
                self.store.mark_capital_action_review(action["id"], str(exc))
                raise
            self.store.mark_capital_action_submitted(action["id"], order_id)
            action = self.store.get_capital_action(action["id"])
        if action["status"] == "submitted":
            try:
                order = self.exchange.get_order_by_client_id(
                    self._config().symbol, action["client_order_id"]
                )
            except (OrderNotFoundError, ExchangeExecutionUnknownError):
                return
            if order.status in {OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED}:
                return
            target = Decimal(action["target_qty"])
            if order.status != OrderStatus.FILLED or order.executed_qty != target:
                self.store.mark_capital_action_review(
                    action["id"],
                    f"aggregate order {order.status.value}: executed {order.executed_qty} of {target}",
                )
                raise RuntimeError("aggregate fill requires manual review")
            allocations = self._planned_allocations(action, order.executed_qty)
            self.store.apply_capital_action(action["id"], allocations, order.executed_qty)
            self.store.append_event(
                self.strategy_id,
                "CAPITAL_ACTION_APPLIED",
                {
                    "action_id": action["id"],
                    "operation": action["operation"],
                    "amount_usdt": action["amount_usdt"],
                    "executed_qty": str(order.executed_qty),
                    "average_price": str(order.average_price),
                    "sell_cell_count": len(allocations),
                },
                run_id=self.run_id,
            )
            self.config = None

    def _plan_capital_action(self, action: dict, mark: Decimal) -> dict:
        assert self.filters is not None
        if mark <= 0:
            raise ValueError("mark price must be positive")
        cells = self.store.list_cells(self.strategy_id)
        if any(cell.stage == CellStage.MANUAL_REVIEW for cell in cells):
            self.store.mark_capital_action_review(action["id"], "a grid cell requires manual review")
            raise ValueError("cannot add capital while a grid cell requires manual review")
        if action["operation"] == "seed":
            sell_cells = [cell for cell in cells if cell.sell_price > mark]
            allocations = {
                cell.cell_id: self._quantity(cell.buy_price, cell)
                for cell in sell_cells
            }
            if any(qty == 0 for qty in allocations.values()):
                raise ValueError("initial sell-side cell is below exchange minimum")
        else:
            invalidate = getattr(self.exchange, "invalidate_positions", None)
            if invalidate is not None:
                invalidate()
            actual_qty = sum(
                (position.quantity for position in self.exchange.get_positions()
                 if position.symbol == self._config().symbol and position.position_side == "LONG"),
                Decimal("0"),
            )
            logical_qty = sum((cell.open_qty for cell in cells), Decimal("0"))
            if abs(actual_qty - logical_qty) > self.filters.step_size:
                self.store.mark_capital_action_review(action["id"], "actual and logical positions differ")
                raise ValueError("actual and logical positions differ; top-up requires review")
            if self.exchange.get_available_balance("USDT") < Decimal(action["amount_usdt"]):
                self.store.mark_capital_action_rejected(
                    action["id"], "available USDT is below the top-up amount"
                )
                return self.store.get_capital_action(action["id"])
            sell_cells = [cell for cell in cells if cell.open_qty > 0]
            sell_ids = [cell.cell_id for cell in sell_cells]
            if action["expected_sell_cell_ids_json"] is not None:
                expected_ids = json.loads(action["expected_sell_cell_ids_json"])
                if sell_ids != expected_ids:
                    self.store.mark_capital_action_rejected(
                        action["id"], "sell-side cells changed since the top-up preview"
                    )
                    return self.store.get_capital_action(action["id"])
            increments = fixed_cell_budgets(Decimal(action["amount_usdt"]), len(cells))
            increment_by_id = {
                cell.cell_id: increment for cell, increment in zip(cells, increments)
            }
            allocations = {}
            for cell in sell_cells:
                old_target = self._quantity(cell.buy_price, cell)
                updated = replace(cell, budget_usdt=cell.budget_usdt + increment_by_id[cell.cell_id])
                new_target = self._quantity(cell.buy_price, updated)
                allocations[cell.cell_id] = max(Decimal("0"), new_target - old_target)
        sell_ids = [cell.cell_id for cell in sell_cells]
        target_qty = sum(allocations.values(), Decimal("0"))
        if sell_ids and (
            target_qty <= 0
            or target_qty < self.filters.min_qty
            or target_qty * mark < self.filters.min_notional
        ):
            message = "aggregate position or per-cell quantity is below exchange minimum"
            if action["operation"] == "topup":
                self.store.mark_capital_action_rejected(action["id"], message)
                return self.store.get_capital_action(action["id"])
            raise ValueError(message)
        digest = hashlib.sha1(action["id"].encode("utf-8")).hexdigest()[:18]
        client_order_id = f"fg-{digest}"
        self.store.plan_capital_action(
            action["id"], sell_ids, allocations, target_qty, client_order_id
        )
        return self.store.get_capital_action(action["id"])

    def _planned_allocations(self, action: dict, quantity: Decimal) -> dict[str, Decimal]:
        planned = action.get("planned_allocations_json")
        if planned is None:
            # Recover actions created before per-cell allocations were persisted.
            return self._split_quantity(quantity, json.loads(action["sell_cell_ids_json"]))
        allocations = {
            cell_id: Decimal(qty) for cell_id, qty in json.loads(planned).items()
        }
        if sum(allocations.values(), Decimal("0")) != quantity:
            raise ValueError("planned cell quantities do not match aggregate fill")
        return allocations

    def _split_quantity(self, quantity: Decimal, cell_ids: list[str]) -> dict[str, Decimal]:
        assert self.filters is not None
        if not cell_ids:
            if quantity != 0:
                raise ValueError("aggregate quantity has no owning cells")
            return {}
        step = self.filters.step_size
        if step <= 0 or quantity != round_down(quantity, step):
            raise ValueError("aggregate quantity violates exchange step size")
        units = int(quantity / step)
        per_cell, remainder = divmod(units, len(cell_ids))
        return {
            cell_id: Decimal(per_cell + (index < remainder)) * step
            for index, cell_id in enumerate(cell_ids)
        }

    def _arm_untriggered(self) -> None:
        for cell in self.store.list_cells(self.strategy_id):
            if cell.stage == CellStage.UNTRIGGERED:
                cell.stage = CellStage.PENDING_ENTRY
                self.store.save_cell(cell)

    def _resize_orders(self) -> None:
        config = self._config()
        for cell in self.store.list_cells(self.strategy_id):
            if cell.stage == CellStage.PENDING_ENTRY and cell.entry_order_id is not None:
                if self._window_entry_ids is not None and cell.cell_id not in self._window_entry_ids:
                    continue
                order = self.exchange.get_order(config.symbol, cell.entry_order_id)
                expected = self._quantity(cell.buy_price, cell)
                if order.status in {OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED} and order.original_qty != expected:
                    canceled = self.exchange.cancel_order(config.symbol, order.order_id)
                    if canceled.status not in {OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.FILLED}:
                        raise RuntimeError("entry cancellation is not confirmed")
                    cell.open_qty += canceled.executed_qty
                    cell.entry_order_id = None
                    cell.entry_client_id = ""
                    cell.stage = CellStage.PENDING_EXIT if cell.open_qty > 0 else CellStage.PENDING_ENTRY
                    self.store.save_cell(cell)
                    if cell.open_qty > 0:
                        self._ensure_exit(cell)
                    else:
                        self._place_entry(cell)
            elif cell.stage == CellStage.PENDING_EXIT and cell.exit_order_id is not None:
                if self._window_exit_ids is not None and cell.cell_id not in self._window_exit_ids:
                    continue
                order = self.exchange.get_order(config.symbol, cell.exit_order_id)
                remaining = order.original_qty - order.executed_qty
                if order.status in {OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED} and remaining != cell.open_qty:
                    canceled = self.exchange.cancel_order(config.symbol, order.order_id)
                    if canceled.status not in {OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.FILLED}:
                        raise RuntimeError("exit cancellation is not confirmed")
                    delta = max(Decimal("0"), canceled.executed_qty - cell.exit_executed_qty)
                    cell.open_qty = max(Decimal("0"), cell.open_qty - delta)
                    cell.exit_order_id = None
                    cell.exit_client_id = ""
                    cell.exit_executed_qty = Decimal("0")
                    cell.stage = CellStage.PENDING_EXIT if cell.open_qty > 0 else CellStage.PENDING_ENTRY
                    self.store.save_cell(cell)
                    if cell.open_qty > 0:
                        self._ensure_exit(cell)
                    else:
                        self._place_entry(cell)
