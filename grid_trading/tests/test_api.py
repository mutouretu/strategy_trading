from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from grid_server.api import create_app
from grid_server.domain import CellStage
from tests.fakes import FakeExchange


class APITests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.exchange = FakeExchange(Decimal("105"))
        self.app = create_app(Path(self.tempdir.name) / "api.sqlite3", lambda: self.exchange)
        self.client = TestClient(self.app)
        self.payload = {
            "symbol": "BTCUSDT",
            "mode": "long",
            "anchor_price": "110",
            "grid_ratio": "0.10",
            "grid_count": 3,
            "order_usdt": "100",
            "leverage": 3,
            "poll_interval_sec": 50.0,
            "move_grid": True,
        }

    def tearDown(self):
        self.client.close()
        self.tempdir.cleanup()

    def test_preview_create_list_and_cells(self):
        preview = self.client.post("/strategies/preview", json=self.payload)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(len(preview.json()["cells"]), 3)
        self.assertEqual(preview.json()["cells"][-1]["sell_price"], "110")

        created = self.client.post("/strategies", json=self.payload)
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["poll_interval_sec"], 50.0)
        self.assertIsNotNone(created.json()["lower_price"])
        self.assertIsNotNone(created.json()["upper_price"])
        self.assertEqual(created.json()["entered"], 0)
        strategy_id = created.json()["strategy_id"]

        listed = self.client.get("/strategies").json()
        self.assertEqual([item["strategy_id"] for item in listed], [strategy_id])
        self.assertEqual(len(self.client.get(f"/strategies/{strategy_id}/cells").json()), 3)

    def test_health_reports_configuration_without_exposing_credentials(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertIsInstance(response.json()["binance_configured"], bool)
        self.assertNotIn("api_key", response.text.lower())
        self.assertNotIn("api_secret", response.text.lower())

    def test_same_symbol_can_have_multiple_strategy_groups(self):
        first = self.client.post("/strategies", json=self.payload).json()
        second = self.client.post("/strategies", json=self.payload).json()
        self.assertNotEqual(first["strategy_id"], second["strategy_id"])
        listed = self.client.get("/strategies").json()
        self.assertEqual([item["symbol"] for item in listed], ["BTCUSDT", "BTCUSDT"])

    def test_edit_rejected_after_configuration_has_been_dispatched(self):
        created = self.client.post("/strategies", json=self.payload).json()
        strategy_id = created["strategy_id"]
        self.app.state.store.mark_started(strategy_id)

        changed = dict(self.payload)
        changed["grid_count"] = 4
        response = self.client.put(f"/strategies/{strategy_id}", json=changed)
        self.assertEqual(response.status_code, 409)
        self.assertIn("immutable", response.json()["detail"])

    def test_manual_price_refresh(self):
        strategy_id = self.client.post("/strategies", json=self.payload).json()["strategy_id"]
        response = self.client.post(f"/strategies/{strategy_id}/refresh-price")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mark_price"], "105")

    def test_position_pool_endpoint_starts_empty(self):
        response = self.client.get("/position-pools")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_cells_include_recorded_entry_and_remaining_exit_quantities(self):
        strategy_id = self.client.post("/strategies", json=self.payload).json()["strategy_id"]
        cell = self.app.state.store.list_cells(strategy_id)[0]
        cell.stage = CellStage.PENDING_EXIT
        cell.entry_order_id = 101
        cell.exit_order_id = 202
        cell.open_qty = Decimal("0.800")
        cell.exit_executed_qty = Decimal("0.200")
        self.app.state.store.save_cell(cell)
        self.app.state.store.append_event(
            strategy_id,
            "ENTRY_FILLED",
            {"order_id": 101, "qty": "1.000"},
            cell.cell_id,
        )
        self.app.state.store.append_event(
            strategy_id,
            "EXIT_PLACED",
            {"order_id": 202, "qty": "1.000"},
            cell.cell_id,
        )

        payload = self.client.get(f"/strategies/{strategy_id}/cells").json()[0]

        self.assertEqual(payload["entry_qty"], "1.000")
        self.assertEqual(payload["exit_qty"], "0.800")

    def test_running_strategy_can_queue_and_list_boundary_cell_action(self):
        strategy_id = self.client.post("/strategies", json=self.payload).json()["strategy_id"]
        self.app.state.store.mark_started(strategy_id)

        queued = self.client.post(
            f"/strategies/{strategy_id}/cell-actions",
            json={"operation": "add", "boundary": "upper"},
        )
        self.assertEqual(queued.status_code, 202, queued.text)
        self.assertEqual(queued.json()["status"], "pending")
        self.assertEqual(queued.json()["boundary"], "upper")

        actions = self.client.get(f"/strategies/{strategy_id}/cell-actions")
        self.assertEqual(actions.status_code, 200)
        self.assertEqual(actions.json()[0]["id"], queued.json()["id"])

    def test_cell_action_is_rejected_for_draft_strategy(self):
        strategy_id = self.client.post("/strategies", json=self.payload).json()["strategy_id"]
        response = self.client.post(
            f"/strategies/{strategy_id}/cell-actions",
            json={"operation": "remove", "boundary": "lower"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("running strategy", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
