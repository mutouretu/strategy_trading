"""Compare one fixed rule plan with live-engine orders on simulator bars."""

from __future__ import annotations

import sys
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest


SIMULATOR_ROOT = Path(__file__).resolve().parents[2] / "market_simulator"
if not SIMULATOR_ROOT.exists():
    pytest.skip("market_simulator project is unavailable", allow_module_level=True)
for package in ("market_protocol", "market_simulator"):
    sys.path.insert(0, str(SIMULATOR_ROOT / "packages" / package / "src"))

from market_simulator import FixedBarMarketSource  # noqa: E402

from grid_rule.fixed_equal_usdt import (  # noqa: E402
    FixedEntryMode, FixedGridSpec, build_fixed_cells,
    initial_sell_allocations, triggered_entry_ids,
)
from grid_rule.fixed_window import (  # noqa: E402
    WindowCell, WindowInput, plan_fixed_window,
)
from grid_server.application.fixed_engine import FixedGridEngine  # noqa: E402
from grid_server.application.service import GridService  # noqa: E402
from grid_server.domain import CellStage, SymbolFilters  # noqa: E402
from grid_server.infrastructure.sqlite_store import SQLiteStore  # noqa: E402
from grid_server.runtime.supervisor import StrategySupervisor  # noqa: E402
from grid_server.simulation import FixedGridReplay  # noqa: E402
from tests.fakes import FakeExchange  # noqa: E402


def test_fixed_rule_matches_live_seed_window_and_one_simulated_buy() -> None:
    source = FixedBarMarketSource("SOXLUSDT", [
        ("50.45", "50.45", "50.45", "50.45"),
        ("50.45", "50.45", "50.25", "50.25"),
    ], step_milliseconds=60_000)
    first = source.reset()
    spec = FixedGridSpec(
        "parity-soxl", Decimal("40"), Decimal("60"), 135,
        Decimal("2000"), Decimal("0.01"),
    )
    rule_cells = build_fixed_cells(spec)
    allocations = initial_sell_allocations(
        rule_cells, first.close, step_size=Decimal("0.01"),
        min_qty=Decimal("0.01"), min_notional=Decimal("5"),
    )
    assert sum(allocations.values(), Decimal("0")) == Decimal("15.38")
    window = plan_fixed_window(WindowInput(
        mark=first.close,
        cells=tuple(WindowCell(
            cell_id=cell.cell_id, index=cell.index,
            buy_price=cell.buy_price, sell_price=cell.sell_price,
            stage="pending_exit" if cell.cell_id in allocations else "pending_entry",
            open_qty=allocations.get(cell.cell_id, Decimal("0")),
            has_entry_order=False, has_exit_order=False,
            entry_ready=True, exit_ready=True,
        ) for cell in rule_cells),
    ))
    assert window.rebalance
    assert len(window.entry_ids) == len(window.exit_ids) == 10
    assert initial_sell_allocations(
        rule_cells, first.close, step_size=Decimal("0.01"),
        min_qty=Decimal("0.01"), min_notional=Decimal("5"),
        entry_mode=FixedEntryMode.PRICE_TRIGGERED,
    ) == {}
    assert triggered_entry_ids(rule_cells, first.close)

    with tempfile.TemporaryDirectory() as directory:
        store = SQLiteStore(Path(directory) / "parity.sqlite3")
        exchange = FakeExchange(first.close)
        exchange.filters = SymbolFilters(
            tick_size=Decimal("0.01"), step_size=Decimal("0.01"),
            min_qty=Decimal("0.01"), min_notional=Decimal("5"),
            contract_type="TRADIFI_PERPETUAL",
            percent_price_up=Decimal("1.03"),
            percent_price_down=Decimal("0.97"),
        )
        service = GridService(
            store, StrategySupervisor(store, Path(directory) / "logs"),
        )
        config = service.create_fixed(
            "SOXLUSDT", spec.lower_price, spec.upper_price,
            spec.grid_count, spec.grid_budget_usdt, spec.tick_size,
        )
        # Rule identifiers use the configured strategy namespace.
        live_rule_cells = build_fixed_cells(FixedGridSpec(
            config.strategy_id, spec.lower_price, spec.upper_price,
            spec.grid_count, spec.grid_budget_usdt, spec.tick_size,
        ))
        live_allocations = initial_sell_allocations(
            live_rule_cells, first.close, step_size=Decimal("0.01"),
            min_qty=Decimal("0.01"), min_notional=Decimal("5"),
        )
        expected_window = plan_fixed_window(WindowInput(
            mark=first.close,
            cells=tuple(WindowCell(
                cell_id=cell.cell_id, index=cell.index,
                buy_price=cell.buy_price, sell_price=cell.sell_price,
                stage=("pending_exit" if cell.cell_id in live_allocations
                       else "pending_entry"),
                open_qty=live_allocations.get(cell.cell_id, Decimal("0")),
                has_entry_order=False, has_exit_order=False,
                entry_ready=True, exit_ready=True,
            ) for cell in live_rule_cells),
        ))
        store.mark_started(config.strategy_id)
        engine = FixedGridEngine(store, exchange, config.strategy_id)
        engine.tick()
        live_cells = store.list_cells(config.strategy_id)
        assert [
            (cell.cell_id, cell.buy_price, cell.sell_price, cell.budget_usdt)
            for cell in live_cells
        ] == [
            (cell.cell_id, cell.buy_price, cell.sell_price, cell.budget_usdt)
            for cell in live_rule_cells
        ]
        assert {
            cell.cell_id: cell.open_qty for cell in live_cells if cell.open_qty > 0
        } == live_allocations
        assert {
            cell.cell_id for cell in live_cells if cell.entry_order_id is not None
        } == expected_window.entry_ids
        assert {
            cell.cell_id for cell in live_cells if cell.exit_order_id is not None
        } == expected_window.exit_ids

        second = source.next()
        for order in tuple(exchange.get_open_orders(config.symbol)):
            if second.low <= order.price <= second.high:
                exchange.fill(order.order_id, order.price)
        exchange.mark = second.close
        engine.tick()
        live_cells = store.list_cells(config.strategy_id)
        bought = next(cell for cell in live_cells if cell.index == 77)
        assert bought.stage == CellStage.PENDING_EXIT
        assert bought.exit_order_id is not None
        assert exchange.orders[bought.exit_order_id].price == live_rule_cells[76].sell_price
        assert exchange.orders[bought.exit_order_id].original_qty == bought.open_qty


def test_market_source_replays_the_live_engine_without_exchange_io() -> None:
    source = FixedBarMarketSource("SOXLUSDT", [
        ("50.45", "50.45", "50.45", "50.45"),
        ("50.45", "50.45", "50.25", "50.25"),
        ("50.25", "50.65", "50.25", "50.65"),
    ])
    result = FixedGridReplay(
        source, symbol="SOXLUSDT",
        lower_price=Decimal("40"), upper_price=Decimal("60"),
        grid_count=135, grid_budget_usdt=Decimal("2000"),
        filters=SymbolFilters(
            tick_size=Decimal("0.01"), step_size=Decimal("0.01"),
            min_qty=Decimal("0.01"), min_notional=Decimal("5"),
            contract_type="TRADIFI_PERPETUAL",
            percent_price_up=Decimal("1.03"),
            percent_price_down=Decimal("0.97"),
        ),
    ).run()
    assert len(result.frames) == 3
    assert [(frame.entry_order_count, frame.exit_order_count)
            for frame in result.frames] == [(10, 10), (9, 11), (11, 9)]
    assert [frame.completed_cycles for frame in result.frames] == [0, 0, 2]
    assert all(frame.logical_position_qty == frame.exchange_position_qty
               for frame in result.frames)
    assert any(event["event_type"] == "CYCLE_CLOSED" for event in result.events)
