from __future__ import annotations

import re
import uuid
from dataclasses import replace
from decimal import Decimal

from ..domain import ExchangeVenue, FixedEntryMode, FuturesMarket, GridKind, Mode, OrderSide, OrderStatus, StrategyConfig, StrategyStatus
from ..ports.exchange import Exchange
from ..domain.grid import build_cells, fixed_cell_budgets, fixed_grid_ratio, round_down
from .grid_performance import strategy_performance, sync_strategy_trades
from ..infrastructure.sqlite_store import SQLiteStore
from ..runtime.supervisor import StrategySupervisor


class GridService:
    def __init__(self, store: SQLiteStore, supervisor: StrategySupervisor | None = None) -> None:
        self.store = store
        self.supervisor = supervisor or StrategySupervisor(store)

    def preview(
        self,
        symbol: str,
        mode: Mode,
        anchor_price: Decimal,
        grid_ratio: Decimal,
        grid_count: int,
        order_usdt: Decimal,
        leverage: int,
        tick_size: Decimal,
        strategy_id: str = "preview",
        poll_interval_sec: float = 1.0,
        move_grid: bool = True,
        market_type: FuturesMarket = FuturesMarket.USDM,
        order_coin_qty: Decimal | None = None,
        contract_size: Decimal = Decimal("0"),
    ) -> tuple[StrategyConfig, list]:
        config = StrategyConfig(
            strategy_id=strategy_id,
            symbol=symbol.strip().upper(),
            mode=mode,
            anchor_price=anchor_price,
            grid_ratio=grid_ratio,
            grid_count=grid_count,
            order_usdt=order_usdt,
            market_type=market_type,
            leverage=leverage,
            poll_interval_sec=poll_interval_sec,
            move_grid=move_grid,
            order_coin_qty=order_coin_qty,
            contract_size=contract_size,
        )
        return config, build_cells(config, tick_size)

    def create(
        self,
        symbol: str,
        mode: Mode,
        anchor_price: Decimal,
        grid_ratio: Decimal,
        grid_count: int,
        order_usdt: Decimal,
        leverage: int,
        tick_size: Decimal,
        poll_interval_sec: float = 1.0,
        move_grid: bool = True,
        market_type: FuturesMarket = FuturesMarket.USDM,
        order_coin_qty: Decimal | None = None,
        contract_size: Decimal = Decimal("0"),
    ) -> StrategyConfig:
        clean_symbol = symbol.strip().upper()
        slug = re.sub(r"[^a-z0-9]+", "-", clean_symbol.lower()).strip("-")
        strategy_id = f"{slug}-{market_type.value}-{mode.value}-{uuid.uuid4().hex[:8]}"
        config, cells = self.preview(
            clean_symbol,
            mode,
            anchor_price,
            grid_ratio,
            grid_count,
            order_usdt,
            leverage,
            tick_size,
            strategy_id,
            poll_interval_sec,
            move_grid,
            market_type,
            order_coin_qty,
            contract_size,
        )
        self.store.create_strategy(config)
        self.store.replace_cells(strategy_id, cells)
        self.store.append_event(strategy_id, "STRATEGY_CREATED", {"cell_count": len(cells)})
        return config

    def preview_fixed(
        self,
        symbol: str,
        lower_price: Decimal,
        upper_price: Decimal,
        grid_count: int,
        grid_budget_usdt: Decimal,
        tick_size: Decimal,
        *,
        strategy_id: str = "preview-fixed",
        poll_interval_sec: float = 1.0,
        venue: ExchangeVenue = ExchangeVenue.BINANCE,
        auto_reinvest_enabled: bool = False,
        auto_reinvest_percent: Decimal = Decimal("100"),
        fixed_entry_mode: FixedEntryMode = FixedEntryMode.SEEDED,
        upper_seed_count: int = 0,
    ) -> tuple[StrategyConfig, list]:
        budgets = fixed_cell_budgets(grid_budget_usdt, grid_count)
        config = StrategyConfig(
            strategy_id=strategy_id,
            symbol=symbol.strip().upper(),
            mode=Mode.LONG,
            anchor_price=upper_price,
            grid_ratio=fixed_grid_ratio(lower_price, upper_price, grid_count),
            grid_count=grid_count,
            order_usdt=budgets[0],
            market_type=FuturesMarket.USDM,
            venue=venue,
            leverage=1,
            poll_interval_sec=poll_interval_sec,
            move_grid=False,
            grid_kind=GridKind.FIXED_EQUAL_USDT,
            fixed_entry_mode=fixed_entry_mode,
            upper_seed_count=upper_seed_count,
            lower_price=lower_price,
            upper_price=upper_price,
            grid_budget_usdt=grid_budget_usdt,
            auto_reinvest_enabled=auto_reinvest_enabled,
            auto_reinvest_percent=auto_reinvest_percent,
        )
        return config, build_cells(config, tick_size)

    def create_fixed(
        self,
        symbol: str,
        lower_price: Decimal,
        upper_price: Decimal,
        grid_count: int,
        grid_budget_usdt: Decimal,
        tick_size: Decimal,
        *,
        poll_interval_sec: float = 1.0,
        venue: ExchangeVenue = ExchangeVenue.BINANCE,
        auto_reinvest_enabled: bool = False,
        auto_reinvest_percent: Decimal = Decimal("100"),
        fixed_entry_mode: FixedEntryMode = FixedEntryMode.SEEDED,
        upper_seed_count: int = 0,
    ) -> StrategyConfig:
        clean_symbol = symbol.strip().upper()
        slug = re.sub(r"[^a-z0-9]+", "-", clean_symbol.lower()).strip("-")
        strategy_id = f"{slug}-fixed-{uuid.uuid4().hex[:8]}"
        config, cells = self.preview_fixed(
            clean_symbol,
            lower_price,
            upper_price,
            grid_count,
            grid_budget_usdt,
            tick_size,
            strategy_id=strategy_id,
            poll_interval_sec=poll_interval_sec,
            venue=venue,
            auto_reinvest_enabled=auto_reinvest_enabled,
            auto_reinvest_percent=auto_reinvest_percent,
            fixed_entry_mode=fixed_entry_mode,
            upper_seed_count=upper_seed_count,
        )
        self.store.create_strategy(config)
        self.store.replace_cells(strategy_id, cells)
        self.store.append_event(strategy_id, "STRATEGY_CREATED", {"cell_count": len(cells), "grid_kind": config.grid_kind.value})
        return config

    def update_draft(self, config: StrategyConfig, tick_size: Decimal) -> None:
        self.store.update_draft(config)
        self.store.replace_cells(config.strategy_id, build_cells(config, tick_size))
        self.store.append_event(config.strategy_id, "STRATEGY_UPDATED", {})

    def preview_fixed_reconfiguration(
        self, strategy_id: str, lower_price: Decimal, upper_price: Decimal,
        grid_count: int, exchange: Exchange, *,
        grid_budget_usdt: Decimal | None = None,
        allow_adjusted_position: bool = False,
        require_quiesced: bool = True,
        mark_override: Decimal | None = None,
    ) -> dict:
        current = self.store.get_strategy(strategy_id)
        if current is None:
            raise KeyError(strategy_id)
        if current.grid_kind != GridKind.FIXED_EQUAL_USDT or current.grid_budget_usdt is None:
            raise ValueError("reconfiguration requires a fixed equal-USDT grid")
        if current.fixed_entry_mode == FixedEntryMode.PRICE_TRIGGERED:
            raise ValueError("reconfiguration of a price-triggered grid is not yet supported")
        if not 1 <= grid_count <= 500:
            raise ValueError("fixed grid count must be between 1 and 500")
        if not current.has_started:
            raise ValueError("strategy must have started before reconfiguration")
        budget = current.grid_budget_usdt if grid_budget_usdt is None else grid_budget_usdt
        fixed_cell_budgets(budget, grid_count)
        if require_quiesced and current.status != StrategyStatus.STOPPED:
            raise ValueError("stop the strategy before reconfiguration")
        filters = exchange.get_symbol_filters(current.symbol)
        mark = exchange.get_mark_price(current.symbol) if mark_override is None else mark_override
        if not lower_price <= mark <= upper_price:
            raise ValueError("current price is outside new grid boundaries")
        if require_quiesced and exchange.get_open_orders(current.symbol):
            raise ValueError("cancel and reconcile all symbol orders before reconfiguration")
        old_cells = self.store.list_cells(strategy_id)
        if require_quiesced and any(
            cell.entry_order_id is not None or cell.exit_order_id is not None
            or cell.stage.value == "manual_review" for cell in old_cells
        ):
            raise ValueError("old cell orders must be reconciled before reconfiguration")
        old_qty = sum((cell.open_qty for cell in old_cells), Decimal("0"))
        actual_qty = sum(
            (position.quantity for position in exchange.get_positions()
             if position.symbol == current.symbol and position.position_side == "LONG"),
            Decimal("0"),
        )
        if require_quiesced and not allow_adjusted_position and actual_qty != old_qty:
            raise ValueError("actual and logical long positions differ before adjustment")
        config = replace(
            current,
            anchor_price=upper_price,
            lower_price=lower_price,
            upper_price=upper_price,
            grid_count=grid_count,
            grid_ratio=fixed_grid_ratio(lower_price, upper_price, grid_count),
            order_usdt=fixed_cell_budgets(budget, grid_count)[0],
            grid_budget_usdt=budget,
            grid_generation=current.grid_generation + 1,
        )
        cells = build_cells(config, filters.tick_size)
        allocations = {
            cell.cell_id: round_down(cell.budget_usdt / cell.buy_price, filters.step_size)
            for cell in cells if cell.sell_price > mark
        }
        if any(qty < filters.min_qty or qty * cell.buy_price < filters.min_notional
               for cell in cells if (qty := allocations.get(cell.cell_id)) is not None):
            raise ValueError("a new sell-side cell is below the exchange minimum")
        target_qty = sum(allocations.values(), Decimal("0"))
        return {
            "config": config, "cells": cells, "allocations": allocations,
            "mark": mark, "old_qty": old_qty, "target_qty": target_qty,
            "actual_qty": actual_qty,
            "old_budget_usdt": current.grid_budget_usdt,
            "delta_qty": target_qty - old_qty,
            "new_sell_cell_ids": list(allocations),
        }

    def apply_fixed_reconfiguration(
        self, plan: dict, exchange: Exchange, *, adjustment_order_id: int | None = None,
        action_id: str | None = None,
    ) -> StrategyConfig:
        """Commit a preview after any required market adjustment is confirmed."""
        config: StrategyConfig = plan["config"]
        fresh = self.preview_fixed_reconfiguration(
            config.strategy_id, config.lower_price, config.upper_price,
            config.grid_count, exchange,
            grid_budget_usdt=config.grid_budget_usdt,
            allow_adjusted_position=True, mark_override=plan["mark"],
        )
        if (fresh["old_qty"] != plan["old_qty"]
                or fresh["target_qty"] != plan["target_qty"]
                or fresh["new_sell_cell_ids"] != plan["new_sell_cell_ids"]
                or fresh["config"].grid_generation != config.grid_generation):
            raise ValueError("reconfiguration preview is stale")
        delta = plan["delta_qty"]
        if (delta != 0) != (adjustment_order_id is not None):
            raise ValueError("position delta requires one confirmed adjustment order")
        if adjustment_order_id is not None:
            order = exchange.get_order(config.symbol, adjustment_order_id)
            expected_side = OrderSide.BUY if delta > 0 else OrderSide.SELL
            if (order.status != OrderStatus.FILLED or order.side != expected_side
                    or order.position_side != "LONG" or order.executed_qty != abs(delta)):
                raise ValueError("position adjustment order is not fully confirmed")
        actual_qty = sum(
            (position.quantity for position in exchange.get_positions()
             if position.symbol == config.symbol and position.position_side == "LONG"),
            Decimal("0"),
        )
        if actual_qty != plan["target_qty"]:
            raise ValueError("exchange position does not match new sell-side quantity")
        old_config = self.store.get_strategy(config.strategy_id)
        assert old_config is not None
        coverage = sync_strategy_trades(self.store, exchange, old_config)
        performance = strategy_performance(self.store, old_config, coverage_complete=coverage)
        if not coverage or not performance["accounting_complete"]:
            raise ValueError("old trade history or fee accounting is incomplete")
        remaining_qty = sum(
            (Decimal(value) for value in performance["open_buy_qty_by_cell"].values()),
            Decimal("0"),
        )
        if remaining_qty != plan["old_qty"]:
            raise ValueError("old open fill quantity does not match logical cells")
        old_fees = sum(
            (Decimal(value) for value in performance["open_buy_fees_by_cell_usdt"].values()),
            Decimal("0"),
        )
        retained_old = min(plan["old_qty"], plan["target_qty"])
        transferred_fees = old_fees * retained_old / plan["old_qty"] if plan["old_qty"] else Decimal("0")
        if delta > 0:
            fills = [trade for trade in self.store.list_strategy_trades(config.strategy_id)
                     if trade["order_id"] == adjustment_order_id]
            if (sum((Decimal(fill["quantity"]) for fill in fills), Decimal("0")) != delta
                    or any(fill["side"] != "BUY" or fill["commission_asset"] != "USDT" for fill in fills)):
                raise ValueError("adjustment opening fills or fees are incomplete")
            transferred_fees += sum((Decimal(fill["commission"]) for fill in fills), Decimal("0"))
        opening_fees: dict[str, Decimal] = {}
        allocation_items = list(plan["allocations"].items())
        for cell_id, qty in allocation_items[:-1]:
            opening_fees[cell_id] = transferred_fees * qty / plan["target_qty"]
        if allocation_items:
            opening_fees[allocation_items[-1][0]] = transferred_fees - sum(
                opening_fees.values(), Decimal("0")
            )
        self.store.reconfigure_fixed_cells(
            config, plan["cells"], plan["allocations"], opening_fees,
            expected_generation=old_config.grid_generation, mark=plan["mark"],
            position_delta_qty=delta, position_delta_order_id=adjustment_order_id,
            action_id=action_id,
        )
        return self.store.get_strategy(config.strategy_id)

    def preview_flat_triggered_reset(
        self, strategy_id: str, lower_price: Decimal, upper_price: Decimal,
        grid_count: int, grid_budget_usdt: Decimal, exchange: Exchange,
    ) -> dict:
        """Plan a new zero-position generation on an existing stopped strategy."""
        current = self.store.get_strategy(strategy_id)
        if current is None:
            raise KeyError(strategy_id)
        if (current.grid_kind != GridKind.FIXED_EQUAL_USDT
                or current.fixed_entry_mode != FixedEntryMode.SEEDED
                or current.venue != self.store.venue
                or current.venue not in {ExchangeVenue.BINANCE, ExchangeVenue.OKX}
                or current.status != StrategyStatus.STOPPED
                or not current.has_started or current.archived):
            raise ValueError("flat triggered reset requires a stopped seeded fixed grid on the selected venue")
        if not 1 <= grid_count <= 500:
            raise ValueError("fixed grid count must be between 1 and 500")
        if any(
            item["status"] in {"requested", "planned", "submitted", "manual_review"}
            for item in self.store.list_fixed_reconfigurations(strategy_id)
        ):
            raise ValueError("a grid reconfiguration is still pending")
        if any(
            item["status"] in {"requested", "planned", "submitted", "manual_review"}
            for item in self.store.list_capital_actions(strategy_id)
        ):
            raise ValueError("a capital action is still pending")
        if strategy_id in self.store.pending_cell_action_strategy_ids():
            raise ValueError("a cell action is still pending")
        seed = self.store.get_capital_action(f"seed:{strategy_id}")
        if seed is None or seed["status"] != "applied":
            raise ValueError("the old seed action is not complete")
        if exchange.get_open_orders(current.symbol):
            raise ValueError("exchange still has open symbol orders")
        if any(
            position.symbol == current.symbol and position.quantity != 0
            for position in exchange.get_positions()
        ):
            raise ValueError("exchange symbol position is not flat")
        mark = exchange.get_mark_price(current.symbol)
        if not lower_price <= mark <= upper_price:
            raise ValueError("current price is outside new grid boundaries")
        balance = exchange.get_available_balance("USDT")
        if balance < grid_budget_usdt:
            raise ValueError("available USDT is below the new grid budget")
        filters = exchange.get_symbol_filters(current.symbol)
        budgets = fixed_cell_budgets(grid_budget_usdt, grid_count)
        config = replace(
            current,
            anchor_price=upper_price,
            grid_ratio=fixed_grid_ratio(lower_price, upper_price, grid_count),
            grid_count=grid_count,
            order_usdt=budgets[0],
            lower_price=lower_price,
            upper_price=upper_price,
            grid_budget_usdt=grid_budget_usdt,
            grid_generation=current.grid_generation + 1,
            fixed_entry_mode=FixedEntryMode.PRICE_TRIGGERED,
        )
        cells = build_cells(config, filters.tick_size)
        if any(
            (qty := round_down(cell.budget_usdt / cell.buy_price, filters.step_size))
            < filters.min_qty or qty * cell.buy_price < filters.min_notional
            for cell in cells
        ):
            raise ValueError("a new cell is below the exchange order minimum")
        old_cells = self.store.list_cells(strategy_id)
        if {cell.cell_id for cell in cells} & {cell.cell_id for cell in old_cells}:
            raise ValueError("new cell IDs overlap old cells")
        return {
            "config": config,
            "cells": cells,
            "mark": mark,
            "available_balance": balance,
            "old_cell_count": len(old_cells),
            "old_logical_qty": sum((cell.open_qty for cell in old_cells), Decimal("0")),
            "old_order_references": sum(
                (cell.entry_order_id is not None) + (cell.exit_order_id is not None)
                for cell in old_cells
            ),
        }

    def reset_flat_fixed_to_triggered(
        self, strategy_id: str, lower_price: Decimal, upper_price: Decimal,
        grid_count: int, grid_budget_usdt: Decimal, exchange: Exchange,
    ) -> dict:
        """Discard old cell state after the account is confirmed flat."""
        plan = self.preview_flat_triggered_reset(
            strategy_id, lower_price, upper_price, grid_count,
            grid_budget_usdt, exchange,
        )
        old_config = self.store.get_strategy(strategy_id)
        assert old_config is not None
        coverage = sync_strategy_trades(self.store, exchange, old_config)
        performance = strategy_performance(
            self.store, old_config, coverage_complete=coverage,
        )
        if (not coverage or not performance["accounting_complete"]
                or performance["net_grid_profit_usdt"] is None):
            raise ValueError("old strategy trade or fee accounting is incomplete")
        old_fill_qty = sum(
            (Decimal(value) for value in performance["open_buy_qty_by_cell"].values()),
            Decimal("0"),
        )
        if old_fill_qty != plan["old_logical_qty"]:
            raise ValueError("old open fills differ from the logical cell quantity")
        fresh = self.preview_flat_triggered_reset(
            strategy_id, lower_price, upper_price, grid_count,
            grid_budget_usdt, exchange,
        )
        if (fresh["old_logical_qty"] != plan["old_logical_qty"]
                or fresh["old_cell_count"] != plan["old_cell_count"]):
            raise ValueError("old cell state changed during the reset preflight")
        self.store.reset_flat_fixed_to_triggered(
            fresh["config"], fresh["cells"],
            expected_generation=old_config.grid_generation,
            expected_old_cell_count=fresh["old_cell_count"],
            expected_old_qty=fresh["old_logical_qty"],
            historical_net_grid_profit=Decimal(performance["net_grid_profit_usdt"]),
            mark=fresh["mark"],
        )
        return {
            "strategy_id": strategy_id,
            "grid_generation": fresh["config"].grid_generation,
            "old_cell_count": fresh["old_cell_count"],
            "new_cell_count": len(fresh["cells"]),
            "old_logical_qty_discarded": str(fresh["old_logical_qty"]),
            "historical_net_grid_profit_usdt": performance["net_grid_profit_usdt"],
            "fixed_entry_mode": FixedEntryMode.PRICE_TRIGGERED.value,
        }

    def start(self, strategy_id: str) -> int:
        config = self.store.get_strategy(strategy_id)
        if config is None:
            raise KeyError(strategy_id)
        pending_reconfiguration = next(
            (item for item in self.store.list_fixed_reconfigurations(strategy_id)
             if item["status"] in {"requested", "planned", "submitted", "manual_review"}),
            None,
        )
        if pending_reconfiguration is not None:
            raise ValueError("finish or abort the pending grid reconfiguration before starting")
        if config.grid_kind == GridKind.FIXED_EQUAL_USDT:
            seed = self.store.get_capital_action(f"seed:{strategy_id}")
            if seed is not None and seed["status"] == "manual_review":
                raise ValueError(
                    "initial position requires review; resolve the exchange error "
                    "and create a new fixed grid"
                )
        for other in self.store.list_strategies():
            if other.strategy_id == strategy_id or not other.has_started:
                continue
            if other.status not in {StrategyStatus.STARTING, StrategyStatus.RUNNING, StrategyStatus.ERROR}:
                continue
            if other.market_type == config.market_type and other.symbol == config.symbol:
                if config.grid_kind == GridKind.FIXED_EQUAL_USDT or other.grid_kind == GridKind.FIXED_EQUAL_USDT:
                    raise ValueError("a fixed grid requires exclusive use of its symbol")
        return self.supervisor.start(strategy_id)

    def stop(self, strategy_id: str) -> None:
        self.supervisor.stop(strategy_id)

    def archive(self, strategy_id: str) -> None:
        if self.supervisor.is_running(strategy_id):
            self.supervisor.stop(strategy_id)
        self.store.archive_strategy(strategy_id)

    def delete(self, strategy_id: str) -> None:
        if self.supervisor.is_running(strategy_id):
            self.supervisor.stop(strategy_id)
        self.store.soft_delete_strategy(strategy_id)

    def refresh_price(self, strategy_id: str, exchange: Exchange) -> Decimal:
        config = self.store.get_strategy(strategy_id)
        if config is None:
            raise KeyError(strategy_id)
        price = exchange.get_mark_price(config.symbol)
        runtime = self.store.get_runtime(strategy_id)
        self.store.heartbeat(
            strategy_id,
            run_id=str(runtime["run_id"]) if runtime and runtime.get("run_id") else "manual-refresh",
            pid=int(runtime["pid"]) if runtime and runtime.get("pid") else 0,
            mark_price=price,
        )
        return price

    def editable_copy(self, strategy_id: str, **changes) -> StrategyConfig:
        config = self.store.get_strategy(strategy_id)
        if config is None:
            raise KeyError(strategy_id)
        if config.has_started or config.archived:
            raise ValueError("configuration is immutable after first start or archive")
        return replace(config, **changes, status=StrategyStatus.DRAFT)
