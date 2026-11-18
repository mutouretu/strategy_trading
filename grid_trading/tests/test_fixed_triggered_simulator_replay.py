"""Replay the 2026-11-17 SOXL trigger-grid outage with simulator bars.

This drives the live fixed-grid engine with a deterministic market_simulator
price source. The generic GridRuleSimulationAdapter uses a different rule
engine and therefore cannot exercise the scheduler/status interaction here.
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest


SIMULATOR_ROOT = Path(__file__).resolve().parents[2] / "market_simulator"
if not SIMULATOR_ROOT.exists():
    pytest.skip("market_simulator project is unavailable", allow_module_level=True)
for package in ("market_protocol", "market_simulator"):
    sys.path.insert(0, str(SIMULATOR_ROOT / "packages" / package / "src"))

from market_simulator import FixedBarMarketSource  # noqa: E402

from grid_server.application.fixed_engine import FixedGridEngine
from grid_server.application.service import GridService
from grid_server.domain import FixedEntryMode, StrategyStatus, SymbolFilters
from grid_server.infrastructure.sqlite_store import SQLiteStore
from tests.fakes import FakeExchange


PRICES = (
    "50.45", "50.40", "50.25", "50.10", "49.95", "49.80",
    "49.65", "49.50", "49.35", "49.21", "49.06", "48.91",
    "48.76", "48.62", "48.47", "48.33",
)


class PreviousStatusGuardStore(SQLiteStore):
    """Recreate only the pre-fix guard that rejected scheduler ERROR retries."""

    def arm_triggered_fixed_cells(self, strategy_id: str, mark: Decimal) -> int:
        if self.get_strategy(strategy_id).status == StrategyStatus.ERROR:
            raise ValueError("price-triggered fixed grid is not active")
        return super().arm_triggered_fixed_cells(strategy_id, mark)


@dataclass(frozen=True)
class ReplayResult:
    status: StrategyStatus
    held_cells: int
    sell_orders: int
    unpaired_indices: tuple[int, ...]
    nearest_ten_unpaired: tuple[int, ...]
    errors: tuple[str, ...]


def replay(store_type: type[SQLiteStore]) -> ReplayResult:
    # Each monotone bar touches resting limits at their own grid price.
    # A limit placed during this bar first becomes eligible on the next bar.
    bars = [(PRICES[0],) * 4] + [
        (previous, previous, current, current)
        for previous, current in zip(PRICES, PRICES[1:])
    ]
    source = FixedBarMarketSource(
        "SOXLUSDT", bars, step_milliseconds=60_000,
    )
    with tempfile.TemporaryDirectory() as directory:
        store = store_type(Path(directory) / "grid.sqlite3")
        exchange = FakeExchange(Decimal(PRICES[0]))
        exchange.filters = SymbolFilters(
            tick_size=Decimal("0.01"), step_size=Decimal("0.01"),
            min_qty=Decimal("0.01"), min_notional=Decimal("5"),
            contract_type="TRADIFI_PERPETUAL",
            percent_price_up=Decimal("1.03"),
            percent_price_down=Decimal("0.97"),
        )
        config = GridService(store).create_fixed(
            "SOXLUSDT", Decimal("40"), Decimal("60"), 135,
            Decimal("2000"), Decimal("0.01"),
            fixed_entry_mode=FixedEntryMode.PRICE_TRIGGERED,
        )
        store.mark_started(config.strategy_id)
        engine = FixedGridEngine(store, exchange, config.strategy_id)

        first = source.reset()
        exchange.mark = first.close
        engine.tick()
        assert exchange.get_positions() == []  # No initial seed position.
        errors: list[str] = []
        while not source.done:
            frame = source.next()
            for order in tuple(exchange.get_open_orders(config.symbol)):
                if frame.low <= order.price <= frame.high:
                    exchange.fill(order.order_id, order.price)
            exchange.mark = frame.close
            if frame.sequence == 5:
                # An exchange response interruption made the scheduler mark
                # this otherwise active strategy ERROR at the real incident.
                store.set_status(config.strategy_id, StrategyStatus.ERROR)
            try:
                engine.tick()
            except ValueError as exc:
                errors.append(str(exc))

        held = [
            cell for cell in store.list_cells(config.strategy_id)
            if cell.open_qty > 0
        ]
        nearest = sorted(
            held,
            key=lambda cell: abs(cell.sell_price - Decimal(PRICES[-1])),
        )[:10]
        return ReplayResult(
            status=store.get_strategy(config.strategy_id).status,
            held_cells=len(held),
            sell_orders=sum(cell.exit_order_id is not None for cell in held),
            unpaired_indices=tuple(
                cell.index for cell in held if cell.exit_order_id is None
            ),
            nearest_ten_unpaired=tuple(
                cell.index for cell in nearest if cell.exit_order_id is None
            ),
            errors=tuple(errors),
        )


def test_soxl_drop_reproduces_missing_sells_then_recovers_after_fix() -> None:
    previous = replay(PreviousStatusGuardStore)
    repaired = replay(SQLiteStore)

    assert previous.status == StrategyStatus.ERROR
    assert previous.held_cells == 14
    assert previous.sell_orders == 4
    assert len(previous.unpaired_indices) == 10
    assert len(previous.errors) == 11
    assert set(previous.errors) == {"price-triggered fixed grid is not active"}

    assert repaired.status == StrategyStatus.RUNNING
    assert repaired.held_cells == 15
    assert repaired.sell_orders == 14
    assert repaired.nearest_ten_unpaired == ()
    assert repaired.errors == ()


if __name__ == "__main__":
    for name, store_type in (
        ("previous", PreviousStatusGuardStore),
        ("repaired", SQLiteStore),
    ):
        print(name, replay(store_type))
