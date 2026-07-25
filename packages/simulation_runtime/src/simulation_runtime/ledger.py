from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Mapping, Protocol

from .models import OrderSide, SimFill


class SimulationLedger(Protocol):
    """Accounting port used by the generic simulation runtime."""

    initial_equity: Decimal
    equity_asset: str

    @property
    def cash(self) -> Decimal: ...

    @property
    def positions(self) -> dict[str, Decimal]: ...

    @property
    def average_costs(self) -> dict[str, Decimal]: ...

    @property
    def realized_pnl(self) -> Decimal: ...

    def apply(self, fill: SimFill) -> None: ...

    def equity(self, marks: Mapping[str, Decimal]) -> Decimal: ...

    def account_metrics(
        self,
        marks: Mapping[str, Decimal],
    ) -> Mapping[str, Decimal]: ...


class LinearLedger:
    """Minimal linear quote-currency ledger used by the first-phase runtime."""

    def __init__(
        self,
        initial_equity: Decimal = Decimal("0"),
        *,
        equity_asset: str = "USDT",
    ) -> None:
        if not equity_asset.strip():
            raise ValueError("equity_asset must not be empty")
        self.initial_equity = Decimal(initial_equity)
        self.equity_asset = equity_asset
        self.cash = Decimal(initial_equity)
        self._positions: defaultdict[str, Decimal] = defaultdict(Decimal)
        self._average_costs: defaultdict[str, Decimal] = defaultdict(Decimal)
        self._realized_pnl: defaultdict[str, Decimal] = defaultdict(Decimal)

    @property
    def positions(self) -> dict[str, Decimal]:
        return dict(self._positions)

    @property
    def average_costs(self) -> dict[str, Decimal]:
        return {
            instrument: price
            for instrument, price in self._average_costs.items()
            if self._positions[instrument] != 0
        }

    @property
    def realized_pnl_by_instrument(self) -> dict[str, Decimal]:
        return dict(self._realized_pnl)

    @property
    def realized_pnl(self) -> Decimal:
        if all(quantity == 0 for quantity in self._positions.values()):
            return self.cash - self.initial_equity
        return sum(self._realized_pnl.values(), Decimal("0"))

    def apply(self, fill: SimFill) -> None:
        value = fill.price * fill.quantity
        signed_quantity = (
            fill.quantity if fill.side == OrderSide.BUY else -fill.quantity
        )
        old_quantity = self._positions[fill.instrument]
        old_average = self._average_costs[fill.instrument]
        new_quantity = old_quantity + signed_quantity

        if old_quantity == 0 or old_quantity * signed_quantity > 0:
            total_cost = (
                abs(old_quantity) * old_average
                + abs(signed_quantity) * fill.price
            )
            self._average_costs[fill.instrument] = total_cost / abs(new_quantity)
        else:
            closing_quantity = min(abs(old_quantity), abs(signed_quantity))
            direction = Decimal("1") if old_quantity > 0 else Decimal("-1")
            self._realized_pnl[fill.instrument] += (
                (fill.price - old_average) * closing_quantity * direction
            )
            if new_quantity == 0:
                self._average_costs[fill.instrument] = Decimal("0")
            elif old_quantity * new_quantity < 0:
                # The trade crossed through zero; the residual opens at fill price.
                self._average_costs[fill.instrument] = fill.price

        if fill.side == OrderSide.BUY:
            self.cash -= value
        else:
            self.cash += value
        self._positions[fill.instrument] = new_quantity

    def equity(self, marks: Mapping[str, Decimal]) -> Decimal:
        missing = set(self._positions) - set(marks)
        if missing:
            raise KeyError(f"missing marks for: {', '.join(sorted(missing))}")
        return self.cash + sum(
            quantity * marks[instrument]
            for instrument, quantity in self._positions.items()
        )

    def account_metrics(
        self,
        marks: Mapping[str, Decimal],
    ) -> Mapping[str, Decimal]:
        return {
            f"total_equity_{self.equity_asset.lower()}": self.equity(marks),
        }
