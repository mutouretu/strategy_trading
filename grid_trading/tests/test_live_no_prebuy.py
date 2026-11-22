from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
import pytest

from grid_server.application.fixed_engine import FixedGridEngine
from grid_server.application.live_entry_policy import is_live_exchange
from grid_server.application.service import GridService
from grid_server.domain import FixedEntryMode
from grid_server.infrastructure.binance import BinanceFuturesExchange
from grid_server.infrastructure.okx import OkxSwapExchange
from grid_server.infrastructure.snapshot_exchange import SnapshotExchange
from grid_server.infrastructure.sqlite_store import SQLiteStore
from grid_server.interfaces.api import create_app


def test_production_detection_does_not_include_demo() -> None:
    assert is_live_exchange(BinanceFuturesExchange("", ""))
    assert is_live_exchange(SnapshotExchange(OkxSwapExchange("", "", "")))
    assert not is_live_exchange(BinanceFuturesExchange("", "", "https://demo-fapi.binance.com"))
    assert not is_live_exchange(OkxSwapExchange("", "", "", simulated=True))


@pytest.mark.parametrize("entry_mode,upper_seed_count", [
    ("seeded", 0),
    ("price_triggered", 3),
])
def test_live_api_rejects_prebuy_before_exchange_calls(
    tmp_path, entry_mode: str, upper_seed_count: int,
) -> None:
    exchange = BinanceFuturesExchange("", "")
    client = TestClient(create_app(tmp_path / "live.sqlite3", lambda: exchange))
    payload = {
        "symbol": "SOLUSDT", "lower_price": "60", "upper_price": "80",
        "grid_count": 20, "grid_budget_usdt": "500",
        "fixed_entry_mode": entry_mode, "upper_seed_count": upper_seed_count,
    }
    for endpoint in ("/fixed-grids/preview", "/fixed-grids"):
        response = client.post(endpoint, json=payload)
        assert response.status_code == 400
        assert "实盘" in response.json()["detail"]


def test_live_api_rejects_upper_seed_change_and_engine_start(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "live.sqlite3"
    store = SQLiteStore(db_path)
    config = GridService(store).create_fixed(
        "SOLUSDT", Decimal("60"), Decimal("80"), 20,
        Decimal("500"), Decimal("0.01"),
        fixed_entry_mode=FixedEntryMode.PRICE_TRIGGERED,
    )
    exchange = BinanceFuturesExchange("", "")
    client = TestClient(create_app(db_path, lambda: exchange))
    response = client.post(f"/strategies/{config.strategy_id}/upper-seed", json={"count": 3})
    assert response.status_code == 400
    assert store.get_strategy(config.strategy_id).upper_seed_count == 0

    store.set_upper_seed_count(config.strategy_id, 3)
    monkeypatch.setenv("GRID_ENABLE_BINANCE_LIVE_FIXED_GRID", "1")
    with pytest.raises(ValueError, match="实盘"):
        FixedGridEngine(store, exchange, config.strategy_id).initialize()
    response = client.post(f"/strategies/{config.strategy_id}/start")
    assert response.status_code == 500
    assert "实盘" in response.json()["detail"]
