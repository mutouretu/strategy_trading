"""Value types used by the grid rule core."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class GridMode(StrEnum):
    LONG = "long"
    SHORT = "short"


class GridMarketType(StrEnum):
    """Quantity and settlement convention used by the grid."""

    USDM = "usdm"
    COINM = "coinm"


class GridOrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class GridOrderRole(StrEnum):
    ENTRY = "entry"
    EXIT = "exit"


class CellPhase(StrEnum):
    DORMANT = "dormant"
    ENTRY_PENDING = "entry_pending"
    EXIT_PENDING = "exit_pending"


@dataclass(frozen=True, slots=True)
class GridRuleConfig:
    grid_id: str
    instrument: str
    mode: GridMode
    anchor_price: Decimal
    grid_ratio: Decimal
    grid_count: int
    order_notional: Decimal
    tick_size: Decimal = Decimal("0.01")
    quantity_step: Decimal = Decimal("0.0001")
    move_grid: bool = False
    market_type: GridMarketType = GridMarketType.USDM
    order_coin_qty: Decimal | None = None
    contract_size: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        for field_name in (
            "anchor_price",
            "grid_ratio",
            "order_notional",
            "tick_size",
            "quantity_step",
            "contract_size",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal):
                object.__setattr__(self, field_name, Decimal(str(value)))
        if self.order_coin_qty is not None and not isinstance(
            self.order_coin_qty,
            Decimal,
        ):
            object.__setattr__(
                self,
                "order_coin_qty",
                Decimal(str(self.order_coin_qty)),
            )
        if not isinstance(self.market_type, GridMarketType):
            object.__setattr__(
                self,
                "market_type",
                GridMarketType(self.market_type),
            )
        if not self.grid_id.strip():
            raise ValueError("grid_id must not be empty")
        if not self.instrument.strip():
            raise ValueError("instrument must not be empty")
        if self.anchor_price <= 0:
            raise ValueError("anchor_price must be > 0")
        if self.grid_ratio <= 0:
            raise ValueError("grid_ratio must be > 0")
        if self.grid_count < 1:
            raise ValueError("grid_count must be >= 1")
        if self.market_type == GridMarketType.COINM:
            if self.order_notional < 0:
                raise ValueError("order_notional must be >= 0 for COIN-M")
            if self.order_coin_qty is None or self.order_coin_qty <= 0:
                raise ValueError("order_coin_qty must be > 0 for COIN-M")
            if self.contract_size <= 0:
                raise ValueError("contract_size must be > 0 for COIN-M")
        elif self.order_notional <= 0:
            raise ValueError("order_notional must be > 0 for USD-M")
        if self.tick_size <= 0:
            raise ValueError("tick_size must be > 0")
        if self.quantity_step <= 0:
            raise ValueError("quantity_step must be > 0")


@dataclass(slots=True)
class GridCellState:
    cell_id: str
    index: int
    buy_price: Decimal
    sell_price: Decimal
    phase: CellPhase = CellPhase.DORMANT
    position_quantity: Decimal = Decimal("0")
    cycle_count: int = 0
    current_order_key: str | None = None


@dataclass(frozen=True, slots=True)
class GridOrderIntent:
    order_key: str
    instrument: str
    side: GridOrderSide
    role: GridOrderRole
    price: Decimal
    quantity: Decimal
    cell_id: str
    cycle: int


@dataclass(frozen=True, slots=True)
class GridFill:
    order_key: str
    instrument: str
    side: GridOrderSide
    price: Decimal
    quantity: Decimal
    sequence: int
    timestamp: int
