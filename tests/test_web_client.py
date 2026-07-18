from __future__ import annotations

import unittest

import requests

from gridtrader.web_client import GridApiClient, GridApiError


class FakeResponse:
    def __init__(self, status_code: int, payload) -> None:
        self.status_code = status_code
        self.payload = payload
        self.ok = 200 <= status_code < 400

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse] | None = None, error: Exception | None = None) -> None:
        self.responses = list(responses or [])
        self.error = error
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error:
            raise self.error
        return self.responses.pop(0)


class WebClientTests(unittest.TestCase):
    def test_strategy_routes_and_timeout_are_forwarded(self):
        session = FakeSession([
            FakeResponse(200, [{"strategy_id": "one"}]),
            FakeResponse(201, {"strategy_id": "two"}),
            FakeResponse(200, {"status": "stopped"}),
            FakeResponse(202, {"id": 7, "status": "pending"}),
        ])
        client = GridApiClient("http://127.0.0.1:8100/", session=session)

        self.assertEqual(client.list_strategies(), [{"strategy_id": "one"}])
        self.assertEqual(client.create_strategy({"symbol": "BTCUSDT"})["strategy_id"], "two")
        self.assertEqual(client.stop_strategy("two")["status"], "stopped")
        self.assertEqual(client.request_cell_action("two", "add", "upper")["id"], 7)

        self.assertEqual(session.calls[0][0:2], ("GET", "http://127.0.0.1:8100/strategies"))
        self.assertEqual(session.calls[1][0:2], ("POST", "http://127.0.0.1:8100/strategies"))
        self.assertEqual(session.calls[2][0:2], ("POST", "http://127.0.0.1:8100/strategies/two/stop"))
        self.assertEqual(session.calls[3][0:2], ("POST", "http://127.0.0.1:8100/strategies/two/cell-actions"))
        self.assertEqual(session.calls[3][2]["json"], {"operation": "add", "boundary": "upper"})
        self.assertEqual(session.calls[0][2]["timeout"], (2.0, 15.0))

    def test_api_error_uses_backend_detail(self):
        session = FakeSession([FakeResponse(409, {"detail": "configuration is immutable"})])
        client = GridApiClient("http://api", session=session)
        with self.assertRaisesRegex(GridApiError, "configuration is immutable"):
            client.update_strategy("one", {})

    def test_connection_error_does_not_echo_transport_details(self):
        session = FakeSession(error=requests.ConnectionError("secret-bearing transport message"))
        client = GridApiClient("http://api", session=session)
        with self.assertRaises(GridApiError) as raised:
            client.health()
        self.assertEqual(str(raised.exception), "无法连接后端服务：http://api")


if __name__ == "__main__":
    unittest.main()
