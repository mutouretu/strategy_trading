from __future__ import annotations

import os
import inspect
from decimal import Decimal
from pathlib import Path
from typing import Callable, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..infrastructure.binance import BinanceCoinMExchange, BinanceFuturesExchange
from ..shared.config import (
    binance_base_url,
    binance_coinm_base_url,
    binance_credentials,
    load_environment,
)
from ..domain import FuturesMarket, GridKind, Mode, StrategyConfig
from ..domain.grid import fixed_cell_budgets, round_down
from ..application.service import GridService
from ..infrastructure.sqlite_store import SQLiteStore
from ..runtime.supervisor import StrategySupervisor


class StrategyInput(BaseModel):
    symbol: str = Field(min_length=1)
    market_type: FuturesMarket = FuturesMarket.USDM
    mode: Mode
    anchor_price: Decimal = Field(gt=0)
    grid_ratio: Decimal = Field(gt=0, description="Decimal ratio, e.g. 0.005 for 0.5%")
    grid_count: int = Field(ge=1, le=100)
    order_usdt: Decimal | None = Field(default=None, gt=0)
    order_coin_qty: Decimal | None = Field(default=None, gt=0)
    leverage: int = Field(ge=1, le=125)
    poll_interval_sec: float = Field(default=1.0, ge=0.2)
    move_grid: bool = True


class CellActionInput(BaseModel):
    operation: Literal["add", "remove"]
    boundary: Literal["lower", "upper"]


class FixedGridInput(BaseModel):
    symbol: str = Field(min_length=1)
    lower_price: Decimal = Field(gt=0)
    upper_price: Decimal = Field(gt=0)
    grid_count: int = Field(ge=1, le=100)
    grid_budget_usdt: Decimal = Field(gt=0)
    poll_interval_sec: float = Field(default=1.0, ge=0.2)


class TopupInput(BaseModel):
    request_id: str = Field(min_length=8, max_length=80)
    amount_usdt: Decimal = Field(gt=0)
    expected_sell_cell_ids: list[str] | None = None


class TopupPreviewInput(BaseModel):
    amount_usdt: Decimal = Field(gt=0)


def strategy_payload(config: StrategyConfig, store: SQLiteStore) -> dict:
    cells = store.list_cells(config.strategy_id)
    runtime = store.get_runtime(config.strategy_id)
    lower_price = min((cell.buy_price for cell in cells), default=None)
    upper_price = max((cell.sell_price for cell in cells), default=None)
    return {
        "strategy_id": config.strategy_id,
        "symbol": config.symbol,
        "market_type": config.market_type.value,
        "grid_kind": config.grid_kind.value,
        "grid_budget_usdt": None if config.grid_budget_usdt is None else str(config.grid_budget_usdt),
        "quantity_unit": "base_asset",
        "mode": config.mode.value,
        "anchor_price": str(config.anchor_price),
        "grid_ratio": str(config.grid_ratio),
        "grid_count": config.grid_count,
        "order_usdt": str(config.order_usdt),
        "order_coin_qty": (
            None if config.order_coin_qty is None else str(config.order_coin_qty)
        ),
        "order_amount": str(
            config.order_base_quantity
            if config.market_type == FuturesMarket.COINM
            else config.order_value_usd
        ),
        "order_unit": (
            _base_asset(config.symbol)
            if config.market_type == FuturesMarket.COINM
            else "USD"
        ),
        "contract_size": str(config.contract_size),
        "leverage": config.leverage,
        "poll_interval_sec": config.poll_interval_sec,
        "move_grid": config.move_grid,
        "status": config.status.value,
        "has_started": config.has_started,
        "archived": config.archived,
        "current_price": runtime.get("mark_price") if runtime else None,
        "lower_price": str(config.lower_price) if config.lower_price is not None else (None if lower_price is None else str(lower_price)),
        "upper_price": str(config.upper_price) if config.upper_price is not None else (None if upper_price is None else str(upper_price)),
        "pending_entry": sum(cell.stage.value == "pending_entry" for cell in cells),
        "entered": sum(cell.open_qty > 0 for cell in cells),
        "pending_exit": sum(cell.stage.value == "pending_exit" for cell in cells),
        "manual_review": sum(cell.stage.value == "manual_review" for cell in cells),
        "cells": len(cells),
        "heartbeat_at": runtime.get("heartbeat_at") if runtime else None,
        "started_at": runtime.get("started_at") if runtime else None,
        "stopped_at": runtime.get("stopped_at") if runtime else None,
        "last_error": runtime.get("last_error") if runtime else None,
    }


def cell_payload(
    cell,
    order_quantities: dict[int, Decimal] | None = None,
    config: StrategyConfig | None = None,
) -> dict:
    quantities = order_quantities or {}
    entry_qty = quantities.get(cell.entry_order_id) if cell.entry_order_id is not None else None
    exit_qty = quantities.get(cell.exit_order_id) if cell.exit_order_id is not None else None
    if entry_qty is None and cell.entry_order_id is not None and cell.open_qty > 0:
        entry_qty = cell.open_qty
    if exit_qty is not None:
        exit_qty = max(Decimal("0"), exit_qty - cell.exit_executed_qty)
    elif cell.exit_order_id is not None:
        exit_qty = cell.open_qty
    entry_contracts = entry_qty
    exit_contracts = exit_qty
    if config is not None and config.market_type == FuturesMarket.COINM:
        entry_price = cell.buy_price if config.mode == Mode.LONG else cell.sell_price
        exit_price = cell.sell_price if config.mode == Mode.LONG else cell.buy_price
        entry_qty = _contracts_to_coin(entry_qty, entry_price, config.contract_size)
        exit_qty = _contracts_to_coin(exit_qty, exit_price, config.contract_size)
    config_side = {
        "cell_id": cell.cell_id,
        "index": cell.index,
        "buy_price": str(cell.buy_price),
        "sell_price": str(cell.sell_price),
        "stage": cell.stage.value,
        "entry_order_id": cell.entry_order_id,
        "exit_order_id": cell.exit_order_id,
        "entry_client_id": cell.entry_client_id,
        "exit_client_id": cell.exit_client_id,
        "entry_qty": None if entry_qty is None else str(entry_qty),
        "exit_qty": None if exit_qty is None else str(exit_qty),
        "entry_contracts": None if entry_contracts is None else str(entry_contracts),
        "exit_contracts": None if exit_contracts is None else str(exit_contracts),
        "open_qty": str(cell.open_qty),
        "exit_executed_qty": str(cell.exit_executed_qty),
        "entry_filled_at": cell.entry_filled_at or None,
        "cycle_count": cell.cycle_count,
        "budget_usdt": str(cell.budget_usdt),
    }
    return config_side


def _base_asset(symbol: str) -> str:
    root = symbol.upper().split("_", 1)[0]
    for quote in ("USDT", "USDC", "FDUSD", "BUSD", "USD"):
        if root.endswith(quote):
            return root[: -len(quote)]
    return root


def _contracts_to_coin(
    contracts: Decimal | None,
    price: Decimal,
    contract_size: Decimal,
) -> Decimal | None:
    if contracts is None or price <= 0 or contract_size <= 0:
        return contracts
    return contracts * contract_size / price


def _strategy_amounts(
    data: StrategyInput,
    contract_size: Decimal,
) -> tuple[Decimal, Decimal | None]:
    if data.market_type == FuturesMarket.COINM:
        if data.order_coin_qty is None:
            raise ValueError("币本位必须填写单格币数量")
        if contract_size <= 0:
            raise ValueError("币本位合约缺少有效的 contractSize")
        return Decimal("0"), data.order_coin_qty
    if data.order_usdt is None:
        raise ValueError("U 本位必须填写单格 USD 金额")
    return data.order_usdt, None


def fixed_grid_preview_details(
    cells, mark: Decimal, filters, maker_rate: Decimal, taker_rate: Decimal
) -> dict:
    if mark <= 0:
        raise ValueError("invalid current price")
    net_profits = []
    taker_net_profits = []
    maker_returns = []
    taker_returns = []
    quantities = {}
    for cell in cells:
        qty = round_down(cell.budget_usdt / cell.buy_price, filters.step_size)
        if qty < filters.min_qty or qty * cell.buy_price < filters.min_notional:
            raise ValueError(f"Cell #{cell.index} is below exchange minimum order size")
        quantities[cell.cell_id] = qty
        net = qty * (
            cell.sell_price - cell.buy_price
            - cell.buy_price * maker_rate
            - cell.sell_price * maker_rate
        )
        if net <= 0:
            raise ValueError(f"Cell #{cell.index} grid profit does not cover maker fees")
        net_profits.append(net)
        maker_returns.append(net / (qty * cell.buy_price))
        taker_net = qty * (
            cell.sell_price - cell.buy_price
            - cell.buy_price * taker_rate
            - cell.sell_price * taker_rate
        )
        if taker_net <= 0:
            raise ValueError(f"Cell #{cell.index} grid profit does not cover taker fees")
        taker_net_profits.append(taker_net)
        taker_returns.append(taker_net / (qty * cell.buy_price))
    sell_cells = [cell for cell in cells if cell.sell_price > mark]
    seed_notional = sum((cell.budget_usdt for cell in sell_cells), Decimal("0"))
    seed_qty = sum((quantities[cell.cell_id] for cell in sell_cells), Decimal("0"))
    seed_market_buy = seed_qty * mark
    sell_order_notionals = [
        quantities[cell.cell_id] * cell.sell_price for cell in cells
    ]
    deferred_buy = sum(
        filters.percent_price_down > 0 and cell.buy_price < mark * filters.percent_price_down
        for cell in cells if cell not in sell_cells
    )
    deferred_sell = sum(
        filters.percent_price_up > 0 and cell.sell_price > mark * filters.percent_price_up
        for cell in sell_cells
    )
    if sell_cells and (
        seed_qty <= 0
        or seed_qty * mark < filters.min_notional
    ):
        raise ValueError("initial sell-side position is below exchange minimum")
    return {
        "current_price": str(mark),
        "buy_cell_count": len(cells) - len(sell_cells),
        "sell_cell_count": len(sell_cells),
        "seed_notional_usdt": str(seed_notional),
        "seed_quantity": str(seed_qty),
        "estimated_seed_market_buy_usdt": str(seed_market_buy),
        "minimum_sell_order_notional_usdt": str(min(sell_order_notionals)),
        "maximum_sell_order_notional_usdt": str(max(sell_order_notionals)),
        "buy_budget_usdt": str(sum((cell.budget_usdt for cell in cells if cell not in sell_cells), Decimal("0"))),
        "inside_range": cells[0].buy_price <= mark <= cells[-1].sell_price,
        "maker_fee_rate": str(maker_rate),
        "taker_fee_rate": str(taker_rate),
        "minimum_net_cycle_profit_usdt": str(min(net_profits)),
        "minimum_net_cycle_profit_taker_usdt": str(min(taker_net_profits)),
        "minimum_net_cycle_profit_pct": str(min(maker_returns) * Decimal("100")),
        "minimum_net_cycle_profit_taker_pct": str(min(taker_returns) * Decimal("100")),
        "deferred_buy_cell_count": deferred_buy,
        "deferred_sell_cell_count": deferred_sell,
        "percent_price_up": str(filters.percent_price_up),
        "percent_price_down": str(filters.percent_price_down),
        "estimated_seed_opening_fee_usdt": str(seed_market_buy * taker_rate),
        "seed_cells_below_fee_break_even": sum(
            cell.sell_price <= mark * (Decimal("1") + taker_rate) / (Decimal("1") - maker_rate)
            for cell in sell_cells
        ),
    }


def create_app(
    db_path: str | Path | None = None,
    exchange_factory: Callable[[], object] | None = None,
) -> FastAPI:
    load_environment()
    if db_path is None:
        db_path = os.getenv("GRID_DB_PATH", "grid_trading.sqlite3")
    store = SQLiteStore(db_path)
    supervisor = StrategySupervisor(store)
    service = GridService(store, supervisor)

    if exchange_factory is None:
        api_key, api_secret = binance_credentials()
        usdm_base_url = binance_base_url()
        coinm_base_url = binance_coinm_base_url()

        def exchange_factory(market_type=FuturesMarket.USDM):
            market = FuturesMarket(market_type)
            if market == FuturesMarket.COINM:
                return BinanceCoinMExchange(
                    api_key,
                    api_secret,
                    coinm_base_url,
                )
            return BinanceFuturesExchange(
                api_key,
                api_secret,
                usdm_base_url,
            )

    assert exchange_factory is not None
    factory_signature = inspect.signature(exchange_factory)
    factory_accepts_market = bool(factory_signature.parameters)

    def exchange_for(market_type: FuturesMarket):
        if factory_accepts_market:
            return exchange_factory(market_type)
        return exchange_factory()

    app = FastAPI(title="Grid Trading Service", version="0.1.0")
    app.state.store = store
    app.state.service = service
    app.state.exchange_factory = exchange_factory
    app.state.exchange_for = exchange_for

    @app.get("/health")
    def health() -> dict:
        api_key, api_secret = binance_credentials()
        return {"status": "ok", "binance_configured": bool(api_key and api_secret)}

    @app.get("/strategies")
    def list_strategies(include_archived: bool = False) -> list[dict]:
        return [strategy_payload(config, store) for config in store.list_strategies(include_archived)]

    @app.get("/position-pools")
    def list_position_pools() -> list[dict]:
        return store.list_position_pools()

    @app.get("/scheduler/incidents")
    def list_scheduler_incidents(limit: int = 100) -> list[dict]:
        return store.list_scheduler_incidents(limit)

    @app.get("/scheduler/runs")
    def list_scheduler_runs(limit: int = 50) -> list[dict]:
        return store.list_scheduler_runs(limit)

    @app.get("/scheduler/gaps")
    def list_scheduler_gaps(limit: int = 100) -> list[dict]:
        return store.list_scheduler_gaps(limit)

    @app.post("/strategies/preview")
    def preview(data: StrategyInput) -> dict:
        try:
            filters = exchange_for(data.market_type).get_symbol_filters(data.symbol.strip().upper())
            _validate_market_contract(data.market_type, filters)
            order_usdt, order_coin_qty = _strategy_amounts(data, filters.contract_size)
            config, cells = service.preview(
                data.symbol,
                data.mode,
                data.anchor_price,
                data.grid_ratio,
                data.grid_count,
                order_usdt,
                data.leverage,
                filters.tick_size,
                poll_interval_sec=data.poll_interval_sec,
                move_grid=data.move_grid,
                market_type=data.market_type,
                order_coin_qty=order_coin_qty,
                contract_size=filters.contract_size,
            )
            return {"config": strategy_payload(config, _PreviewStore(cells)), "cells": [cell_payload(cell) for cell in cells]}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/fixed-grids/preview")
    def preview_fixed_grid(data: FixedGridInput) -> dict:
        try:
            exchange = exchange_for(FuturesMarket.USDM)
            symbol = data.symbol.strip().upper()
            filters = exchange.get_symbol_filters(symbol)
            if filters.contract_type and filters.contract_type not in {"PERPETUAL", "TRADIFI_PERPETUAL"}:
                raise ValueError("fixed grid requires a perpetual contract")
            config, cells = service.preview_fixed(
                symbol, data.lower_price, data.upper_price,
                data.grid_count, data.grid_budget_usdt, filters.tick_size,
                poll_interval_sec=data.poll_interval_sec,
            )
            maker_rate, taker_rate = exchange.get_commission_rates(symbol)
            details = fixed_grid_preview_details(
                cells, exchange.get_mark_price(symbol), filters, maker_rate, taker_rate
            )
            return {
                "config": strategy_payload(config, _PreviewStore(cells)),
                "cells": [cell_payload(cell, config=config) for cell in cells],
                "allocation": details,
            }
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/fixed-grids", status_code=201)
    def create_fixed_grid(data: FixedGridInput) -> dict:
        try:
            exchange = exchange_for(FuturesMarket.USDM)
            symbol = data.symbol.strip().upper()
            filters = exchange.get_symbol_filters(symbol)
            if filters.contract_type and filters.contract_type not in {"PERPETUAL", "TRADIFI_PERPETUAL"}:
                raise ValueError("fixed grid requires a perpetual contract")
            _, preview_cells = service.preview_fixed(
                symbol, data.lower_price, data.upper_price,
                data.grid_count, data.grid_budget_usdt, filters.tick_size,
                poll_interval_sec=data.poll_interval_sec,
            )
            maker_rate, taker_rate = exchange.get_commission_rates(symbol)
            fixed_grid_preview_details(
                preview_cells, exchange.get_mark_price(symbol), filters, maker_rate, taker_rate
            )
            config = service.create_fixed(
                symbol, data.lower_price, data.upper_price,
                data.grid_count, data.grid_budget_usdt, filters.tick_size,
                poll_interval_sec=data.poll_interval_sec,
            )
            return strategy_payload(config, store)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/strategies", status_code=201)
    def create_strategy(data: StrategyInput) -> dict:
        try:
            filters = exchange_for(data.market_type).get_symbol_filters(data.symbol.strip().upper())
            _validate_market_contract(data.market_type, filters)
            order_usdt, order_coin_qty = _strategy_amounts(data, filters.contract_size)
            config = service.create(
                data.symbol,
                data.mode,
                data.anchor_price,
                data.grid_ratio,
                data.grid_count,
                order_usdt,
                data.leverage,
                filters.tick_size,
                poll_interval_sec=data.poll_interval_sec,
                move_grid=data.move_grid,
                market_type=data.market_type,
                order_coin_qty=order_coin_qty,
                contract_size=filters.contract_size,
            )
            return strategy_payload(config, store)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/strategies/{strategy_id}")
    def update_strategy(strategy_id: str, data: StrategyInput) -> dict:
        try:
            existing = store.get_strategy(strategy_id)
            if existing is not None and existing.grid_kind == GridKind.FIXED_EQUAL_USDT:
                raise ValueError("fixed grid parameters cannot be edited through the triggered-grid endpoint")
            filters = exchange_for(data.market_type).get_symbol_filters(data.symbol.strip().upper())
            _validate_market_contract(data.market_type, filters)
            order_usdt, order_coin_qty = _strategy_amounts(data, filters.contract_size)
            config = service.editable_copy(
                strategy_id,
                symbol=data.symbol.strip().upper(),
                mode=data.mode,
                market_type=data.market_type,
                anchor_price=data.anchor_price,
                grid_ratio=data.grid_ratio,
                grid_count=data.grid_count,
                order_usdt=order_usdt,
                order_coin_qty=order_coin_qty,
                contract_size=filters.contract_size,
                leverage=data.leverage,
                poll_interval_sec=data.poll_interval_sec,
                move_grid=data.move_grid,
            )
            service.update_draft(config, filters.tick_size)
            return strategy_payload(store.get_strategy(strategy_id), store)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="strategy not found") from exc
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/strategies/{strategy_id}/cells")
    def strategy_cells(strategy_id: str) -> list[dict]:
        config = store.get_strategy(strategy_id)
        if config is None:
            raise HTTPException(status_code=404, detail="strategy not found")
        cells = store.list_cells(strategy_id)
        order_ids = {
            order_id
            for cell in cells
            for order_id in (cell.entry_order_id, cell.exit_order_id)
            if order_id is not None
        }
        quantities = store.get_order_quantities(strategy_id, order_ids)
        return [cell_payload(cell, quantities, config) for cell in cells]

    @app.get("/strategies/{strategy_id}/events")
    def strategy_events(strategy_id: str) -> list[dict]:
        if store.get_strategy(strategy_id) is None:
            raise HTTPException(status_code=404, detail="strategy not found")
        return store.list_events(strategy_id)

    @app.get("/strategies/{strategy_id}/cell-actions")
    def strategy_cell_actions(strategy_id: str, limit: int = 20) -> list[dict]:
        if store.get_strategy(strategy_id) is None:
            raise HTTPException(status_code=404, detail="strategy not found")
        return store.list_cell_actions(strategy_id, min(max(limit, 1), 100))

    @app.get("/strategies/{strategy_id}/topups")
    def strategy_topups(strategy_id: str) -> list[dict]:
        if store.get_strategy(strategy_id) is None:
            raise HTTPException(status_code=404, detail="strategy not found")
        return store.list_capital_actions(strategy_id)

    @app.post("/strategies/{strategy_id}/topups/preview")
    def preview_topup(strategy_id: str, data: TopupPreviewInput) -> dict:
        try:
            config = store.get_strategy(strategy_id)
            if config is None:
                raise KeyError(strategy_id)
            if config.grid_kind != GridKind.FIXED_EQUAL_USDT:
                raise ValueError("top-up requires a fixed equal-USDT grid")
            cells = store.list_cells(strategy_id)
            increments = fixed_cell_budgets(data.amount_usdt, len(cells))
            sell_count = sum(cell.open_qty > 0 for cell in cells)
            sell_amount = sum(
                (increment for cell, increment in zip(cells, increments) if cell.open_qty > 0),
                Decimal("0"),
            )
            exchange = exchange_for(config.market_type)
            available = exchange.get_available_balance("USDT")
            if available < data.amount_usdt:
                raise ValueError("available USDT is below the top-up amount")
            filters = exchange.get_symbol_filters(config.symbol)
            mark = exchange.get_mark_price(config.symbol)
            quantity = sum(
                (
                    max(
                        Decimal("0"),
                        round_down((cell.budget_usdt + increment) / cell.buy_price, filters.step_size)
                        - round_down(cell.budget_usdt / cell.buy_price, filters.step_size),
                    )
                    for cell, increment in zip(cells, increments) if cell.open_qty > 0
                ),
                Decimal("0"),
            )
            if sell_count and (
                quantity <= 0
                or quantity < filters.min_qty
                or quantity * mark < filters.min_notional
            ):
                raise ValueError("top-up sell-side amount is below exchange minimum")
            estimated_buy = quantity * mark
            return {
                "amount_usdt": str(data.amount_usdt),
                "per_cell_usdt": str(increments[0]),
                "sell_cell_ids": [cell.cell_id for cell in cells if cell.open_qty > 0],
                "sell_cell_count": sell_count,
                "buy_cell_count": len(cells) - sell_count,
                "aggregate_buy_usdt": str(estimated_buy),
                "sell_budget_increase_usdt": str(sell_amount),
                "buy_order_budget_increase_usdt": str(data.amount_usdt - sell_amount),
                "available_usdt": str(available),
                "estimated_opening_fee_usdt": str(
                    estimated_buy * exchange.get_commission_rates(config.symbol)[1]
                ),
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="strategy not found") from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/strategies/{strategy_id}/topups", status_code=202)
    def request_topup(strategy_id: str, data: TopupInput) -> dict:
        try:
            action = store.create_capital_action(
                data.request_id, strategy_id, "topup", data.amount_usdt,
                data.expected_sell_cell_ids,
            )
            return action
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="strategy not found") from exc
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/strategies/{strategy_id}/cell-actions", status_code=202)
    def request_cell_action(strategy_id: str, data: CellActionInput) -> dict:
        try:
            config = store.get_strategy(strategy_id)
            if config is not None and config.grid_kind == GridKind.FIXED_EQUAL_USDT:
                raise ValueError("fixed grid boundaries and cell count cannot be moved")
            action = store.request_cell_action(
                strategy_id,
                data.operation,
                data.boundary,
            )
            store.append_event(
                strategy_id,
                "CELL_ACTION_REQUESTED",
                {
                    "action_id": action["id"],
                    "operation": data.operation,
                    "boundary": data.boundary,
                },
                action["target_cell_id"],
                "web-api",
            )
            return action
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="strategy not found") from exc
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/strategies/{strategy_id}/start")
    def start_strategy(strategy_id: str) -> dict:
        try:
            config = store.get_strategy(strategy_id)
            if config is not None and config.grid_kind == GridKind.FIXED_EQUAL_USDT:
                exchange = exchange_for(FuturesMarket.USDM)
                if isinstance(exchange, BinanceFuturesExchange):
                    if urlparse(exchange.base_url).hostname not in {"demo-fapi.binance.com", "testnet.binancefuture.com"}:
                        raise ValueError("fixed equal-USDT grid is enabled on Binance testnet only")
                if config.lower_price is not None and config.upper_price is not None:
                    mark = exchange.get_mark_price(config.symbol)
                    if not config.lower_price <= mark <= config.upper_price:
                        raise ValueError("current price is outside fixed grid boundaries")
            return {"pid": service.start(strategy_id), "configuration_locked": True}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="strategy not found") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/strategies/{strategy_id}/stop")
    def stop_strategy(strategy_id: str) -> dict:
        try:
            service.stop(strategy_id)
            return {"status": "stopped", "orders_untouched": True}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="strategy not found") from exc

    @app.post("/strategies/{strategy_id}/refresh-price")
    def refresh_price(strategy_id: str) -> dict:
        try:
            config = store.get_strategy(strategy_id)
            if config is None:
                raise KeyError(strategy_id)
            price = service.refresh_price(
                strategy_id,
                exchange_for(config.market_type),
            )
            return {"mark_price": str(price)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="strategy not found") from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/strategies/{strategy_id}/archive")
    def archive_strategy(strategy_id: str) -> dict:
        try:
            service.archive(strategy_id)
            return {"status": "archived"}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="strategy not found") from exc

    @app.delete("/strategies/{strategy_id}")
    def delete_strategy(strategy_id: str) -> dict:
        try:
            service.delete(strategy_id)
            return {"status": "deleted", "physical_data_retained": True}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="strategy not found") from exc

    return app


def _validate_market_contract(market_type: FuturesMarket, filters) -> None:
    if market_type != FuturesMarket.COINM:
        return
    if filters.contract_type != "PERPETUAL":
        raise ValueError("COIN-M currently supports PERPETUAL symbols such as BTCUSD_PERP")
    if filters.contract_size <= 0:
        raise ValueError("COIN-M symbol does not provide a valid contractSize")


class _PreviewStore:
    """Small adapter used only to reuse response serialization for previews."""

    def __init__(self, cells):
        self.cells = cells

    def list_cells(self, _strategy_id):
        return self.cells

    def get_runtime(self, _strategy_id):
        return None
