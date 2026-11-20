"""Recoverable, operator-triggered fixed-grid reconfiguration."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from ..domain import FixedEntryMode, OrderSide, OrderStatus, StrategyStatus
from ..ports.exchange import Exchange, ExchangeExecutionUnknownError, OrderNotFoundError
from ..infrastructure.okx import OkxAPIError
from ..infrastructure.sqlite_store import SQLiteStore
from .grid_performance import strategy_performance, sync_strategy_trades
from .service import GridService


TERMINAL = {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.REJECTED}


class FixedReconfigurationWorkflow:
    def __init__(self, store: SQLiteStore, service: GridService, exchange: Exchange) -> None:
        self.store = store
        self.service = service
        self.exchange = exchange

    def request(
        self, action_id: str, strategy_id: str, lower_price: Decimal,
        upper_price: Decimal, grid_count: int, grid_budget_usdt: Decimal,
    ) -> dict:
        return self.store.request_fixed_reconfiguration(
            action_id, strategy_id, lower_price, upper_price,
            grid_count, grid_budget_usdt,
        )

    def preview(
        self, strategy_id: str, lower_price: Decimal,
        upper_price: Decimal, grid_count: int, grid_budget_usdt: Decimal,
    ) -> dict:
        plan = self.service.preview_fixed_reconfiguration(
            strategy_id, lower_price, upper_price, grid_count, self.exchange,
            grid_budget_usdt=grid_budget_usdt, require_quiesced=False,
        )
        return self._public_plan(plan)

    @staticmethod
    def _public_plan(plan: dict) -> dict:
        config = plan["config"]
        return {
            "strategy_id": config.strategy_id,
            "grid_generation": config.grid_generation,
            "lower_price": str(config.lower_price),
            "upper_price": str(config.upper_price),
            "grid_count": config.grid_count,
            "grid_budget_usdt": str(config.grid_budget_usdt),
            "per_cell_usdt": str(plan["cells"][0].budget_usdt),
            "grid_ratio_pct": str(config.grid_ratio * 100),
            "mark": str(plan["mark"]),
            "old_qty": str(plan["old_qty"]),
            "actual_qty": str(plan["actual_qty"]),
            "target_qty": str(plan["target_qty"]),
            "delta_qty": str(plan["delta_qty"]),
            "delta_notional_usdt": str(plan["delta_qty"] * plan["mark"]),
            "sell_cell_count": len(plan["allocations"]),
            "buy_cell_count": len(plan["cells"]) - len(plan["allocations"]),
            "unallocated_downside_usdt": str(plan["unallocated_downside_usdt"]),
            "immediate_buy_usdt": str(plan["immediate_buy_usdt"]),
            "upper_seed_cell_count": len(plan["upper_seed_cell_ids"]),
            "upper_seed_frontier": config.upper_seed_frontier,
        }

    def _quiesce_orders(self, strategy_id: str) -> None:
        config = self.store.get_strategy(strategy_id)
        if config is None or config.status != StrategyStatus.STOPPED:
            raise ValueError("strategy must be stopped before order cancellation")
        cells = self.store.list_cells(strategy_id)
        references = {
            int(order_id): (cell.cell_id, role)
            for cell in cells
            for role, order_id in (
                ("entry", cell.entry_order_id), ("exit", cell.exit_order_id)
            )
            if order_id is not None
        }
        if any(
            (cell.entry_client_id and cell.entry_order_id is None)
            or (cell.exit_client_id and cell.exit_order_id is None)
            for cell in cells
        ):
            raise ValueError("an old cell has an unresolved order submission")
        open_orders = self.exchange.get_open_orders(config.symbol)
        unknown = [order.order_id for order in open_orders if order.order_id not in references]
        if unknown:
            raise ValueError(f"unowned open orders on the symbol: {unknown[:5]}")
        for order in open_orders:
            result = self.exchange.cancel_order(config.symbol, order.order_id)
            if result.status not in TERMINAL:
                raise ValueError(f"order {order.order_id} cancellation is not final")
        for order_id, (cell_id, role) in references.items():
            order = self.exchange.get_order(config.symbol, order_id)
            if order.status not in TERMINAL:
                raise ValueError(f"old order {order_id} remains open")
            self.store.settle_reconfiguration_order(strategy_id, cell_id, role, order)
        if self.exchange.get_open_orders(config.symbol):
            raise ValueError("new orders appeared during the reconfiguration freeze")
        cells = self.store.list_cells(strategy_id)
        if any(cell.entry_order_id is not None or cell.exit_order_id is not None
               or cell.stage.value == "manual_review" for cell in cells):
            raise ValueError("old order references remain unresolved")
        actual = sum(
            (p.quantity for p in self.exchange.get_positions()
             if p.symbol == config.symbol and p.position_side == "LONG"), Decimal("0"),
        )
        logical = sum((cell.open_qty for cell in cells), Decimal("0"))
        if actual != logical:
            raise ValueError(f"position mismatch after cancellation: actual {actual}, logical {logical}")

    def _prepare(self, action: dict) -> dict:
        strategy_id = action["strategy_id"]
        current = self.store.get_strategy(strategy_id)
        if current is None or current.grid_generation != int(action["expected_generation"]):
            raise ValueError("strategy generation changed before reconfiguration")
        if current.fixed_entry_mode == FixedEntryMode.PRICE_TRIGGERED:
            preflight = self.service.preview_fixed_reconfiguration(
                strategy_id, Decimal(action["lower_price"]), Decimal(action["upper_price"]),
                int(action["grid_count"]), self.exchange,
                grid_budget_usdt=Decimal(action["grid_budget_usdt"]),
                require_quiesced=False,
            )
            required = (preflight["immediate_buy_usdt"]
                        + max(preflight["delta_qty"], Decimal("0")) * preflight["mark"]
                        + Decimal("1000"))
            if self.exchange.get_available_balance("USDT") < required:
                raise ValueError(f"available USDT is below the immediate migration requirement {required}")
        active = {StrategyStatus.STARTING, StrategyStatus.RUNNING, StrategyStatus.ERROR}
        if any(item.status in active and item.strategy_id != strategy_id
               for item in self.store.list_strategies()):
            raise ValueError("another active strategy needs the shared scheduler")
        if current.status != StrategyStatus.STOPPED:
            self.service.stop(strategy_id)
        self.service.supervisor.shutdown_if_idle()
        self._quiesce_orders(strategy_id)
        plan = self.service.preview_fixed_reconfiguration(
            strategy_id, Decimal(action["lower_price"]), Decimal(action["upper_price"]),
            int(action["grid_count"]), self.exchange,
            grid_budget_usdt=Decimal(action["grid_budget_usdt"]),
        )
        coverage = sync_strategy_trades(self.store, self.exchange, current)
        performance = strategy_performance(self.store, current, coverage_complete=coverage)
        if not coverage or not performance["accounting_complete"]:
            raise ValueError("old grid fill and fee history is incomplete")
        if sum((Decimal(qty) for qty in performance["open_buy_qty_by_cell"].values()),
               Decimal("0")) != plan["old_qty"]:
            raise ValueError("old open fills do not match the settled position")
        frozen = {
            **self._public_plan(plan),
            "new_sell_cell_ids": plan["new_sell_cell_ids"],
            "upper_seed_cell_ids": plan["upper_seed_cell_ids"],
            "baseline_net_grid_profit_usdt": performance["net_grid_profit_usdt"],
            "baseline_completed_cycles": performance["completed_cycles"],
            "baseline_started_at": performance["started_at"],
            "baseline_initial_entry_price": performance["initial_entry_price"],
        }
        client_id = "gr" + hashlib.sha1(action["id"].encode()).hexdigest()[:20]
        return self.store.plan_fixed_reconfiguration(action["id"], frozen, client_id)

    def _frozen_plan(self, action: dict) -> dict:
        frozen = json.loads(action["plan_json"] or "{}")
        plan = self.service.preview_fixed_reconfiguration(
            action["strategy_id"], Decimal(action["lower_price"]),
            Decimal(action["upper_price"]), int(action["grid_count"]), self.exchange,
            grid_budget_usdt=Decimal(action["grid_budget_usdt"]),
            allow_adjusted_position=True,
            mark_override=Decimal(frozen["mark"]),
        )
        if (str(plan["old_qty"]) != frozen["old_qty"]
                or str(plan["target_qty"]) != frozen["target_qty"]
                or plan["new_sell_cell_ids"] != frozen["new_sell_cell_ids"]
                or plan["upper_seed_cell_ids"] != frozen["upper_seed_cell_ids"]
                or plan["config"].upper_seed_frontier != frozen["upper_seed_frontier"]
                or plan["config"].grid_generation != int(frozen["grid_generation"])):
            raise ValueError("frozen migration plan no longer matches old cells")
        return plan

    def execute(self, action_id: str) -> dict:
        action = self.store.get_fixed_reconfiguration(action_id)
        if action is None:
            raise KeyError(action_id)
        if action["status"] == "manual_review":
            raise ValueError(f"reconfiguration needs review: {action['message']}")
        if action["status"] == "applied":
            return self._restart(action)
        if action["status"] == "requested":
            action = self._prepare(action)
        plan = self._frozen_plan(action)
        delta = plan["delta_qty"]
        if (action["status"] == "planned"
                and plan["config"].fixed_entry_mode == FixedEntryMode.PRICE_TRIGGERED):
            required = (plan["immediate_buy_usdt"]
                        + max(delta, Decimal("0")) * plan["mark"]
                        + Decimal("1000"))
            if self.exchange.get_available_balance("USDT") < required:
                raise ValueError(f"available USDT is below the immediate migration requirement {required}")
        if action["status"] == "planned" and delta:
            if delta > 0 and self.exchange.get_available_balance("USDT") < delta * plan["mark"]:
                raise ValueError("available USDT is below the required additional position")
            action = self.store.mark_fixed_reconfiguration_submitted(action_id)
            side = OrderSide.BUY if delta > 0 else OrderSide.SELL
            try:
                order_id = self.exchange.place_market_order(
                    plan["config"].symbol, side, "LONG", abs(delta),
                    action["client_order_id"],
                )
            except ExchangeExecutionUnknownError:
                order_id = None
            except OkxAPIError as exc:
                self.store.mark_fixed_reconfiguration_review(action_id, str(exc))
                raise
            if order_id is not None:
                action = self.store.mark_fixed_reconfiguration_submitted(action_id, order_id)
        order_id = None
        if delta:
            try:
                order = self.exchange.get_order_by_client_id(
                    plan["config"].symbol, action["client_order_id"],
                )
            except OrderNotFoundError:
                return {"status": "submitted", "message": "adjustment result is not confirmed"}
            if order.status in {OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED}:
                return {"status": "submitted", "message": "adjustment is not fully filled"}
            if (order.status != OrderStatus.FILLED or order.executed_qty != abs(delta)
                    or order.side != (OrderSide.BUY if delta > 0 else OrderSide.SELL)
                    or order.position_side != "LONG"):
                self.store.mark_fixed_reconfiguration_review(
                    action_id, f"adjustment ended {order.status.value} with {order.executed_qty} of {abs(delta)}",
                )
                raise ValueError("adjustment order needs manual review")
            order_id = order.order_id
            action = self.store.mark_fixed_reconfiguration_submitted(action_id, order_id)
        self.service.apply_fixed_reconfiguration(
            plan, self.exchange, adjustment_order_id=order_id,
            action_id=action_id,
        )
        return self._restart(self.store.get_fixed_reconfiguration(action_id))

    def abort(self, action_id: str) -> dict:
        action = self.store.get_fixed_reconfiguration(action_id)
        if action is None:
            raise KeyError(action_id)
        if action["status"] not in {"requested", "planned", "manual_review"}:
            raise ValueError("submitted or completed reconfiguration cannot be aborted")
        config = self.store.get_strategy(action["strategy_id"])
        if config is None:
            raise KeyError(action["strategy_id"])
        if action["client_order_id"]:
            try:
                self.exchange.get_order_by_client_id(config.symbol, action["client_order_id"])
            except OrderNotFoundError:
                pass
            else:
                raise ValueError("an adjustment order exists; inspect it before aborting")
        if config.status == StrategyStatus.STOPPED:
            if self.exchange.get_open_orders(config.symbol):
                raise ValueError("open orders remain after reconfiguration stop")
            actual = sum(
                (p.quantity for p in self.exchange.get_positions()
                 if p.symbol == config.symbol and p.position_side == "LONG"), Decimal("0"),
            )
            logical = sum((cell.open_qty for cell in self.store.list_cells(config.strategy_id)), Decimal("0"))
            if actual != logical:
                raise ValueError("old position does not reconcile; cannot abort safely")
        self.store.abort_fixed_reconfiguration(action_id)
        if config.status == StrategyStatus.STOPPED:
            self.service.start(config.strategy_id)
        return {"id": action_id, "status": "aborted",
                "strategy_status": self.store.get_strategy(config.strategy_id).status.value}

    def _restart(self, action: dict) -> dict:
        config = self.store.get_strategy(action["strategy_id"])
        if config is None:
            raise KeyError(action["strategy_id"])
        if config.status == StrategyStatus.STOPPED:
            self.service.start(config.strategy_id)
        return {
            "id": action["id"], "strategy_id": config.strategy_id,
            "status": action["status"], "strategy_status": self.store.get_strategy(config.strategy_id).status.value,
            "grid_generation": config.grid_generation,
            "plan": json.loads(action["plan_json"] or "{}"),
        }
