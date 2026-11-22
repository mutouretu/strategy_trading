from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
import pytest

from grid_server.application.fixed_engine import FixedGridEngine
from grid_server.application.live_entry_policy import is_live_exchange
from grid_server.application.service import GridService
from grid_server.domain import CellStage, FixedEntryMode, SymbolFilters
from grid_server.infrastructure.binance import BinanceFuturesExchange
from grid_server.infrastructure.okx import OkxSwapExchange
from grid_server.infrastructure.snapshot_exchange import SnapshotExchange
from grid_server.infrastructure.sqlite_store import SQLiteStore
from grid_server.interfaces.api import create_app
from tests.fakes import FakeExchange


def test_production_detection_does_not_include_demo() -> None:
    assert is_live_exchange(BinanceFuturesExchange("", ""))
    assert is_live_exchange(SnapshotExchange(OkxSwapExchange("", "", "")))
    assert not is_live_exchange(BinanceFuturesExchange("", "", "https://demo-fapi.binance.com"))
    assert not is_live_exchange(OkxSwapExchange("", "", "", simulated=True))


def test_live_api_rejects_seeded_mode_before_exchange_calls(tmp_path) -> None:
    exchange = BinanceFuturesExchange("", "")
    client = TestClient(create_app(tmp_path / "live.sqlite3", lambda: exchange))
    payload = {
        "symbol": "SOLUSDT", "lower_price": "60", "upper_price": "80",
        "grid_count": 20, "grid_budget_usdt": "500",
        "fixed_entry_mode": "seeded", "upper_seed_count": 0,
    }
    for endpoint in ("/fixed-grids/preview", "/fixed-grids"):
        response = client.post(endpoint, json=payload)
        assert response.status_code == 400
        assert "实盘" in response.json()["detail"]


def test_live_api_allows_three_deferred_upper_seeds(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "live.sqlite3"
    exchange = BinanceFuturesExchange("", "")
    monkeypatch.setattr(exchange, "get_symbol_filters", lambda symbol: SymbolFilters(
        tick_size=Decimal("0.01"), step_size=Decimal("0.01"),
        contract_type="PERPETUAL",
    ))
    monkeypatch.setattr(exchange, "get_commission_rates", lambda symbol: (
        Decimal("0.0002"), Decimal("0.0005"),
    ))
    monkeypatch.setattr(exchange, "get_mark_price", lambda symbol: Decimal("70"))
    client = TestClient(create_app(db_path, lambda: exchange))
    payload = {
        "symbol": "SOLUSDT", "lower_price": "60", "upper_price": "80",
        "grid_count": 20, "grid_budget_usdt": "500",
        "fixed_entry_mode": "price_triggered", "upper_seed_count": 3,
    }
    preview = client.post("/fixed-grids/preview", json=payload)
    assert preview.status_code == 200
    assert preview.json()["allocation"]["initial_upper_seed_cell_count"] == 0
    created = client.post("/fixed-grids", json=payload)
    assert created.status_code == 201
    strategy_id = created.json()["strategy_id"]
    assert created.json()["upper_seed_count"] == 3
    assert created.json()["upper_seed_active_count"] == 0
    assert client.post(f"/strategies/{strategy_id}/upper-seed", json={"count": 0}).status_code == 200
    assert client.post(f"/strategies/{strategy_id}/upper-seed", json={"count": 3}).status_code == 200


def test_live_upper_seed_waits_for_price_advance_and_survives_restart(tmp_path) -> None:
    path = tmp_path / "live.sqlite3"
    store = SQLiteStore(path)
    config = GridService(store).create_fixed(
        "SOLUSDT", Decimal("80"), Decimal("120"), 40,
        Decimal("500"), Decimal("0.01"),
        fixed_entry_mode=FixedEntryMode.PRICE_TRIGGERED, upper_seed_count=3,
    )
    store.mark_started(config.strategy_id)
    assert store.arm_upper_seed_cells(
        config.strategy_id, Decimal("100"), defer_until_price_advance=True,
    ) == 0
    baseline = store.get_strategy(config.strategy_id).upper_seed_frontier
    assert baseline is not None
    restarted = SQLiteStore(path)
    assert restarted.arm_upper_seed_cells(
        config.strategy_id, Decimal("100"), defer_until_price_advance=True,
    ) == 0
    assert restarted.arm_upper_seed_cells(
        config.strategy_id, Decimal("95"), defer_until_price_advance=True,
    ) == 0
    next_buy = min(
        cell.buy_price for cell in restarted.list_cells(config.strategy_id)
        if cell.index > baseline
    )
    assert restarted.arm_upper_seed_cells(
        config.strategy_id, next_buy, defer_until_price_advance=True,
    ) == 3
    assert restarted.upper_seed_active_count(config.strategy_id) == 3
    assert restarted.arm_upper_seed_cells(
        config.strategy_id, Decimal("95"), defer_until_price_advance=True,
    ) == 0


def test_live_engine_places_no_upper_buy_at_start_then_arms_three(tmp_path, monkeypatch) -> None:
    import grid_server.application.fixed_engine as fixed_engine_module

    store = SQLiteStore(tmp_path / "live.sqlite3")
    exchange = FakeExchange(Decimal("100"))
    config = GridService(store).create_fixed(
        "SOLUSDT", Decimal("80"), Decimal("120"), 40,
        Decimal("500"), Decimal("0.01"),
        fixed_entry_mode=FixedEntryMode.PRICE_TRIGGERED, upper_seed_count=3,
    )
    store.mark_started(config.strategy_id)
    monkeypatch.setattr(fixed_engine_module, "is_live_exchange", lambda exchange: True)
    engine = FixedGridEngine(store, exchange, config.strategy_id)
    engine.tick()
    assert store.upper_seed_active_count(config.strategy_id) == 0
    assert exchange.get_positions() == []
    assert exchange.calls.get("place_market_order", 0) == 0
    baseline = store.get_strategy(config.strategy_id).upper_seed_frontier
    assert baseline is not None
    exchange.mark = min(
        cell.buy_price for cell in store.list_cells(config.strategy_id)
        if cell.index > baseline
    )
    engine.tick()
    assert store.upper_seed_active_count(config.strategy_id) == 3
    upper_ids = store.list_active_upper_seed_cell_ids(config.strategy_id)
    assert [store.get_cell(config.strategy_id, cell_id).index for cell_id in upper_ids] == [
        baseline + 2, baseline + 3, baseline + 4,
    ]
    assert all(store.get_cell(config.strategy_id, cell_id).entry_order_id is not None
               for cell_id in upper_ids)
    assert exchange.calls.get("place_market_order", 0) == 0


def test_reenable_preserves_held_seed_frontier(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "live.sqlite3")
    config = GridService(store).create_fixed(
        "SOLUSDT", Decimal("80"), Decimal("120"), 40,
        Decimal("500"), Decimal("0.01"),
        fixed_entry_mode=FixedEntryMode.PRICE_TRIGGERED, upper_seed_count=3,
    )
    store.mark_started(config.strategy_id)
    assert store.arm_upper_seed_cells(config.strategy_id, Decimal("100")) == 3
    old_ids = store.list_active_upper_seed_cell_ids(config.strategy_id)
    old_max = max(store.get_cell(config.strategy_id, cell_id).index for cell_id in old_ids)
    store.set_upper_seed_count(config.strategy_id, 0)
    store.set_upper_seed_count(config.strategy_id, 3)
    assert store.arm_upper_seed_cells(
        config.strategy_id, Decimal("90"), defer_until_price_advance=True,
    ) == 0
    assert store.get_strategy(config.strategy_id).upper_seed_frontier == old_max
    old = store.get_cell(config.strategy_id, old_ids[0])
    old.cycle_count += 1
    old.stage = CellStage.PENDING_ENTRY
    store.save_cell(old)
    assert store.arm_upper_seed_cells(
        config.strategy_id, Decimal("100"), defer_until_price_advance=True,
    ) == 0
    next_buy = min(cell.buy_price for cell in store.list_cells(config.strategy_id)
                   if cell.index > old_max)
    assert store.arm_upper_seed_cells(
        config.strategy_id, next_buy, defer_until_price_advance=True,
    ) == 1


def test_live_engine_still_rejects_seeded_grid(tmp_path, monkeypatch) -> None:
    store = SQLiteStore(tmp_path / "live.sqlite3")
    config = GridService(store).create_fixed(
        "SOLUSDT", Decimal("60"), Decimal("80"), 20,
        Decimal("500"), Decimal("0.01"),
        fixed_entry_mode=FixedEntryMode.SEEDED,
    )
    exchange = BinanceFuturesExchange("", "")

    monkeypatch.setenv("GRID_ENABLE_BINANCE_LIVE_FIXED_GRID", "1")
    with pytest.raises(ValueError, match="实盘"):
        FixedGridEngine(store, exchange, config.strategy_id).initialize()
