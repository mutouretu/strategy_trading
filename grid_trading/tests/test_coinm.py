from __future__ import annotations

import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient

from grid_server.api import create_app
from grid_server.binance import BinanceCoinMExchange
from grid_server.domain import (
    CellStage,
    FuturesMarket,
    Mode,
    StrategyConfig,
    SymbolFilters,
)
from grid_server.engine import TradingEngine
from grid_server.scheduler import StrategyScheduler
from grid_server.service import GridService
from grid_server.store import SQLiteStore
from tests.fakes import FakeExchange


class CoinMAdapterTests(unittest.TestCase):
    def test_coinm_mark_price_accepts_dapi_list_response(self):
        exchange = BinanceCoinMExchange("key", "secret")
        exchange._request = Mock(  # type: ignore[method-assign]
            return_value=[
                {"symbol": "ETHUSD_PERP", "markPrice": "3000"},
                {"symbol": "BTCUSD_PERP", "markPrice": "100000.5"},
            ]
        )

        self.assertEqual(
            exchange.get_mark_price("BTCUSD_PERP"),
            Decimal("100000.5"),
        )

    def test_dapi_filters_expose_contract_face_value_and_integer_step(self):
        exchange = BinanceCoinMExchange("key", "secret")
        exchange._request = Mock(  # type: ignore[method-assign]
            return_value={
                "symbols": [
                    {
                        "symbol": "BTCUSD_PERP",
                        "contractType": "PERPETUAL",
                        "contractSize": 100,
                        "baseAsset": "BTC",
                        "marginAsset": "BTC",
                        "filters": [
                            {"filterType": "PRICE_FILTER", "tickSize": "0.1"},
                            {
                                "filterType": "LOT_SIZE",
                                "stepSize": "1",
                                "minQty": "1",
                            },
                        ],
                    }
                ]
            }
        )

        filters = exchange.get_symbol_filters("BTCUSD_PERP")

        self.assertEqual(filters.contract_size, Decimal("100"))
        self.assertEqual(filters.step_size, Decimal("1"))
        self.assertEqual(filters.contract_type, "PERPETUAL")
        exchange._request.assert_called_once_with("GET", "/dapi/v1/exchangeInfo")

    def test_coinm_positions_use_dapi_contract_counts(self):
        exchange = BinanceCoinMExchange("key", "secret")
        exchange._request = Mock(  # type: ignore[method-assign]
            return_value=[
                {
                    "symbol": "BTCUSD_PERP",
                    "positionSide": "LONG",
                    "positionAmt": "-3",
                }
            ]
        )

        positions = exchange.get_positions()

        self.assertEqual(positions[0].quantity, Decimal("3"))
        exchange._request.assert_called_once_with(
            "GET", "/dapi/v1/positionRisk", signed=True
        )


class CoinMEngineTests(unittest.TestCase):
    def test_coin_quantity_is_converted_to_nearest_contract_count(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "coinm.sqlite3")
            config = StrategyConfig(
                strategy_id="btc-coinm-long",
                symbol="BTCUSD_PERP",
                market_type=FuturesMarket.COINM,
                mode=Mode.LONG,
                anchor_price=Decimal("110000"),
                grid_ratio=Decimal("0.10"),
                grid_count=1,
                order_usdt=Decimal("0"),
                order_coin_qty=Decimal("0.0021"),
                contract_size=Decimal("100"),
                move_grid=False,
            )
            store.create_strategy(config)
            exchange = FakeExchange(Decimal("105000"))
            exchange.market_type = FuturesMarket.COINM
            exchange.filters = SymbolFilters(
                tick_size=Decimal("0.1"),
                step_size=Decimal("1"),
                min_qty=Decimal("1"),
                contract_size=Decimal("100"),
                base_asset="BTC",
                margin_asset="BTC",
                contract_type="PERPETUAL",
            )

            engine = TradingEngine(store, exchange, config.strategy_id)
            engine.tick()

            cell = store.list_cells(config.strategy_id)[0]
            self.assertEqual(cell.stage, CellStage.PENDING_ENTRY)
            self.assertEqual(exchange.placed[0]["quantity"], Decimal("2"))

    def test_delivery_contract_is_rejected_before_any_order(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "delivery.sqlite3")
            config = StrategyConfig(
                strategy_id="btc-delivery",
                symbol="BTCUSD_260925",
                market_type=FuturesMarket.COINM,
                mode=Mode.LONG,
                anchor_price=Decimal("110000"),
                grid_ratio=Decimal("0.10"),
                grid_count=1,
                order_usdt=Decimal("0"),
                order_coin_qty=Decimal("0.001"),
                contract_size=Decimal("100"),
            )
            store.create_strategy(config)
            exchange = FakeExchange(Decimal("105000"))
            exchange.filters = SymbolFilters(
                tick_size=Decimal("0.1"),
                step_size=Decimal("1"),
                min_qty=Decimal("1"),
                contract_size=Decimal("100"),
                contract_type="CURRENT_QUARTER",
            )

            with self.assertRaisesRegex(ValueError, "PERPETUAL"):
                TradingEngine(store, exchange, config.strategy_id).initialize()
            self.assertEqual(exchange.placed, [])


class CoinMPersistenceAndSchedulerTests(unittest.TestCase):
    def test_position_pool_key_separates_usdm_and_coinm(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "pools.sqlite3")
            values = [Decimal("1")] * 5
            store.save_position_pool(
                "BTCUSD",
                "LONG",
                *values,
                "consistent",
                FuturesMarket.USDM,
            )
            store.save_position_pool(
                "BTCUSD",
                "LONG",
                *values,
                "consistent",
                FuturesMarket.COINM,
            )

            pools = store.list_position_pools()
            self.assertEqual(
                {pool["market_type"] for pool in pools},
                {"usdm", "coinm"},
            )

    def test_legacy_pool_rows_migrate_to_usdm(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE position_pools (
                    symbol TEXT NOT NULL,
                    position_side TEXT NOT NULL,
                    actual_qty TEXT NOT NULL,
                    logical_qty TEXT NOT NULL,
                    external_reserved_qty TEXT NOT NULL,
                    unassigned_qty TEXT NOT NULL,
                    shortage_qty TEXT NOT NULL,
                    status TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    PRIMARY KEY(symbol, position_side)
                );
                INSERT INTO position_pools VALUES(
                    'BTCUSDT', 'LONG', '1', '1', '0', '0', '0',
                    'consistent', '2026-07-21T00:00:00+00:00'
                );
                """
            )
            connection.commit()
            connection.close()

            store = SQLiteStore(path)

            self.assertEqual(store.list_position_pools()[0]["market_type"], "usdm")

    def test_mixed_market_scheduler_uses_independent_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteStore(Path(directory) / "mixed.sqlite3")
            service = GridService(store, Mock())
            usdm = FakeExchange(Decimal("105"))
            coinm = FakeExchange(Decimal("105000"))
            coinm.market_type = FuturesMarket.COINM
            coinm.filters = SymbolFilters(
                tick_size=Decimal("0.1"),
                step_size=Decimal("1"),
                min_qty=Decimal("1"),
                contract_size=Decimal("100"),
                contract_type="PERPETUAL",
            )
            u = service.create(
                "BTCUSDT", Mode.LONG, Decimal("110"), Decimal("0.10"), 1,
                Decimal("100"), 3, Decimal("0.01"), move_grid=False,
            )
            c = service.create(
                "BTCUSD_PERP", Mode.LONG, Decimal("110000"), Decimal("0.10"), 1,
                Decimal("0"), 3, Decimal("0.1"), move_grid=False,
                market_type=FuturesMarket.COINM,
                order_coin_qty=Decimal("0.001"),
                contract_size=Decimal("100"),
            )
            store.mark_started(u.strategy_id)
            store.mark_started(c.strategy_id)

            scheduler = StrategyScheduler(
                store,
                {
                    FuturesMarket.USDM: usdm,
                    FuturesMarket.COINM: coinm,
                },
            )
            self.assertEqual(scheduler.run_once(now=0), 2)
            self.assertEqual(len(usdm.placed), 1)
            self.assertEqual(len(coinm.placed), 1)
            self.assertEqual(coinm.placed[0]["quantity"], Decimal("1"))


class CoinMApiTests(unittest.TestCase):
    def test_preview_and_create_expose_base_coin_quantity(self):
        with tempfile.TemporaryDirectory() as directory:
            usdm = FakeExchange(Decimal("105"))
            coinm = FakeExchange(Decimal("105000"))
            coinm.market_type = FuturesMarket.COINM
            coinm.filters = SymbolFilters(
                tick_size=Decimal("0.1"),
                step_size=Decimal("1"),
                min_qty=Decimal("1"),
                contract_size=Decimal("100"),
                contract_type="PERPETUAL",
            )
            exchanges = {
                FuturesMarket.USDM: usdm,
                FuturesMarket.COINM: coinm,
            }
            app = create_app(
                Path(directory) / "api.sqlite3",
                lambda market: exchanges[FuturesMarket(market)],
            )
            with TestClient(app) as client:
                payload = {
                    "symbol": "BTCUSD_PERP",
                    "market_type": "coinm",
                    "mode": "long",
                    "anchor_price": "110000",
                    "grid_ratio": "0.02",
                    "grid_count": 2,
                    "order_coin_qty": "0.001",
                    "leverage": 3,
                    "poll_interval_sec": 50,
                    "move_grid": True,
                }
                preview = client.post("/strategies/preview", json=payload)
                created = client.post("/strategies", json=payload)

            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(created.status_code, 201, created.text)
            self.assertEqual(created.json()["market_type"], "coinm")
            self.assertEqual(created.json()["quantity_unit"], "base_asset")
            self.assertEqual(created.json()["order_amount"], "0.001")
            self.assertEqual(created.json()["order_unit"], "BTC")
            self.assertEqual(created.json()["contract_size"], "100")

    def test_cells_convert_internal_contracts_to_coin_amounts(self):
        with tempfile.TemporaryDirectory() as directory:
            coinm = FakeExchange(Decimal("105000"))
            coinm.market_type = FuturesMarket.COINM
            coinm.filters = SymbolFilters(
                tick_size=Decimal("0.1"),
                step_size=Decimal("1"),
                min_qty=Decimal("1"),
                contract_size=Decimal("100"),
                base_asset="BTC",
                margin_asset="BTC",
                contract_type="PERPETUAL",
            )
            app = create_app(
                Path(directory) / "api.sqlite3",
                lambda _market: coinm,
            )
            with TestClient(app) as client:
                created = client.post(
                    "/strategies",
                    json={
                        "symbol": "BTCUSD_PERP",
                        "market_type": "coinm",
                        "mode": "long",
                        "anchor_price": "110000",
                        "grid_ratio": "0.10",
                        "grid_count": 1,
                        "order_coin_qty": "0.001",
                        "leverage": 3,
                    },
                ).json()
                cell = app.state.store.list_cells(created["strategy_id"])[0]
                cell.stage = CellStage.PENDING_EXIT
                cell.entry_order_id = 101
                cell.exit_order_id = 202
                cell.open_qty = Decimal("1")
                app.state.store.save_cell(cell)
                app.state.store.append_event(
                    created["strategy_id"], "ENTRY_FILLED", {"order_id": 101, "qty": "1"}, cell.cell_id
                )
                app.state.store.append_event(
                    created["strategy_id"], "EXIT_PLACED", {"order_id": 202, "qty": "1"}, cell.cell_id
                )
                payload = client.get(
                    f"/strategies/{created['strategy_id']}/cells"
                ).json()[0]

            self.assertEqual(Decimal(payload["entry_qty"]), Decimal("0.001"))
            self.assertEqual(
                Decimal(payload["exit_qty"]),
                Decimal("100") / Decimal("110000"),
            )
            self.assertEqual(payload["entry_contracts"], "1")
            self.assertEqual(payload["exit_contracts"], "1")

    def test_coinm_rejects_legacy_usd_amount_without_coin_quantity(self):
        with tempfile.TemporaryDirectory() as directory:
            coinm = FakeExchange(Decimal("105000"))
            coinm.filters = SymbolFilters(
                tick_size=Decimal("0.1"),
                step_size=Decimal("1"),
                min_qty=Decimal("1"),
                contract_size=Decimal("100"),
                contract_type="PERPETUAL",
            )
            app = create_app(Path(directory) / "api.sqlite3", lambda _market: coinm)
            with TestClient(app) as client:
                response = client.post(
                    "/strategies/preview",
                    json={
                        "symbol": "BTCUSD_PERP",
                        "market_type": "coinm",
                        "mode": "long",
                        "anchor_price": "110000",
                        "grid_ratio": "0.02",
                        "grid_count": 1,
                        "order_usdt": "100",
                        "leverage": 3,
                    },
                )

            self.assertEqual(response.status_code, 400)
            self.assertIn("单格币数量", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
