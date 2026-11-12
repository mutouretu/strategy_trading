from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from decimal import Decimal
from urllib.parse import urlparse

from ..domain import CellStage, GridCell, OrderSide, OrderStatus, StrategyStatus
from ..domain.grid import fixed_cell_budgets, round_down
from ..infrastructure.binance import BinanceAPIError, BinanceFuturesExchange
from ..ports.exchange import ExchangeExecutionUnknownError, OrderNotFoundError
from .engine import TradingEngine


class FixedGridEngine(TradingEngine):
    """Fixed long grid with one exchange position and per-cell logical ownership."""

    def initialize(self) -> None:
        raw_exchange = getattr(self.exchange, "exchange", self.exchange)
        if isinstance(raw_exchange, BinanceFuturesExchange):
            host = urlparse(raw_exchange.base_url).hostname
            if host not in {"demo-fapi.binance.com", "testnet.binancefuture.com"}:
                raise ValueError("fixed equal-USDT grid is enabled on Binance testnet only")
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
        mark = getattr(self, "_tick_mark", None) or self.exchange.get_mark_price(symbol)
        if (side == OrderSide.BUY and mark <= price) or (
            side == OrderSide.SELL and mark >= price
        ):
            order_id = self.exchange.place_market_order(
                symbol, side, position_side, quantity, client_order_id
            )
            self.store.append_event(
                self.strategy_id,
                "GRID_CATCHUP_MARKET_ORDER",
                {
                    "order_id": order_id,
                    "client_order_id": client_order_id,
                    "side": side.value,
                    "target_price": str(price),
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
        if self.filters.percent_price_down > 0 and price < mark * self.filters.percent_price_down:
            return False
        if self.filters.percent_price_up > 0 and price > mark * self.filters.percent_price_up:
            return False
        return True

    def is_virtual_exit(self, cell: GridCell, mark: Decimal) -> bool:
        """A far sell target is valid grid state while Binance rejects its limit price."""
        assert self.filters is not None
        return (
            cell.open_qty > 0
            and cell.exit_order_id is None
            and mark < cell.sell_price
            and self.filters.percent_price_up > 0
            and cell.sell_price > mark * self.filters.percent_price_up
        )

    @staticmethod
    def _retryable_price_rejection(exc: BinanceAPIError) -> bool:
        return any(code in str(exc) for code in ("-4016", "-4024", "-5022"))

    def _place_entry(self, cell: GridCell) -> None:
        if not self._order_is_ready(OrderSide.BUY, cell.buy_price):
            return
        try:
            super()._place_entry(cell)
        except BinanceAPIError as exc:
            if not self._retryable_price_rejection(exc):
                raise

    def _ensure_exit(self, cell: GridCell, quantity: Decimal | None = None) -> None:
        if not self._order_is_ready(OrderSide.SELL, cell.sell_price):
            return
        try:
            super()._ensure_exit(cell, quantity)
        except BinanceAPIError as exc:
            if not self._retryable_price_rejection(exc):
                raise

    def _recovery_mismatches(self, cell: GridCell, role: str, order) -> list[str]:
        # A top-up changes the desired entry size; a still-open old order is
        # resized after restart instead of being treated as an alien order.
        # A crossed virtual grid level is caught up with a market order; Binance
        # reports its limit price as zero when recovering by client ID.
        return [
            mismatch
            for mismatch in super()._recovery_mismatches(cell, role, order)
            if not (
                (role == "entry" and mismatch.startswith("qty:"))
                or (
                    order.status == OrderStatus.FILLED
                    and order.price == 0
                    and mismatch.startswith("price:")
                )
            )
        ]

    def tick(self) -> Decimal:
        if not self.initialized:
            self.initialize()
        config = self._config()
        mark = self.exchange.get_mark_price(config.symbol)
        self._tick_mark = mark
        seed_id = f"seed:{self.strategy_id}"
        seed = self.store.get_capital_action(seed_id)
        if seed is None:
            self._preflight_seed()
            assert config.grid_budget_usdt is not None
            self.store.create_capital_action(
                seed_id, self.strategy_id, "seed", config.grid_budget_usdt
            )
            seed = self.store.get_capital_action(seed_id)

        if seed["status"] == "manual_review":
            raise RuntimeError(f"initial position needs manual review: {seed['message']}")
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

        self._arm_untriggered()
        self.sync_orders_only()
        self._resize_orders()
        self._heartbeat(mark)
        return mark

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
        if self.exchange.get_available_balance("USDT") < config.grid_budget_usdt:
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
