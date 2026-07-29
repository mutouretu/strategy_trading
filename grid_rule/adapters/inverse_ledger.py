"""Simulation ledger for coin-margined inverse contracts."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Mapping

from simulation_runtime import OrderSide, SimFill


if TYPE_CHECKING:
    from simulation_runtime import FundingSettlement


class InverseContractLedger:
    """Coin-margined inverse-contract account with a separate spot holding.

    Contract quantities are counts. Each contract has a fixed quote-currency
    face value, while realized and unrealized PnL settle in the base asset.
    The spot balance is valuation-only and is never changed by grid fills.
    """

    def __init__(
        self,
        *,
        instrument: str,
        contract_size: Decimal,
        spot_base_balance: Decimal,
        futures_wallet_balance: Decimal,
        base_asset: str = "BTC",
        quote_asset: str = "USDT",
        notional_asset: str = "USD",
    ) -> None:
        self.instrument = instrument
        self.contract_size = Decimal(contract_size)
        self.spot_base_balance = Decimal(spot_base_balance)
        self._futures_wallet_balance = Decimal(futures_wallet_balance)
        self._initial_futures_wallet_balance = (
            self._futures_wallet_balance
        )
        self.base_asset = base_asset.upper()
        # quote_asset is the reporting/valuation currency. Binance COIN-M
        # contract face value and maintenance brackets are denominated in
        # USD, which is intentionally kept as a separate unit.
        self.quote_asset = quote_asset.upper()
        self.notional_asset = notional_asset.upper()
        if not self.instrument.strip():
            raise ValueError("instrument must not be empty")
        if self.contract_size <= 0:
            raise ValueError("contract_size must be > 0")
        if self.spot_base_balance < 0:
            raise ValueError("spot_base_balance must be >= 0")
        if self._futures_wallet_balance < 0:
            raise ValueError("futures_wallet_balance must be >= 0")
        if (
            not self.base_asset
            or not self.quote_asset
            or not self.notional_asset
        ):
            raise ValueError("asset names must not be empty")

        self.initial_equity = (
            self.spot_base_balance + self._futures_wallet_balance
        )
        self.equity_asset = self.base_asset
        self._position = Decimal("0")
        self._average_cost = Decimal("0")
        self._gross_realized_pnl = Decimal("0")
        self._total_fees = Decimal("0")
        self._total_funding = Decimal("0")

    @property
    def cash(self) -> Decimal:
        """Futures wallet balance in the settlement/base asset."""

        return self._futures_wallet_balance

    @property
    def futures_wallet_balance(self) -> Decimal:
        """Settled collateral balance, excluding unrealized PnL."""

        return self._futures_wallet_balance

    @property
    def position_quantity(self) -> Decimal:
        """Signed inverse-contract count."""

        return self._position

    @property
    def average_entry_price(self) -> Decimal:
        """Inverse-weighted average entry price for the open position."""

        return self._average_cost

    def clone(self) -> InverseContractLedger:
        """Return an independent copy for deterministic account projection."""

        cloned = InverseContractLedger(
            instrument=self.instrument,
            contract_size=self.contract_size,
            spot_base_balance=self.spot_base_balance,
            futures_wallet_balance=(
                self._initial_futures_wallet_balance
            ),
            base_asset=self.base_asset,
            quote_asset=self.quote_asset,
            notional_asset=self.notional_asset,
        )
        cloned._futures_wallet_balance = self._futures_wallet_balance
        cloned._position = self._position
        cloned._average_cost = self._average_cost
        cloned._gross_realized_pnl = self._gross_realized_pnl
        cloned._total_fees = self._total_fees
        cloned._total_funding = self._total_funding
        return cloned

    @property
    def positions(self) -> dict[str, Decimal]:
        return {self.instrument: self._position}

    @property
    def average_costs(self) -> dict[str, Decimal]:
        if self._position == 0:
            return {}
        return {self.instrument: self._average_cost}

    @property
    def realized_pnl(self) -> Decimal:
        """Backward-compatible alias for net realized PnL."""

        return self.net_realized_pnl

    @property
    def gross_realized_pnl(self) -> Decimal:
        return self._gross_realized_pnl

    @property
    def total_fees(self) -> Decimal:
        return self._total_fees

    @property
    def net_realized_pnl(self) -> Decimal:
        return self._gross_realized_pnl - self._total_fees

    @property
    def total_funding(self) -> Decimal:
        """Signed wallet change: positive received, negative paid."""

        return self._total_funding

    @property
    def net_pnl_after_fees_and_funding(self) -> Decimal:
        return self.net_realized_pnl + self.total_funding

    def apply(self, fill: SimFill) -> None:
        if fill.instrument != self.instrument:
            raise ValueError(
                f"unexpected instrument {fill.instrument}; "
                f"expected {self.instrument}"
            )
        if fill.price <= 0 or fill.quantity <= 0:
            raise ValueError("fill price and quantity must be > 0")
        if fill.fee_asset.upper() != self.base_asset:
            raise ValueError(
                f"fee asset {fill.fee_asset} does not match "
                f"ledger settlement asset {self.base_asset}"
            )

        signed_quantity = (
            fill.quantity
            if fill.side == OrderSide.BUY
            else -fill.quantity
        )
        old_quantity = self._position
        new_quantity = old_quantity + signed_quantity

        if old_quantity == 0 or old_quantity * signed_quantity > 0:
            old_inverse_cost = (
                abs(old_quantity) / self._average_cost
                if old_quantity != 0
                else Decimal("0")
            )
            new_inverse_cost = abs(signed_quantity) / fill.price
            self._average_cost = (
                abs(new_quantity)
                / (old_inverse_cost + new_inverse_cost)
            )
        else:
            closing_quantity = min(
                abs(old_quantity),
                abs(signed_quantity),
            )
            direction = (
                Decimal("1")
                if old_quantity > 0
                else Decimal("-1")
            )
            realized = (
                direction
                * closing_quantity
                * self.contract_size
                * (
                    Decimal("1") / self._average_cost
                    - Decimal("1") / fill.price
                )
            )
            self._gross_realized_pnl += realized
            if new_quantity == 0:
                self._average_cost = Decimal("0")
            elif old_quantity * new_quantity < 0:
                self._average_cost = fill.price

        self._total_fees += fill.fee_amount
        self._futures_wallet_balance = (
            self._initial_futures_wallet_balance
            + self.net_pnl_after_fees_and_funding
        )
        self._position = new_quantity

    def apply_funding(self, settlement: FundingSettlement) -> None:
        if settlement.instrument != self.instrument:
            raise ValueError(
                f"unexpected funding instrument "
                f"{settlement.instrument}; expected {self.instrument}"
            )
        if settlement.settlement_asset.upper() != self.base_asset:
            raise ValueError(
                f"funding asset {settlement.settlement_asset} does not "
                f"match ledger settlement asset {self.base_asset}"
            )
        if settlement.position_quantity != self._position:
            raise ValueError(
                "funding settlement position does not match ledger"
            )
        self._total_funding += settlement.wallet_delta
        self._futures_wallet_balance = (
            self._initial_futures_wallet_balance
            + self.net_pnl_after_fees_and_funding
        )

    def unrealized_pnl(
        self,
        marks: Mapping[str, Decimal],
    ) -> Decimal:
        mark = self._mark(marks)
        if self._position == 0:
            return Decimal("0")
        direction = Decimal("1") if self._position > 0 else Decimal("-1")
        return (
            direction
            * abs(self._position)
            * self.contract_size
            * (
                Decimal("1") / self._average_cost
                - Decimal("1") / mark
            )
        )

    def equity(self, marks: Mapping[str, Decimal]) -> Decimal:
        return (
            self.spot_base_balance
            + self._futures_wallet_balance
            + self.unrealized_pnl(marks)
        )

    def account_metrics(
        self,
        marks: Mapping[str, Decimal],
    ) -> Mapping[str, Decimal]:
        mark = self._mark(marks)
        unrealized = self.unrealized_pnl(marks)
        futures_equity = self._futures_wallet_balance + unrealized
        total_base = self.spot_base_balance + futures_equity
        base = self.base_asset.lower()
        quote = self.quote_asset.lower()
        notional = self.notional_asset.lower()
        return {
            f"spot_{base}": self.spot_base_balance,
            f"futures_wallet_{base}": self._futures_wallet_balance,
            f"futures_unrealized_pnl_{base}": unrealized,
            f"futures_equity_{base}": futures_equity,
            f"gross_realized_pnl_{base}": self.gross_realized_pnl,
            f"total_fees_{base}": self.total_fees,
            f"net_realized_pnl_{base}": self.net_realized_pnl,
            f"total_funding_{base}": self.total_funding,
            f"net_pnl_after_fees_and_funding_{base}": (
                self.net_pnl_after_fees_and_funding
            ),
            f"realized_pnl_{base}": self.realized_pnl,
            f"total_equity_{base}": total_base,
            f"total_equity_{quote}": total_base * mark,
            f"{base}_mark_{quote}": mark,
            f"contract_notional_{notional}": (
                abs(self._position) * self.contract_size
            ),
        }

    def _mark(self, marks: Mapping[str, Decimal]) -> Decimal:
        try:
            mark = Decimal(marks[self.instrument])
        except KeyError as exc:
            raise KeyError(
                f"missing mark for: {self.instrument}"
            ) from exc
        if mark <= 0:
            raise ValueError("mark price must be > 0")
        return mark
