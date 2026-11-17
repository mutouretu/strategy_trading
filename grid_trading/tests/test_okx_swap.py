from __future__ import annotations

import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from grid_server.application.fixed_engine import FixedGridEngine
from grid_server.domain import ExchangeVenue, OrderSide, OrderStatus, SymbolFilters
from grid_server.infrastructure.okx import OkxSwapExchange
from grid_server.infrastructure.sqlite_store import SQLiteStore
from grid_server.interfaces.api import create_app
from tests.fakes import FakeExchange


class Response:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def json(self):
        if isinstance(self.data, dict) and "code" in self.data:
            return self.data
        return {"code": "0", "msg": "", "data": self.data}


class QueueSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return Response(self.responses.pop(0))


class OkxSwapTests(unittest.TestCase):
    def test_contract_units_and_order_status_are_normalized(self):
        instrument = {
            "instId": "ABC-USDT-SWAP", "state": "live", "ctType": "linear",
            "ctVal": "0.1", "ctValCcy": "ABC", "settleCcy": "USDT",
            "tickSz": "0.01", "lotSz": "0.01", "minSz": "0.01", "groupId": "4",
        }
        session = QueueSession([instrument], [{"sCode": "0", "ordId": "123"}], [{
            "ordId": "123", "clOrdId": "grid123", "state": "partially_filled",
            "side": "buy", "posSide": "long", "px": "10.00", "sz": "0.12",
            "accFillSz": "0.05", "avgPx": "10.00",
        }])
        exchange = OkxSwapExchange("key", "secret", "phrase", session=session)
        filters = exchange.get_symbol_filters("ABC-USDT-SWAP")
        self.assertEqual(filters.step_size, Decimal("0.001"))
        self.assertEqual(exchange.place_limit_order(
            "ABC-USDT-SWAP", OrderSide.BUY, "LONG", Decimal("0.012"),
            Decimal("10"), "grid123", post_only=True,
        ), 123)
        body = json.loads(session.calls[1][2]["data"])
        self.assertEqual((body["sz"], body["ordType"], body["posSide"]), ("0.12", "post_only", "long"))
        order = exchange.get_order("ABC-USDT-SWAP", 123)
        self.assertEqual(order.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual((order.original_qty, order.executed_qty), (Decimal("0.012"), Decimal("0.005")))

    def test_fee_group_and_fill_fee_sign(self):
        session = QueueSession([{
            "feeGroup": [{"groupId": "4", "maker": "-0.0002", "taker": "-0.0005"}],
        }], [{
            "tradeId": "123", "billId": "456", "ordId": "789", "side": "sell",
            "fillSz": "0.05", "fillPx": "11", "fillPnl": "-0.1",
            "fee": "-0.002", "feeCcy": "USDT", "fillTime": "1700000000000",
        }])
        exchange = OkxSwapExchange("key", "secret", "phrase", session=session)
        exchange._filters["ABC-USDT-SWAP"] = SymbolFilters(Decimal("0.01"), Decimal("0.001"), contract_size=Decimal("0.1"))
        exchange._fee_groups["ABC-USDT-SWAP"] = "4"
        self.assertEqual(exchange.get_commission_rates("ABC-USDT-SWAP"), (Decimal("0.0002"), Decimal("0.0005")))
        fills = exchange.get_user_trades("ABC-USDT-SWAP")
        self.assertEqual((fills[0]["qty"], fills[0]["commission"], fills[0]["realizedPnl"]), ("0.005", "0.002", "-0.1"))

    def test_grid_client_ids_are_okx_safe_and_recoverable(self):
        seed_id = "fg-b6a2b790df8f98d6ec"
        cell_id = "wg-e1a97c4b-0123abcd-e45a3f1"
        self.assertEqual(OkxSwapExchange._wire_client_id(seed_id), "fgb6a2b790df8f98d6ec")
        self.assertEqual(OkxSwapExchange._grid_client_id("fgb6a2b790df8f98d6ec"), seed_id)
        self.assertEqual(OkxSwapExchange._grid_client_id(OkxSwapExchange._wire_client_id(cell_id)), cell_id)
        session = QueueSession([{"sCode": "0", "ordId": "123"}], [{
            "ordId": "123", "clOrdId": "fgb6a2b790df8f98d6ec", "state": "filled",
            "side": "buy", "posSide": "long", "px": "", "sz": "0.12",
            "accFillSz": "0.12", "avgPx": "10.00",
        }])
        exchange = OkxSwapExchange("key", "secret", "phrase", session=session)
        exchange._filters["ABC-USDT-SWAP"] = SymbolFilters(Decimal("0.01"), Decimal("0.01"), contract_size=Decimal("1"))
        self.assertEqual(exchange.place_market_order("ABC-USDT-SWAP", OrderSide.BUY, "LONG", Decimal("0.12"), seed_id), 123)
        self.assertEqual(json.loads(session.calls[0][2]["data"])["clOrdId"], "fgb6a2b790df8f98d6ec")
        self.assertEqual(exchange.get_order_by_client_id("ABC-USDT-SWAP", seed_id).client_order_id, seed_id)
        self.assertIn("clOrdId=fgb6a2b790df8f98d6ec", session.calls[1][1])

    def test_order_rejection_reports_per_order_code(self):
        session = QueueSession({"code": "1", "msg": "All operations failed", "data": [{"sCode": "51000", "sMsg": "Invalid clOrdId"}]})
        exchange = OkxSwapExchange("key", "secret", "phrase", session=session)
        exchange._filters["ABC-USDT-SWAP"] = SymbolFilters(Decimal("0.01"), Decimal("0.01"), contract_size=Decimal("1"))
        with self.assertRaisesRegex(Exception, "OKX 51000: Invalid clOrdId"):
            exchange.place_market_order("ABC-USDT-SWAP", OrderSide.BUY, "LONG", Decimal("0.12"), "fg-123")

    def test_pending_orders_read_all_pages(self):
        def order(number):
            return {
                "instId": "ABC-USDT-SWAP", "ordId": str(number), "clOrdId": str(number),
                "state": "live", "side": "buy", "posSide": "long",
                "px": "10", "sz": "0.01", "accFillSz": "0", "avgPx": "",
            }

        session = QueueSession([order(number) for number in range(200, 100, -1)], [order(100)])
        exchange = OkxSwapExchange("key", "secret", "phrase", session=session)
        exchange._filters["ABC-USDT-SWAP"] = SymbolFilters(Decimal("0.01"), Decimal("0.01"), contract_size=Decimal("1"))
        grouped = exchange.get_open_orders_by_symbol({"ABC-USDT-SWAP"})
        self.assertEqual(len(grouped["ABC-USDT-SWAP"]), 101)
        self.assertIn("after=101", session.calls[1][1])

    def test_catchup_order_uses_okx_price_limits(self):
        exchange = OkxSwapExchange("key", "secret", "phrase")
        exchange.get_price_limits = lambda symbol: (Decimal("64.51"), Decimal("63.49"))
        exchange.get_mark_price = lambda symbol: Decimal("64")
        submitted = []
        exchange.place_limit_order = lambda *args, **kwargs: submitted.append((args, kwargs)) or 123
        events = []
        store = type("Store", (), {"append_event": lambda self, *args, **kwargs: events.append(args)})()
        engine = FixedGridEngine(store, exchange, "grid")
        engine.filters = SymbolFilters(Decimal("0.01"), Decimal("0.01"))
        engine._submit_limit_order("ABC-USDT-SWAP", OrderSide.BUY, "LONG", Decimal("1"), Decimal("65"), "buy")
        engine._submit_limit_order("ABC-USDT-SWAP", OrderSide.SELL, "LONG", Decimal("1"), Decimal("63"), "sell")
        self.assertEqual([item[0][4] for item in submitted], [Decimal("64.51"), Decimal("63.49")])
        self.assertTrue(all(item[1]["post_only"] is False for item in submitted))
        self.assertEqual(len(events), 2)

    def test_database_binding_and_okx_fixed_grid_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "okx.sqlite3"
            SQLiteStore(path, venue=ExchangeVenue.OKX)
            with self.assertRaisesRegex(ValueError, "bound to okx"):
                SQLiteStore(path)
            fake = FakeExchange(Decimal("65"))
            fake.filters = SymbolFilters(
                tick_size=Decimal("0.01"), step_size=Decimal("0.01"),
                min_qty=Decimal("0.01"), contract_size=Decimal("1"),
                contract_type="PERPETUAL",
            )
            with patch.dict(os.environ, {
                "GRID_EXCHANGE": "okx", "GRID_ENV_FILE": str(Path(temp) / "empty.env"),
                "GRID_RUNTIME_DIR": str(Path(temp) / "runtime"),
            }):
                client = TestClient(create_app(path, lambda market=None: fake))
                payload = {
                    "symbol": "SOXL-USDT-SWAP", "lower_price": "60",
                    "upper_price": "70", "grid_count": 2, "grid_budget_usdt": "100",
                }
                preview = client.post("/fixed-grids/preview", json=payload)
                self.assertEqual(preview.status_code, 200, preview.text)
                created = client.post("/fixed-grids", json=payload)
                self.assertEqual(created.status_code, 201, created.text)
                self.assertEqual(created.json()["venue"], "okx")
                self.assertEqual(client.get("/health").json()["venue"], "okx")
                self.assertEqual(client.post("/strategies/preview", json={}).status_code, 422)

                strategy_id = created.json()["strategy_id"]
                client.app.state.store.mark_started(strategy_id)
                change = {
                    "lower_price": "50", "upper_price": "80",
                    "grid_count": 5, "grid_budget_usdt": "500",
                }
                plan = client.post(
                    f"/strategies/{strategy_id}/reconfigurations/preview", json=change,
                )
                self.assertEqual(plan.status_code, 200, plan.text)
                self.assertEqual(plan.json()["per_cell_usdt"], "100.00000000")
                requested = client.post(
                    f"/strategies/{strategy_id}/reconfigurations",
                    json={**change, "request_id": "api-reconfigure-1"},
                )
                self.assertEqual(requested.status_code, 202, requested.text)
                self.assertEqual(requested.json()["status"], "requested")
                actions = client.get(f"/strategies/{strategy_id}/reconfigurations")
                self.assertEqual(actions.status_code, 200, actions.text)
                self.assertEqual(actions.json()[0]["id"], "api-reconfigure-1")


if __name__ == "__main__":
    unittest.main()
