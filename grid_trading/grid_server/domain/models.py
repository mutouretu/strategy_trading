from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Mode(StrEnum):
    LONG = "long"
    SHORT = "short"


class ExchangeVenue(StrEnum):
    BINANCE = "binance"
    OKX = "okx"


class FuturesMarket(StrEnum):
    """Binance futures product family.

    USD-M quantities are base-asset amounts. COIN-M quantities are contract
    counts whose USD face value is supplied by ``contractSize``.
    """

    USDM = "usdm"
    COINM = "coinm"


class GridKind(StrEnum):
    TRIGGERED = "triggered"
    FIXED_EQUAL_USDT = "fixed_equal_usdt"


class FixedEntryMode(StrEnum):
    SEEDED = "seeded"
    PRICE_TRIGGERED = "price_triggered"


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
    market_type: FuturesMarket = FuturesMarket.USDM
    venue: ExchangeVenue = ExchangeVenue.BINANCE
    leverage: int = 3
    poll_interval_sec: float = 1.0
    move_grid: bool = True
    status: StrategyStatus = StrategyStatus.DRAFT
    has_started: bool = False
    archived: bool = False
    # USD-M is configured by quote notional. COIN-M is configured by the
    # amount of its margin/base coin and converted to integer contracts only
    # at the order price.
    order_coin_qty: Decimal | None = None
    contract_size: Decimal = Decimal("0")
    grid_kind: GridKind = GridKind.TRIGGERED
    fixed_entry_mode: FixedEntryMode = FixedEntryMode.SEEDED
    lower_price: Decimal | None = None
    upper_price: Decimal | None = None
    grid_budget_usdt: Decimal | None = None
    auto_reinvest_enabled: bool = False
    auto_reinvest_percent: Decimal = Decimal("100")
    grid_generation: int = 0

    def validate(self) -> None:
        if not self.strategy_id.strip():
            raise ValueError("strategy_id must not be empty")
        if not self.symbol.strip():
            raise ValueError("symbol must not be empty")
        if self.venue == ExchangeVenue.OKX:
            if self.market_type != FuturesMarket.USDM or self.grid_kind != GridKind.FIXED_EQUAL_USDT:
                raise ValueError("OKX currently supports fixed USDT perpetual grids only")
            if not self.symbol.endswith("-USDT-SWAP"):
                raise ValueError("OKX fixed grid symbol must end in -USDT-SWAP")
        if self.anchor_price <= 0:
            raise ValueError("anchor_price must be > 0")
        if self.grid_ratio <= 0:
            raise ValueError("grid_ratio must be > 0")
        if not self.auto_reinvest_percent.is_finite() or not (
            Decimal("0") <= self.auto_reinvest_percent <= Decimal("100")
        ):
            raise ValueError("auto_reinvest_percent must be between 0 and 100")
        if self.grid_count < 1:
            raise ValueError("grid_count must be >= 1")
        if self.grid_generation < 0:
            raise ValueError("grid_generation must be >= 0")
        if not self.auto_reinvest_percent.is_finite() or not (Decimal("0") <= self.auto_reinvest_percent <= Decimal("100")):
            raise ValueError("auto_reinvest_percent must be between 0 and 100")
        if self.grid_kind == GridKind.FIXED_EQUAL_USDT:
            if self.market_type != FuturesMarket.USDM or self.mode != Mode.LONG:
                raise ValueError("fixed equal-USDT grid supports USD-M long only")
            if self.leverage != 1 or self.move_grid:
                raise ValueError("fixed equal-USDT grid requires 1x and fixed boundaries")
            if (
                self.lower_price is None
                or self.upper_price is None
                or self.lower_price <= 0
                or self.upper_price <= self.lower_price
            ):
                raise ValueError("fixed grid requires 0 < lower_price < upper_price")
            if self.grid_budget_usdt is None or self.grid_budget_usdt <= 0:
                raise ValueError("fixed grid requires positive grid_budget_usdt")
        elif self.fixed_entry_mode != FixedEntryMode.SEEDED:
            raise ValueError("price-triggered entry mode requires a fixed equal-USDT grid")
        if self.market_type == FuturesMarket.COINM:
            if self.order_coin_qty is None or self.order_coin_qty <= 0:
                raise ValueError("order_coin_qty must be > 0 for COIN-M")
            if self.contract_size < 0:
                raise ValueError("contract_size must be >= 0")
        elif self.order_usdt <= 0:
            raise ValueError("order_usdt must be > 0")
        if self.leverage < 1:
            raise ValueError("leverage must be >= 1")
        if self.poll_interval_sec < 0.2:
            raise ValueError("poll_interval_sec must be >= 0.2")

    @property
    def order_value_usd(self) -> Decimal:
        """Per-cell USD notional for USD-M strategies."""

        return self.order_usdt

    @property
    def order_base_quantity(self) -> Decimal:
        """Configured per-cell margin/base-coin amount for COIN-M."""

        return self.order_coin_qty or Decimal("0")


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
    budget_usdt: Decimal = Decimal("0")

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
    contract_size: Decimal = Decimal("0")
    base_asset: str = ""
    margin_asset: str = ""
    contract_type: str = ""
    percent_price_up: Decimal = Decimal("0")
    percent_price_down: Decimal = Decimal("0")


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
