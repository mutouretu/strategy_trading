"""Virtual owner positions and financial attribution for StrategyApplication."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from trading_strategies.kernel import (
    PositionEffect,
    RuleIntentProposal,
    TradeSide,
)
from trading_strategies.strategy import StrategyProductType

from .allocation import CapitalAllocation


def _text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _decimal(name: str, value: object) -> Decimal:
    converted = Decimal(str(value))
    if not converted.is_finite():
        raise ValueError(f"{name} must be finite")
    return converted


def _positive(name: str, value: object) -> Decimal:
    converted = _decimal(name, value)
    if converted <= 0:
        raise ValueError(f"{name} must be > 0")
    return converted


@dataclass(frozen=True, slots=True)
class StrategyAccountingFill:
    fill_id: str
    intent_key: str
    instrument: str
    side: TradeSide
    price: Decimal
    quantity: Decimal
    quantity_unit: str
    settlement_asset: str
    fee_amount: Decimal = Decimal("0")
    contract_size: Decimal = Decimal("1")
    sequence: int = 0
    timestamp: int = 0

    def __post_init__(self) -> None:
        for name in (
            "fill_id",
            "intent_key",
            "instrument",
            "quantity_unit",
        ):
            object.__setattr__(self, name, _text(name, getattr(self, name)))
        object.__setattr__(
            self,
            "settlement_asset",
            _text("settlement_asset", self.settlement_asset).upper(),
        )
        if not isinstance(self.side, TradeSide):
            raise TypeError("side must be a TradeSide")
        object.__setattr__(self, "price", _positive("price", self.price))
        object.__setattr__(
            self,
            "quantity",
            _positive("quantity", self.quantity),
        )
        object.__setattr__(
            self,
            "contract_size",
            _positive("contract_size", self.contract_size),
        )
        fee = _decimal("fee_amount", self.fee_amount)
        if fee < 0:
            raise ValueError("fee_amount must be >= 0")
        object.__setattr__(self, "fee_amount", fee)
        for name in ("sequence", "timestamp"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be an integer >= 0")


@dataclass(frozen=True, slots=True)
class VirtualPosition:
    position_owner_id: str
    strategy_instance_id: str
    instrument: str
    product_type: StrategyProductType
    settlement_asset: str
    quantity_unit: str
    contract_size: Decimal
    quantity: Decimal
    average_entry_price: Decimal

    def __post_init__(self) -> None:
        for name in (
            "position_owner_id",
            "strategy_instance_id",
            "instrument",
            "quantity_unit",
        ):
            object.__setattr__(self, name, _text(name, getattr(self, name)))
        if not isinstance(self.product_type, StrategyProductType):
            raise TypeError("product_type must be a StrategyProductType")
        object.__setattr__(
            self,
            "settlement_asset",
            _text("settlement_asset", self.settlement_asset).upper(),
        )
        object.__setattr__(
            self,
            "contract_size",
            _positive("contract_size", self.contract_size),
        )
        quantity = _decimal("quantity", self.quantity)
        if quantity == 0:
            raise ValueError("quantity must not be zero")
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(
            self,
            "average_entry_price",
            _positive("average_entry_price", self.average_entry_price),
        )

    def notional(self, mark_price: Decimal) -> Decimal:
        mark = _positive("mark_price", mark_price)
        if self.product_type == StrategyProductType.INVERSE_PERPETUAL:
            return abs(self.quantity) * self.contract_size
        return abs(self.quantity) * self.contract_size * mark

    def unrealized_pnl(self, mark_price: Decimal) -> Decimal:
        mark = _positive("mark_price", mark_price)
        direction = Decimal("1") if self.quantity > 0 else Decimal("-1")
        if self.product_type == StrategyProductType.INVERSE_PERPETUAL:
            return (
                direction
                * abs(self.quantity)
                * self.contract_size
                * (
                    Decimal("1") / self.average_entry_price
                    - Decimal("1") / mark
                )
            )
        return (
            direction
            * abs(self.quantity)
            * self.contract_size
            * (mark - self.average_entry_price)
        )


@dataclass(frozen=True, slots=True)
class PositionBookFillPreview:
    base_version: int
    fill: StrategyAccountingFill
    strategy_instance_id: str
    position_owner_id: str
    previous_position: VirtualPosition | None
    next_position: VirtualPosition | None
    gross_realized_pnl_delta: Decimal


@dataclass(frozen=True, slots=True)
class StrategyFundingSettlement:
    settlement_id: str
    instrument: str
    product_type: StrategyProductType
    settlement_asset: str
    wallet_delta: Decimal
    position_quantity: Decimal
    mark_price: Decimal
    sequence: int = 0
    timestamp: int = 0

    def __post_init__(self) -> None:
        for name in ("settlement_id", "instrument"):
            object.__setattr__(self, name, _text(name, getattr(self, name)))
        if not isinstance(self.product_type, StrategyProductType):
            raise TypeError("product_type must be a StrategyProductType")
        object.__setattr__(
            self,
            "settlement_asset",
            _text("settlement_asset", self.settlement_asset).upper(),
        )
        wallet_delta = _decimal("wallet_delta", self.wallet_delta)
        if wallet_delta == 0:
            raise ValueError("wallet_delta must not be zero")
        object.__setattr__(self, "wallet_delta", wallet_delta)
        position = _decimal("position_quantity", self.position_quantity)
        if position == 0:
            raise ValueError("position_quantity must not be zero")
        object.__setattr__(self, "position_quantity", position)
        object.__setattr__(
            self,
            "mark_price",
            _positive("mark_price", self.mark_price),
        )
        for name in ("sequence", "timestamp"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be an integer >= 0")


@dataclass(frozen=True, slots=True)
class StrategyFundingAllocation:
    settlement_id: str
    strategy_instance_id: str
    position_owner_id: str
    instrument: str
    settlement_asset: str
    exposure_weight: Decimal
    wallet_delta: Decimal

    def __post_init__(self) -> None:
        for name in (
            "settlement_id",
            "strategy_instance_id",
            "position_owner_id",
            "instrument",
        ):
            object.__setattr__(self, name, _text(name, getattr(self, name)))
        object.__setattr__(
            self,
            "settlement_asset",
            _text("settlement_asset", self.settlement_asset).upper(),
        )
        weight = _positive("exposure_weight", self.exposure_weight)
        if weight > 1:
            raise ValueError("exposure_weight must be <= 1")
        object.__setattr__(self, "exposure_weight", weight)
        wallet_delta = _decimal("wallet_delta", self.wallet_delta)
        if wallet_delta == 0:
            raise ValueError("wallet_delta must not be zero")
        object.__setattr__(self, "wallet_delta", wallet_delta)


@dataclass(frozen=True, slots=True)
class StrategyAttributionSnapshot:
    strategy_instance_id: str
    settlement_asset: str
    allocated_capital: Decimal
    positions: tuple[VirtualPosition, ...]
    gross_realized_pnl: Decimal
    total_fees: Decimal
    net_realized_pnl: Decimal
    total_funding: Decimal
    net_pnl_after_fees_and_funding: Decimal
    unrealized_pnl: Decimal
    attributed_equity: Decimal
    fill_count: int
    funding_event_count: int


@dataclass(frozen=True, slots=True)
class AuthoritativeAccountSnapshot:
    settlement_asset: str
    initial_equity: Decimal
    positions: Mapping[str, Decimal]
    gross_realized_pnl: Decimal
    total_fees: Decimal
    total_funding: Decimal
    unrealized_pnl: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "settlement_asset",
            _text("settlement_asset", self.settlement_asset).upper(),
        )
        for name in (
            "initial_equity",
            "gross_realized_pnl",
            "total_fees",
            "total_funding",
            "unrealized_pnl",
        ):
            object.__setattr__(self, name, _decimal(name, getattr(self, name)))
        positions = {
            _text("instrument", instrument): _decimal("position", quantity)
            for instrument, quantity in self.positions.items()
        }
        object.__setattr__(self, "positions", MappingProxyType(positions))


@dataclass(frozen=True, slots=True)
class AttributionReconciliation:
    settlement_asset: str
    position_residuals: Mapping[str, Decimal]
    allocation_residual: Decimal
    gross_realized_pnl_residual: Decimal
    fee_residual: Decimal
    funding_residual: Decimal
    unrealized_pnl_residual: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "settlement_asset",
            _text("settlement_asset", self.settlement_asset).upper(),
        )
        residuals = {
            _text("instrument", instrument): _decimal("position", value)
            for instrument, value in self.position_residuals.items()
        }
        object.__setattr__(
            self,
            "position_residuals",
            MappingProxyType(residuals),
        )
        for name in (
            "allocation_residual",
            "gross_realized_pnl_residual",
            "fee_residual",
            "funding_residual",
            "unrealized_pnl_residual",
        ):
            object.__setattr__(self, name, _decimal(name, getattr(self, name)))

    @property
    def balanced(self) -> bool:
        return (
            all(value == 0 for value in self.position_residuals.values())
            and self.allocation_residual == 0
            and self.gross_realized_pnl_residual == 0
            and self.fee_residual == 0
            and self.funding_residual == 0
            and self.unrealized_pnl_residual == 0
        )


class VirtualPositionBook:
    """Application-owned attribution ledger; never replaces account ledger."""

    def __init__(
        self,
        allocations: Mapping[str, CapitalAllocation],
    ) -> None:
        self._allocations = {
            allocation.strategy_instance_id: allocation
            for allocation in allocations.values()
        }
        if len(self._allocations) != len(allocations):
            raise ValueError("each StrategyInstance needs one Allocation")
        self._positions: dict[tuple[str, str], VirtualPosition] = {}
        self._gross_realized: defaultdict[str, Decimal] = defaultdict(Decimal)
        self._fees: defaultdict[str, Decimal] = defaultdict(Decimal)
        self._funding: defaultdict[str, Decimal] = defaultdict(Decimal)
        self._fill_counts: defaultdict[str, int] = defaultdict(int)
        self._funding_counts: defaultdict[str, int] = defaultdict(int)
        self._processed_fill_ids: set[str] = set()
        self._processed_funding_ids: set[str] = set()
        self._known_position_keys: set[tuple[str, str]] = set()
        self._version = 0

    @property
    def positions(self) -> tuple[VirtualPosition, ...]:
        return tuple(self._positions[key] for key in sorted(self._positions))

    def position(
        self,
        position_owner_id: str,
        instrument: str,
    ) -> VirtualPosition | None:
        return self._positions.get((position_owner_id, instrument))

    def tracks_position(
        self,
        position_owner_id: str,
        instrument: str,
    ) -> bool:
        return (position_owner_id, instrument) in self._known_position_keys

    def preview_fill(
        self,
        *,
        fill: StrategyAccountingFill,
        intent: RuleIntentProposal,
        strategy_instance_id: str,
        position_owner_id: str,
        product_type: StrategyProductType,
    ) -> PositionBookFillPreview:
        self._validate_fill_identity(
            fill=fill,
            intent=intent,
            strategy_instance_id=strategy_instance_id,
        )
        if fill.fill_id in self._processed_fill_ids:
            raise ValueError(f"fill {fill.fill_id!r} was already attributed")
        key = (position_owner_id, fill.instrument)
        previous = self._positions.get(key)
        if previous is not None:
            if previous.product_type != product_type:
                raise ValueError("product_type changed within owner position")
            if previous.settlement_asset != fill.settlement_asset:
                raise ValueError(
                    "settlement_asset changed within owner position"
                )
            if previous.quantity_unit != fill.quantity_unit:
                raise ValueError("quantity_unit changed within owner position")
            if previous.contract_size != fill.contract_size:
                raise ValueError("contract_size changed within owner position")
        signed = fill.quantity if fill.side == TradeSide.BUY else -fill.quantity
        next_quantity = (previous.quantity if previous else Decimal("0")) + signed
        effect = intent.position_effect
        self._validate_position_effect(previous, signed, effect, fill.quantity)
        realized = self._realized_pnl(previous, fill, signed)
        next_position = self._next_position(
            previous=previous,
            fill=fill,
            signed_quantity=signed,
            next_quantity=next_quantity,
            strategy_instance_id=strategy_instance_id,
            position_owner_id=position_owner_id,
            product_type=product_type,
        )
        return PositionBookFillPreview(
            base_version=self._version,
            fill=fill,
            strategy_instance_id=strategy_instance_id,
            position_owner_id=position_owner_id,
            previous_position=previous,
            next_position=next_position,
            gross_realized_pnl_delta=realized,
        )

    def commit_fill(self, preview: PositionBookFillPreview) -> None:
        if preview.base_version != self._version:
            raise RuntimeError("VirtualPositionBook changed after fill preview")
        key = (preview.position_owner_id, preview.fill.instrument)
        self._known_position_keys.add(key)
        if preview.next_position is None:
            self._positions.pop(key, None)
        else:
            self._positions[key] = preview.next_position
        owner = preview.strategy_instance_id
        self._gross_realized[owner] += preview.gross_realized_pnl_delta
        self._fees[owner] += preview.fill.fee_amount
        self._fill_counts[owner] += 1
        self._processed_fill_ids.add(preview.fill.fill_id)
        self._version += 1

    def allocate_funding(
        self,
        settlement: StrategyFundingSettlement,
    ) -> tuple[StrategyFundingAllocation, ...]:
        if settlement.settlement_id in self._processed_funding_ids:
            raise ValueError(
                f"funding {settlement.settlement_id!r} was already attributed"
            )
        positions = tuple(
            position
            for position in self.positions
            if position.instrument == settlement.instrument
            and position.product_type == settlement.product_type
            and position.settlement_asset == settlement.settlement_asset
            and position.quantity * settlement.position_quantity > 0
        )
        total_quantity = sum(
            (position.quantity for position in positions),
            Decimal("0"),
        )
        if total_quantity != settlement.position_quantity:
            raise ValueError(
                "funding position does not match attributed owner positions"
            )
        notionals = tuple(
            position.notional(settlement.mark_price) for position in positions
        )
        total_notional = sum(notionals, Decimal("0"))
        if total_notional <= 0:
            raise ValueError("funding requires attributed notional exposure")
        allocations: list[StrategyFundingAllocation] = []
        allocated = Decimal("0")
        for index, (position, notional) in enumerate(
            zip(positions, notionals)
        ):
            weight = notional / total_notional
            wallet_delta = (
                settlement.wallet_delta - allocated
                if index == len(positions) - 1
                else settlement.wallet_delta * weight
            )
            allocated += wallet_delta
            allocations.append(
                StrategyFundingAllocation(
                    settlement_id=settlement.settlement_id,
                    strategy_instance_id=position.strategy_instance_id,
                    position_owner_id=position.position_owner_id,
                    instrument=position.instrument,
                    settlement_asset=position.settlement_asset,
                    exposure_weight=weight,
                    wallet_delta=wallet_delta,
                )
            )
        for item in allocations:
            self._funding[item.strategy_instance_id] += item.wallet_delta
            self._funding_counts[item.strategy_instance_id] += 1
        self._processed_funding_ids.add(settlement.settlement_id)
        self._version += 1
        return tuple(allocations)

    def snapshot(
        self,
        strategy_instance_id: str,
        marks: Mapping[str, Decimal],
    ) -> StrategyAttributionSnapshot:
        try:
            allocation = self._allocations[strategy_instance_id]
        except KeyError as exc:
            raise ValueError(
                f"unknown StrategyInstance {strategy_instance_id!r}"
            ) from exc
        positions = tuple(
            position
            for position in self.positions
            if position.strategy_instance_id == strategy_instance_id
        )
        unrealized = sum(
            (
                position.unrealized_pnl(marks[position.instrument])
                for position in positions
            ),
            Decimal("0"),
        )
        gross = self._gross_realized[strategy_instance_id]
        fees = self._fees[strategy_instance_id]
        funding = self._funding[strategy_instance_id]
        net_realized = gross - fees
        net_after = net_realized + funding
        return StrategyAttributionSnapshot(
            strategy_instance_id=strategy_instance_id,
            settlement_asset=allocation.settlement_asset,
            allocated_capital=allocation.amount,
            positions=positions,
            gross_realized_pnl=gross,
            total_fees=fees,
            net_realized_pnl=net_realized,
            total_funding=funding,
            net_pnl_after_fees_and_funding=net_after,
            unrealized_pnl=unrealized,
            attributed_equity=allocation.amount + net_after + unrealized,
            fill_count=self._fill_counts[strategy_instance_id],
            funding_event_count=self._funding_counts[strategy_instance_id],
        )

    def reconcile(
        self,
        account: AuthoritativeAccountSnapshot,
        marks: Mapping[str, Decimal],
    ) -> AttributionReconciliation:
        snapshots = tuple(
            self.snapshot(strategy_id, marks)
            for strategy_id, allocation in sorted(self._allocations.items())
            if allocation.settlement_asset == account.settlement_asset
        )
        attributed_positions: defaultdict[str, Decimal] = defaultdict(Decimal)
        for snapshot in snapshots:
            for position in snapshot.positions:
                attributed_positions[position.instrument] += position.quantity
        instruments = set(account.positions) | set(attributed_positions)
        position_residuals = {
            instrument: (
                account.positions.get(instrument, Decimal("0"))
                - attributed_positions[instrument]
            )
            for instrument in sorted(instruments)
        }
        return AttributionReconciliation(
            settlement_asset=account.settlement_asset,
            position_residuals=position_residuals,
            allocation_residual=(
                account.initial_equity
                - sum(
                    (item.allocated_capital for item in snapshots),
                    Decimal("0"),
                )
            ),
            gross_realized_pnl_residual=(
                account.gross_realized_pnl
                - sum(
                    (item.gross_realized_pnl for item in snapshots),
                    Decimal("0"),
                )
            ),
            fee_residual=(
                account.total_fees
                - sum(
                    (item.total_fees for item in snapshots),
                    Decimal("0"),
                )
            ),
            funding_residual=(
                account.total_funding
                - sum(
                    (item.total_funding for item in snapshots),
                    Decimal("0"),
                )
            ),
            unrealized_pnl_residual=(
                account.unrealized_pnl
                - sum(
                    (item.unrealized_pnl for item in snapshots),
                    Decimal("0"),
                )
            ),
        )

    def _validate_fill_identity(
        self,
        *,
        fill: StrategyAccountingFill,
        intent: RuleIntentProposal,
        strategy_instance_id: str,
    ) -> None:
        if fill.intent_key != intent.intent_key:
            raise ValueError("accounting fill intent key does not match intent")
        if fill.side != intent.side:
            raise ValueError("accounting fill side does not match intent")
        if fill.quantity != intent.quantity:
            raise ValueError("partial accounted fills are not supported in v1")
        if fill.quantity_unit != intent.quantity_unit:
            raise ValueError("accounting fill quantity unit does not match intent")
        if fill.instrument != intent.tags.get("instrument"):
            raise ValueError("accounting fill instrument does not match intent")
        if strategy_instance_id not in self._allocations:
            raise ValueError("accounting fill has no Strategy Allocation")
        allocation = self._allocations[strategy_instance_id]
        if fill.settlement_asset != allocation.settlement_asset:
            raise ValueError(
                "accounting fill asset does not match Strategy Allocation"
            )

    @staticmethod
    def _validate_position_effect(
        previous: VirtualPosition | None,
        signed_quantity: Decimal,
        effect: PositionEffect,
        quantity: Decimal,
    ) -> None:
        if effect == PositionEffect.OPEN:
            if previous is not None:
                raise ValueError("OPEN requires an empty owner position")
            return
        if effect == PositionEffect.INCREASE:
            if previous is None or previous.quantity * signed_quantity <= 0:
                raise ValueError("INCREASE requires same-direction position")
            return
        if previous is None or previous.quantity * signed_quantity >= 0:
            raise ValueError("REDUCE/CLOSE requires opposite-side position")
        if quantity > abs(previous.quantity):
            raise ValueError("owner reduce quantity exceeds virtual position")
        if effect == PositionEffect.CLOSE and quantity != abs(previous.quantity):
            raise ValueError("CLOSE must consume the whole owner position")

    @staticmethod
    def _realized_pnl(
        previous: VirtualPosition | None,
        fill: StrategyAccountingFill,
        signed_quantity: Decimal,
    ) -> Decimal:
        if previous is None or previous.quantity * signed_quantity >= 0:
            return Decimal("0")
        closing = min(abs(previous.quantity), fill.quantity)
        direction = Decimal("1") if previous.quantity > 0 else Decimal("-1")
        if previous.product_type == StrategyProductType.INVERSE_PERPETUAL:
            return (
                direction
                * closing
                * previous.contract_size
                * (
                    Decimal("1") / previous.average_entry_price
                    - Decimal("1") / fill.price
                )
            )
        return (
            direction
            * closing
            * previous.contract_size
            * (fill.price - previous.average_entry_price)
        )

    @staticmethod
    def _next_position(
        *,
        previous: VirtualPosition | None,
        fill: StrategyAccountingFill,
        signed_quantity: Decimal,
        next_quantity: Decimal,
        strategy_instance_id: str,
        position_owner_id: str,
        product_type: StrategyProductType,
    ) -> VirtualPosition | None:
        if next_quantity == 0:
            return None
        if previous is None:
            average = fill.price
        elif previous.quantity * signed_quantity > 0:
            if product_type == StrategyProductType.INVERSE_PERPETUAL:
                average = abs(next_quantity) / (
                    abs(previous.quantity) / previous.average_entry_price
                    + abs(signed_quantity) / fill.price
                )
            else:
                average = (
                    abs(previous.quantity) * previous.average_entry_price
                    + abs(signed_quantity) * fill.price
                ) / abs(next_quantity)
        else:
            average = previous.average_entry_price
        return VirtualPosition(
            position_owner_id=position_owner_id,
            strategy_instance_id=strategy_instance_id,
            instrument=fill.instrument,
            product_type=product_type,
            settlement_asset=fill.settlement_asset,
            quantity_unit=fill.quantity_unit,
            contract_size=fill.contract_size,
            quantity=next_quantity,
            average_entry_price=average,
        )
