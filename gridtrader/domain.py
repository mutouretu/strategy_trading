from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Mode(StrEnum):
    LONG = "long"
    SHORT = "short"


class StrategyStatus(StrEnum):
    DRAFT = "draft"
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"
    ARCHIVED = "archived"


class CellStage(StrEnum):
    UNTRIGGERED = "untriggered"
    PENDING_ENTRY = "pending_entry"
    PENDING_EXIT = "pending_exit"
    MANUAL_REVIEW = "manual_review"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(StrEnum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class StrategyConfig:
    strategy_id: str
    symbol: str
    mode: Mode
    anchor_price: Decimal
    grid_ratio: Decimal
    grid_count: int
    order_usdt: Decimal
    leverage: int = 3
    poll_interval_sec: float = 1.0
    move_grid: bool = True
    status: StrategyStatus = StrategyStatus.DRAFT
    has_started: bool = False
    archived: bool = False

    def validate(self) -> None:
        if not self.strategy_id.strip():
            raise ValueError("strategy_id must not be empty")
        if not self.symbol.strip():
            raise ValueError("symbol must not be empty")
        if self.anchor_price <= 0:
            raise ValueError("anchor_price must be > 0")
        if self.grid_ratio <= 0:
            raise ValueError("grid_ratio must be > 0")
        if self.grid_count < 1:
            raise ValueError("grid_count must be >= 1")
        if self.order_usdt <= 0:
            raise ValueError("order_usdt must be > 0")
        if self.leverage < 1:
            raise ValueError("leverage must be >= 1")
        if self.poll_interval_sec < 0.2:
            raise ValueError("poll_interval_sec must be >= 0.2")


@dataclass
class GridCell:
    strategy_id: str
    cell_id: str
    index: int
    buy_price: Decimal
    sell_price: Decimal
    stage: CellStage = CellStage.UNTRIGGERED
    entry_order_id: int | None = None
    exit_order_id: int | None = None
    entry_client_id: str = ""
    exit_client_id: str = ""
    open_qty: Decimal = Decimal("0")
    exit_executed_qty: Decimal = Decimal("0")
    entry_filled_at: str = ""
    cycle_count: int = 0

    def validate(self) -> None:
        if self.buy_price <= 0 or self.sell_price <= 0:
            raise ValueError("cell prices must be > 0")
        if self.buy_price >= self.sell_price:
            raise ValueError("buy_price must be lower than sell_price")


@dataclass(frozen=True)
class SymbolFilters:
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal = Decimal("0")
    min_notional: Decimal = Decimal("0")


@dataclass(frozen=True)
class OrderSnapshot:
    order_id: int
    client_order_id: str
    status: OrderStatus
    side: OrderSide
    price: Decimal
    original_qty: Decimal
    executed_qty: Decimal = Decimal("0")
    average_price: Decimal = Decimal("0")
    position_side: str = ""


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    position_side: str
    quantity: Decimal
