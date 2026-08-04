from __future__ import annotations

import json
import os
import socket
import time
import unittest
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit

from streamlit.testing.v1 import AppTest


APP_FILE = os.path.join(os.path.dirname(__file__), "..", "app.py")


def strategy_payload(
    strategy_id: str = "strategy-one",
    *,
    symbol: str = "BTCUSDT",
    mode: str = "long",
    status: str = "draft",
    has_started: bool = False,
) -> dict:
    return {
        "strategy_id": strategy_id,
        "symbol": symbol,
        "mode": mode,
        "current_price": "64000",
        "lower_price": "61000",
        "upper_price": "65000",
        "grid_ratio": "0.01",
        "order_usdt": "10",
        "leverage": 3,
        "grid_count": 5,
        "pending_entry": 5,
        "entered": 0,
        "pending_exit": 0,
        "manual_review": 0,
        "status": status,
        "heartbeat_at": None,
        "started_at": None,
        "stopped_at": None,
        "anchor_price": "65000",
        "poll_interval_sec": 50.0,
        "move_grid": True,
        "has_started": has_started,
        "archived": status == "archived",
        "last_error": None,
    }


def preview_payload(symbol: str, anchor: str = "100") -> dict:
    return {
        "config": {
            "symbol": symbol,
            "lower_price": "98.0296",
            "upper_price": anchor,
        },
        "cells": [
            {"index": index, "buy_price": str(100 - index), "sell_price": str(101 - index)}
            for index in range(1, 6)
        ],
    }


@dataclass
class StubState:
    strategies: object = field(default_factory=list)
    cells: object = field(default_factory=list)
    preview_responses: list[tuple[int, object]] = field(default_factory=list)
    status_overrides: dict[tuple[str, str], tuple[int, object]] = field(default_factory=dict)
    calls: list[tuple[str, str, object | None]] = field(default_factory=list)


@contextmanager
def stub_api(state: StubState):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, payload: object, *, content_type: str = "application/json") -> None:
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _handle(self) -> None:
            method = self.command
            path = urlsplit(self.path).path
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length) if length else b""
            request_json = json.loads(raw_body) if raw_body else None
            state.calls.append((method, path, request_json))

            override = state.status_overrides.get((method, path))
            if override is not None:
                self._reply(*override)
                return
            if method == "GET" and path == "/strategies":
                self._reply(200, state.strategies)
                return
            if method == "POST" and path == "/strategies/preview":
                status, payload = state.preview_responses.pop(0)
                self._reply(status, payload)
                return
            if method == "POST" and path == "/strategies":
                self._reply(201, {**request_json, "strategy_id": "created-one"})
                return
            if method == "GET" and path.endswith("/cells"):
                self._reply(200, state.cells)
                return
            if method == "GET" and path.endswith("/cell-actions"):
                self._reply(200, [])
                return
            if method == "POST" and path.endswith("/cell-actions"):
                self._reply(
                    202,
                    {
                        "id": 1,
                        "strategy_id": path.split("/")[2],
                        **request_json,
                        "status": "pending",
                    },
                )
                return
            if method == "POST" and path.endswith("/start"):
                self._reply(200, {"status": "running"})
                return
            if method == "POST" and path.endswith("/stop"):
                self._reply(200, {"status": "stopped"})
                return
            if method == "POST" and path.endswith("/refresh-price"):
                self._reply(200, {"status": "ok"})
                return
            self._reply(404, {"detail": "not found"})

        do_GET = _handle
        do_POST = _handle
        do_PUT = _handle
        do_DELETE = _handle

        def log_message(self, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    previous = os.environ.get("GRID_API_URL")
    os.environ["GRID_API_URL"] = f"http://127.0.0.1:{server.server_port}"
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if previous is None:
            os.environ.pop("GRID_API_URL", None)
        else:
            os.environ["GRID_API_URL"] = previous


def run_app(*, timeout: float = 8) -> AppTest:
    return AppTest.from_file(APP_FILE, default_timeout=timeout).run()


class StreamlitAbnormalTests(unittest.TestCase):
    def test_initial_backend_outage_has_recovery_message_without_traceback(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            unused_port = probe.getsockname()[1]
        previous = os.environ.get("GRID_API_URL")
        os.environ["GRID_API_URL"] = f"http://127.0.0.1:{unused_port}"
        try:
            app = run_app()
        finally:
            if previous is None:
                os.environ.pop("GRID_API_URL", None)
            else:
                os.environ["GRID_API_URL"] = previous

        self.assertEqual(list(app.exception), [])
        self.assertIn("无法连接后端服务", app.error[0].value)
        self.assertIn("请先启动 FastAPI", app.info[0].value)

    def test_detail_cells_failure_is_rendered_without_page_crash(self):
        item = strategy_payload()
        state = StubState(
            strategies=[item],
            status_overrides={
                ("GET", f"/strategies/{item['strategy_id']}/cells"): (503, {"detail": "cells unavailable"}),
            },
        )
        with stub_api(state):
            app = AppTest.from_file(APP_FILE, default_timeout=8)
            app.query_params["strategy"] = item["strategy_id"]
            app.run()

        self.assertEqual(list(app.exception), [])
        self.assertEqual([error.value for error in app.error], ["cells unavailable"])

    def test_detail_table_does_not_insert_current_price_row(self):
        item = strategy_payload()
        item["current_price"] = "0.0013333"
        item["lower_price"] = "0.0013054"
        item["upper_price"] = "0.0013583"
        cells = [
            {
                "index": 9,
                "buy_price": "0.0013054",
                "sell_price": "0.0013316",
                "stage": "pending_entry",
                "entry_order_id": 9,
                "exit_order_id": None,
                "open_qty": "0",
                "cycle_count": 0,
            },
            {
                "index": 10,
                "buy_price": "0.0013316",
                "sell_price": "0.0013583",
                "stage": "pending_exit",
                "entry_order_id": 10,
                "exit_order_id": 20,
                "open_qty": "1",
                "cycle_count": 0,
            },
        ]
        state = StubState(strategies=[item], cells=cells)
        with stub_api(state):
            app = AppTest.from_file(APP_FILE, default_timeout=8)
            app.query_params["strategy"] = item["strategy_id"]
            app.run()

        self.assertEqual(list(app.exception), [])
        self.assertEqual(list(app.dataframe[0].value["网格"]), ["#010", "#009"])

    def test_detail_order_columns_show_coin_quantities(self):
        item = strategy_payload(symbol="AKEUSDT")
        cells = [
            {
                "index": 4,
                "buy_price": "0.0018293",
                "sell_price": "0.0018475",
                "stage": "pending_entry",
                "entry_order_id": 101,
                "exit_order_id": None,
                "entry_qty": "5466",
                "exit_qty": None,
                "open_qty": "0",
                "cycle_count": 0,
            },
            {
                "index": 5,
                "buy_price": "0.0018475",
                "sell_price": "0.0018659",
                "stage": "pending_exit",
                "entry_order_id": 102,
                "exit_order_id": 202,
                "entry_qty": "5412",
                "exit_qty": "1818",
                "open_qty": "1818",
                "cycle_count": 7,
            },
        ]
        state = StubState(strategies=[item], cells=cells)
        with stub_api(state):
            app = AppTest.from_file(APP_FILE, default_timeout=8)
            app.query_params["strategy"] = item["strategy_id"]
            app.run()

        frame = app.dataframe[0].value
        self.assertEqual(
            list(frame["买入"]),
            ["成交:#102 · 5,412 AKE", "挂单:#101 · 5,466 AKE"],
        )
        self.assertEqual(list(frame["卖出"]), ["挂单:#202 · 1,818 AKE", ""])

    def test_short_detail_puts_entry_quantity_under_sell_and_exit_under_buy(self):
        item = strategy_payload(symbol="HOMEUSDT", mode="short")
        cells = [
            {
                "index": 1,
                "buy_price": "0.0064650",
                "sell_price": "0.0065950",
                "stage": "pending_exit",
                "entry_order_id": 301,
                "exit_order_id": 302,
                "entry_qty": "3032",
                "exit_qty": "3000",
                "open_qty": "3000",
                "cycle_count": 1,
            }
        ]
        state = StubState(strategies=[item], cells=cells)
        with stub_api(state):
            app = AppTest.from_file(APP_FILE, default_timeout=8)
            app.query_params["strategy"] = item["strategy_id"]
            app.run()

        frame = app.dataframe[0].value
        self.assertEqual(list(frame["买入"]), ["挂单:#302 · 3,000 HOME"])
        self.assertEqual(list(frame["卖出"]), ["成交:#301 · 3,032 HOME"])

    def test_detail_add_upper_cell_starts_from_page_and_queues_api_action(self):
        item = strategy_payload(status="running", has_started=True)
        cells = [
            {
                "index": index,
                "cell_id": f"cell-{index}",
                "buy_price": str(100 + index),
                "sell_price": str(101 + index),
                "stage": "pending_entry",
                "entry_order_id": index,
                "exit_order_id": None,
                "open_qty": "0",
                "cycle_count": 0,
            }
            for index in (1, 2)
        ]
        state = StubState(strategies=[item], cells=cells)
        action_path = f"/strategies/{item['strategy_id']}/cell-actions"
        with stub_api(state):
            app = AppTest.from_file(APP_FILE, default_timeout=8)
            app.query_params["strategy"] = item["strategy_id"]
            app.run()
            app.button(key=f"add_cell_{item['strategy_id']}_upper").click().run()

        self.assertEqual(list(app.exception), [])
        requests = [call for call in state.calls if call[:2] == ("POST", action_path)]
        self.assertEqual(requests, [("POST", action_path, {"operation": "add", "boundary": "upper"})])
        action_reads = [call for call in state.calls if call[:2] == ("GET", action_path)]
        self.assertGreaterEqual(len(action_reads), 2)
        self.assertNotIn(
            f"cell_action_pending_{item['strategy_id']}",
            app.session_state.filtered_state,
        )

    def test_overview_symbol_link_explicitly_reuses_current_tab(self):
        item = strategy_payload(symbol="AKEUSDT")
        state = StubState(strategies=[item])
        with stub_api(state):
            app = run_app()

        symbol_markup = [
            element.value for element in app.markdown
            if "AKEUSDT" in element.value and "href=" in element.value
        ]
        self.assertEqual(len(symbol_markup), 1)
        self.assertIn("target='_self'", symbol_markup[0])

    def test_refresh_failure_is_visible_and_does_not_mutate_strategy(self):
        item = strategy_payload()
        refresh_path = f"/strategies/{item['strategy_id']}/refresh-price"
        state = StubState(
            strategies=[item],
            status_overrides={("POST", refresh_path): (503, {"detail": "refresh unavailable"})},
        )
        with stub_api(state):
            app = run_app()
            app.button(key=f"refresh_{item['strategy_id']}").click().run()

        self.assertEqual(list(app.exception), [])
        self.assertIn("refresh unavailable", [error.value for error in app.error])
        self.assertEqual([call[1] for call in state.calls].count(refresh_path), 1)

    def test_start_failure_rolls_toggle_back_to_server_state(self):
        item = strategy_payload()
        start_path = f"/strategies/{item['strategy_id']}/start"
        state = StubState(
            strategies=[item],
            status_overrides={("POST", start_path): (503, {"detail": "start rejected"})},
        )
        with stub_api(state):
            app = run_app()
            app.toggle(key=f"run_{item['strategy_id']}").set_value(True).run()

        self.assertEqual(list(app.exception), [])
        self.assertFalse(app.toggle(key=f"run_{item['strategy_id']}").value)
        self.assertIn("start rejected", [error.value for error in app.error])
        self.assertEqual([call[1] for call in state.calls].count(start_path), 1)

    def test_unknown_strategy_query_falls_back_to_available_strategy(self):
        item = strategy_payload()
        state = StubState(strategies=[item], cells=[])
        with stub_api(state):
            app = AppTest.from_file(APP_FILE, default_timeout=8)
            app.query_params["strategy"] = "deleted-or-stale-id"
            app.run()

        self.assertEqual(list(app.exception), [])
        self.assertEqual(app.query_params.get("strategy"), [item["strategy_id"]])
        self.assertTrue(any(item["symbol"] in heading.value for heading in app.markdown))

    def test_fifty_strategy_overview_has_unique_widgets_and_completes(self):
        strategies = [
            strategy_payload(f"strategy-{index:02d}", symbol=f"T{index:02d}USDT")
            for index in range(50)
        ]
        state = StubState(strategies=strategies)
        started = time.monotonic()
        with stub_api(state):
            app = run_app(timeout=15)
        elapsed = time.monotonic() - started

        self.assertEqual(list(app.exception), [])
        self.assertEqual(len(app.toggle), 51)  # one filter plus fifty run-state toggles
        self.assertLess(elapsed, 15)

    def test_missing_strategy_field_currently_surfaces_frontend_exception(self):
        malformed = strategy_payload()
        malformed.pop("mode")
        state = StubState(strategies=[malformed])
        with stub_api(state):
            app = run_app()

        self.assertTrue(app.exception)
        self.assertIn("mode", str(app.exception[0].value))

    def test_invalid_json_success_response_currently_surfaces_frontend_exception(self):
        state = StubState(
            status_overrides={
                ("GET", "/strategies"): (200, b"<html>not json</html>"),
            }
        )
        with stub_api(state):
            app = run_app()

        self.assertTrue(app.exception)
        self.assertIn("Expecting value", str(app.exception[0].value))

    def test_reopened_create_dialog_exposes_stale_preview_as_confirmable(self):
        first_symbol = "SOLUSDT"
        old_payload = {
            "symbol": first_symbol,
            "mode": "long",
            "anchor_price": "100",
            "grid_ratio": "0.005",
            "grid_count": 5,
            "order_usdt": "10",
            "leverage": 3,
            "poll_interval_sec": 50.0,
            "move_grid": True,
        }
        state = StubState(strategies=[])
        with stub_api(state):
            app = run_app()
            # This is the exact state left behind when a previewed dialog is
            # dismissed without creating. A newly opened form is blank, but the
            # old preview and its confirmation action are still rendered.
            app.session_state["grid_create_preview"] = preview_payload(first_symbol)
            app.session_state["grid_create_payload"] = old_payload
            app.button[0].click().run()

        self.assertEqual(app.text_input[0].value, "")
        self.assertIsNone(app.number_input[0].value)
        self.assertTrue(any(button.label == "确认创建" for button in app.button))
        self.assertTrue(any("范围" in caption.value for caption in app.caption))
        self.assertEqual(app.session_state["grid_create_payload"]["symbol"], first_symbol)


if __name__ == "__main__":
    unittest.main()
