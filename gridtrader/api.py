from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import Callable, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .binance import BinanceFuturesExchange
from .config import binance_base_url, binance_credentials, load_environment
from .domain import Mode, StrategyConfig
from .service import GridService
from .store import SQLiteStore
from .supervisor import StrategySupervisor


class StrategyInput(BaseModel):
    symbol: str = Field(min_length=1)
    mode: Mode
    anchor_price: Decimal = Field(gt=0)
    grid_ratio: Decimal = Field(gt=0, description="Decimal ratio, e.g. 0.005 for 0.5%")
    grid_count: int = Field(ge=1, le=100)
    order_usdt: Decimal = Field(gt=0)
    leverage: int = Field(ge=1, le=125)
    poll_interval_sec: float = Field(default=1.0, ge=0.2)
    move_grid: bool = True


class CellActionInput(BaseModel):
    operation: Literal["add", "remove"]
    boundary: Literal["lower", "upper"]


def strategy_payload(config: StrategyConfig, store: SQLiteStore) -> dict:
    cells = store.list_cells(config.strategy_id)
    runtime = store.get_runtime(config.strategy_id)
    lower_price = min((cell.buy_price for cell in cells), default=None)
    upper_price = max((cell.sell_price for cell in cells), default=None)
    return {
        "strategy_id": config.strategy_id,
        "symbol": config.symbol,
        "mode": config.mode.value,
        "anchor_price": str(config.anchor_price),
        "grid_ratio": str(config.grid_ratio),
        "grid_count": config.grid_count,
        "order_usdt": str(config.order_usdt),
        "leverage": config.leverage,
        "poll_interval_sec": config.poll_interval_sec,
        "move_grid": config.move_grid,
        "status": config.status.value,
        "has_started": config.has_started,
        "archived": config.archived,
        "current_price": runtime.get("mark_price") if runtime else None,
        "lower_price": None if lower_price is None else str(lower_price),
        "upper_price": None if upper_price is None else str(upper_price),
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


def cell_payload(cell, order_quantities: dict[int, Decimal] | None = None) -> dict:
    quantities = order_quantities or {}
    entry_qty = quantities.get(cell.entry_order_id) if cell.entry_order_id is not None else None
    exit_qty = quantities.get(cell.exit_order_id) if cell.exit_order_id is not None else None
    if entry_qty is None and cell.entry_order_id is not None and cell.open_qty > 0:
        entry_qty = cell.open_qty
    if exit_qty is not None:
        exit_qty = max(Decimal("0"), exit_qty - cell.exit_executed_qty)
    elif cell.exit_order_id is not None:
        exit_qty = cell.open_qty
    config_side = {
        "cell_id": cell.cell_id,
        "index": cell.index,
        "buy_price": str(cell.buy_price),
        "sell_price": str(cell.sell_price),
        "stage": cell.stage.value,
        "entry_order_id": cell.entry_order_id,
        "exit_order_id": cell.exit_order_id,
        "entry_qty": None if entry_qty is None else str(entry_qty),
        "exit_qty": None if exit_qty is None else str(exit_qty),
        "open_qty": str(cell.open_qty),
        "exit_executed_qty": str(cell.exit_executed_qty),
        "entry_filled_at": cell.entry_filled_at or None,
        "cycle_count": cell.cycle_count,
    }
    return config_side


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
        base_url = binance_base_url()
        exchange_factory = lambda: BinanceFuturesExchange(
            api_key,
            api_secret,
            base_url,
        )

    app = FastAPI(title="Grid Trading Service", version="0.1.0")
    app.state.store = store
    app.state.service = service
    app.state.exchange_factory = exchange_factory

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

    @app.post("/strategies/preview")
    def preview(data: StrategyInput) -> dict:
        try:
            filters = exchange_factory().get_symbol_filters(data.symbol.strip().upper())
            config, cells = service.preview(
                data.symbol,
                data.mode,
                data.anchor_price,
                data.grid_ratio,
                data.grid_count,
                data.order_usdt,
                data.leverage,
                filters.tick_size,
                poll_interval_sec=data.poll_interval_sec,
                move_grid=data.move_grid,
            )
            return {"config": strategy_payload(config, _PreviewStore(cells)), "cells": [cell_payload(cell) for cell in cells]}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/strategies", status_code=201)
    def create_strategy(data: StrategyInput) -> dict:
        try:
            filters = exchange_factory().get_symbol_filters(data.symbol.strip().upper())
            config = service.create(
                data.symbol,
                data.mode,
                data.anchor_price,
                data.grid_ratio,
                data.grid_count,
                data.order_usdt,
                data.leverage,
                filters.tick_size,
                poll_interval_sec=data.poll_interval_sec,
                move_grid=data.move_grid,
            )
            return strategy_payload(config, store)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/strategies/{strategy_id}")
    def update_strategy(strategy_id: str, data: StrategyInput) -> dict:
        try:
            filters = exchange_factory().get_symbol_filters(data.symbol.strip().upper())
            config = service.editable_copy(
                strategy_id,
                symbol=data.symbol.strip().upper(),
                mode=data.mode,
                anchor_price=data.anchor_price,
                grid_ratio=data.grid_ratio,
                grid_count=data.grid_count,
                order_usdt=data.order_usdt,
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
        if store.get_strategy(strategy_id) is None:
            raise HTTPException(status_code=404, detail="strategy not found")
        cells = store.list_cells(strategy_id)
        order_ids = {
            order_id
            for cell in cells
            for order_id in (cell.entry_order_id, cell.exit_order_id)
            if order_id is not None
        }
        quantities = store.get_order_quantities(strategy_id, order_ids)
        return [cell_payload(cell, quantities) for cell in cells]

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

    @app.post("/strategies/{strategy_id}/cell-actions", status_code=202)
    def request_cell_action(strategy_id: str, data: CellActionInput) -> dict:
        try:
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
            price = service.refresh_price(strategy_id, exchange_factory())
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


class _PreviewStore:
    """Small adapter used only to reuse response serialization for previews."""

    def __init__(self, cells):
        self.cells = cells

    def list_cells(self, _strategy_id):
        return self.cells

    def get_runtime(self, _strategy_id):
        return None
