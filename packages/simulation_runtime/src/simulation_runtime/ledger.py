from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import TYPE_CHECKING, Mapping, Protocol

from .models import OrderSide, SimFill


if TYPE_CHECKING:
    from .funding import FundingSettlement


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

    @property
    def gross_realized_pnl(self) -> Decimal: ...

    @property
    def total_fees(self) -> Decimal: ...

    @property
    def net_realized_pnl(self) -> Decimal: ...

    @property
    def total_funding(self) -> Decimal: ...

    @property
    def net_pnl_after_fees_and_funding(self) -> Decimal: ...

    def apply(self, fill: SimFill) -> None: ...

    def apply_funding(self, settlement: FundingSettlement) -> None: ...

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
        self._gross_realized_pnl: defaultdict[
            str,
            Decimal,
        ] = defaultdict(Decimal)
        self._fees: defaultdict[str, Decimal] = defaultdict(Decimal)
        self._funding: defaultdict[str, Decimal] = defaultdict(Decimal)

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
        instruments = set(self._gross_realized_pnl) | set(self._fees)
        return {
            instrument: (
                self._gross_realized_pnl[instrument]
                - self._fees[instrument]
            )
            for instrument in instruments
        }

    @property
    def gross_realized_pnl_by_instrument(self) -> dict[str, Decimal]:
        return dict(self._gross_realized_pnl)

    @property
    def gross_realized_pnl(self) -> Decimal:
        if all(
            quantity == 0
            for quantity in self._positions.values()
        ):
            return (
                self.cash
                - self.initial_equity
                + self.total_fees
                - self.total_funding
            )
        return sum(
            self._gross_realized_pnl.values(),
            Decimal("0"),
        )

    @property
    def total_fees(self) -> Decimal:
        return sum(self._fees.values(), Decimal("0"))

    @property
    def net_realized_pnl(self) -> Decimal:
        return self.gross_realized_pnl - self.total_fees

    @property
    def total_funding(self) -> Decimal:
        """Signed wallet change: positive received, negative paid."""

        return sum(self._funding.values(), Decimal("0"))

    @property
    def net_pnl_after_fees_and_funding(self) -> Decimal:
        return self.net_realized_pnl + self.total_funding

    @property
    def realized_pnl(self) -> Decimal:
        """Backward-compatible alias for net realized PnL."""

        return self.net_realized_pnl

    def apply(self, fill: SimFill) -> None:
        if fill.fee_asset.upper() != self.equity_asset.upper():
            raise ValueError(
                f"fee asset {fill.fee_asset} does not match "
                f"ledger equity asset {self.equity_asset}"
            )
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
            self._gross_realized_pnl[fill.instrument] += (
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
        self.cash -= fill.fee_amount
        self._fees[fill.instrument] += fill.fee_amount
        self._positions[fill.instrument] = new_quantity

    def apply_funding(self, settlement: FundingSettlement) -> None:
        if settlement.settlement_asset.upper() != self.equity_asset.upper():
            raise ValueError(
                f"funding asset {settlement.settlement_asset} does not "
                f"match ledger equity asset {self.equity_asset}"
            )
        if settlement.instrument not in self._positions:
            raise ValueError(
                "funding settlement instrument has no ledger position: "
                f"{settlement.instrument}"
            )
        if (
            self._positions[settlement.instrument]
            != settlement.position_quantity
        ):
            raise ValueError(
                "funding settlement position does not match ledger"
            )
        self.cash += settlement.wallet_delta
        self._funding[settlement.instrument] += (
            settlement.wallet_delta
        )

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
        asset = self.equity_asset.lower()
        return {
            f"gross_realized_pnl_{asset}": self.gross_realized_pnl,
            f"total_fees_{asset}": self.total_fees,
            f"net_realized_pnl_{asset}": self.net_realized_pnl,
            f"total_funding_{asset}": self.total_funding,
            f"net_pnl_after_fees_and_funding_{asset}": (
                self.net_pnl_after_fees_and_funding
            ),
            f"total_equity_{asset}": self.equity(marks),
        }
