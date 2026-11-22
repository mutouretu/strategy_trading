"""A stopped seeded strategy can reuse its ID after an external flat close."""

from __future__ import annotations

import tempfile
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from grid_server.application.fixed_engine import FixedGridEngine
from grid_server.application.service import GridService
from grid_server.domain import CellStage, ExchangeVenue, FixedEntryMode, OrderSide, StrategyStatus
from grid_server.infrastructure.sqlite_store import SQLiteStore
from grid_server.runtime.supervisor import StrategySupervisor
from tests.fakes import FakeExchange


def _stopped_seeded_strategy(
    directory: str, venue: ExchangeVenue = ExchangeVenue.BINANCE,
):
    store = SQLiteStore(Path(directory) / "grid.sqlite3", venue=venue)
    service = GridService(
        store, StrategySupervisor(store, Path(directory) / "logs"),
    )
    config = service.create_fixed(
        "SOXL-USDT-SWAP" if venue == ExchangeVenue.OKX else "SOXLUSDT",
        Decimal("15"), Decimal("90"), 4,
        Decimal("400"), Decimal("0.01"), venue=venue,
    )
    store.mark_started(config.strategy_id)
    store.set_status(config.strategy_id, StrategyStatus.STOPPED)
    with store.connection() as conn:
        conn.execute(
            """INSERT INTO capital_actions(
                id, strategy_id, operation, amount_usdt, status,
                created_at, updated_at
            ) VALUES (?, ?, 'seed', '400', 'applied', ?, ?)""",
            (f"seed:{config.strategy_id}", config.strategy_id,
             "2026-11-01T00:00:00+00:00", "2026-11-01T00:00:00+00:00"),
        )
    old = store.list_cells(config.strategy_id)[0]
    store.save_cell(replace(
        old, stage=CellStage.PENDING_EXIT, open_qty=Decimal("0.50"),
        entry_order_id=777,
    ))
    return store, service, config


def test_flat_reset_keeps_strategy_and_history_but_replaces_all_cells() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store, service, original = _stopped_seeded_strategy(directory)
        exchange = FakeExchange(Decimal("50"))
        previous_start = store.get_first_started_at(original.strategy_id)
        old_ids = {cell.cell_id for cell in store.list_cells(original.strategy_id)}
        plan = service.preview_flat_triggered_reset(
            original.strategy_id, Decimal("15"), Decimal("90"), 4,
            Decimal("400"), exchange,
        )
        assert plan["old_logical_qty"] == Decimal("0.50")
        store.reset_flat_fixed_to_triggered(
            plan["config"], plan["cells"],
            expected_generation=original.grid_generation,
            expected_old_cell_count=4,
            expected_old_qty=Decimal("0.50"),
            historical_net_grid_profit=Decimal("7.25"),
            mark=plan["mark"],
        )
        updated = store.get_strategy(original.strategy_id)
        assert updated is not None
        assert updated.strategy_id == original.strategy_id
        assert updated.grid_generation == original.grid_generation + 1
        assert updated.fixed_entry_mode == FixedEntryMode.PRICE_TRIGGERED
        assert updated.status == StrategyStatus.STOPPED
        assert store.get_first_started_at(original.strategy_id) == previous_start
        cells = store.list_cells(original.strategy_id)
        assert len(cells) == 4
        assert not old_ids.intersection(cell.cell_id for cell in cells)
        assert all(
            cell.stage == CellStage.UNTRIGGERED
            and cell.open_qty == 0 and cell.cycle_count == 0
            and cell.entry_order_id is None and cell.exit_order_id is None
            for cell in cells
        )
        assert sum((cell.budget_usdt for cell in cells), Decimal("0")) == Decimal("400")
        assert store.get_cell_price_history(original.strategy_id).keys() >= old_ids
        reset_event = store.list_events(original.strategy_id)[-1]
        assert reset_event["event_type"] == "GRID_FLAT_TRIGGERED_RESET"
        assert reset_event["payload"]["historical_net_grid_profit_usdt"] == "7.25"
        with pytest.raises(ValueError, match="stopped seeded"):
            service.preview_flat_triggered_reset(
                original.strategy_id, Decimal("15"), Decimal("90"), 4,
                Decimal("400"), exchange,
            )

        store.set_status(original.strategy_id, StrategyStatus.STARTING)
        FixedGridEngine(store, exchange, original.strategy_id).tick()
        assert exchange.calls.get("place_market_order", 0) == 0
        assert exchange.get_positions() == []
        assert exchange.placed
        assert all(order["side"] == OrderSide.BUY for order in exchange.placed)


def test_flat_reset_refuses_position_orders_or_insufficient_balance() -> None:
    with tempfile.TemporaryDirectory() as directory:
        _, service, config = _stopped_seeded_strategy(directory)
        exchange = FakeExchange(Decimal("50"))
        exchange.positions[(config.symbol, "LONG")] = Decimal("0.01")
        with pytest.raises(ValueError, match="position is not flat"):
            service.preview_flat_triggered_reset(
                config.strategy_id, Decimal("15"), Decimal("90"), 4,
                Decimal("400"), exchange,
            )
        exchange.positions.clear()
        exchange.available_balance = Decimal("399")
        with pytest.raises(ValueError, match="below the new grid budget"):
            service.preview_flat_triggered_reset(
                config.strategy_id, Decimal("15"), Decimal("90"), 4,
                Decimal("400"), exchange,
            )


def test_okx_flat_reset_uses_same_strategy_without_seed_position() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store, service, original = _stopped_seeded_strategy(
            directory, ExchangeVenue.OKX,
        )
        exchange = FakeExchange(Decimal("50"))
        old_ids = {cell.cell_id for cell in store.list_cells(original.strategy_id)}
        plan = service.preview_flat_triggered_reset(
            original.strategy_id, Decimal("15"), Decimal("90"), 4,
            Decimal("500"), exchange,
        )
        store.reset_flat_fixed_to_triggered(
            plan["config"], plan["cells"],
            expected_generation=original.grid_generation,
            expected_old_cell_count=4,
            expected_old_qty=Decimal("0.50"),
            historical_net_grid_profit=Decimal("7.25"),
            mark=plan["mark"],
        )
        updated = store.get_strategy(original.strategy_id)
        assert updated is not None
        assert updated.venue == ExchangeVenue.OKX
        assert updated.strategy_id == original.strategy_id
        assert updated.grid_budget_usdt == Decimal("500")
        assert updated.fixed_entry_mode == FixedEntryMode.PRICE_TRIGGERED
        assert updated.grid_generation == original.grid_generation + 1
        cells = store.list_cells(original.strategy_id)
        assert len(cells) == 4
        assert not old_ids.intersection(cell.cell_id for cell in cells)
        assert all(cell.stage == CellStage.UNTRIGGERED for cell in cells)
        store.set_status(original.strategy_id, StrategyStatus.STARTING)
        FixedGridEngine(store, exchange, original.strategy_id).tick()
        assert exchange.calls.get("place_market_order", 0) == 0
        assert exchange.get_positions() == []
        assert exchange.placed
        assert all(order["side"] == OrderSide.BUY for order in exchange.placed)
