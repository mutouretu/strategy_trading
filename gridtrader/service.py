from __future__ import annotations

import re
import uuid
from dataclasses import replace
from decimal import Decimal

from .domain import Mode, StrategyConfig, StrategyStatus
from .exchange import Exchange
from .grid_math import build_cells
from .store import SQLiteStore
from .supervisor import StrategySupervisor


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
    ) -> tuple[StrategyConfig, list]:
        config = StrategyConfig(
            strategy_id=strategy_id,
            symbol=symbol.strip().upper(),
            mode=mode,
            anchor_price=anchor_price,
            grid_ratio=grid_ratio,
            grid_count=grid_count,
            order_usdt=order_usdt,
            leverage=leverage,
            poll_interval_sec=poll_interval_sec,
            move_grid=move_grid,
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
    ) -> StrategyConfig:
        clean_symbol = symbol.strip().upper()
        slug = re.sub(r"[^a-z0-9]+", "-", clean_symbol.lower()).strip("-")
        strategy_id = f"{slug}-{mode.value}-{uuid.uuid4().hex[:8]}"
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
        )
        self.store.create_strategy(config)
        self.store.replace_cells(strategy_id, cells)
        self.store.append_event(strategy_id, "STRATEGY_CREATED", {"cell_count": len(cells)})
        return config

    def update_draft(self, config: StrategyConfig, tick_size: Decimal) -> None:
        self.store.update_draft(config)
        self.store.replace_cells(config.strategy_id, build_cells(config, tick_size))
        self.store.append_event(config.strategy_id, "STRATEGY_UPDATED", {})

    def start(self, strategy_id: str) -> int:
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
