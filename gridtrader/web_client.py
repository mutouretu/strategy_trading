from __future__ import annotations

from typing import Any

import requests


class GridApiError(RuntimeError):
    pass


class GridApiClient:
    def __init__(
        self,
        base_url: str,
        *,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (2.0, 15.0),
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.timeout = timeout

    def health(self) -> dict:
        return self._request("GET", "/health")

    def list_strategies(self, *, include_archived: bool = False) -> list[dict]:
        return self._request(
            "GET",
            "/strategies",
            params={"include_archived": str(include_archived).lower()},
        )

    def preview_strategy(self, payload: dict) -> dict:
        return self._request("POST", "/strategies/preview", json=payload)

    def create_strategy(self, payload: dict) -> dict:
        return self._request("POST", "/strategies", json=payload)

    def update_strategy(self, strategy_id: str, payload: dict) -> dict:
        return self._request("PUT", f"/strategies/{strategy_id}", json=payload)

    def cells(self, strategy_id: str) -> list[dict]:
        return self._request("GET", f"/strategies/{strategy_id}/cells")

    def cell_actions(self, strategy_id: str) -> list[dict]:
        return self._request("GET", f"/strategies/{strategy_id}/cell-actions")

    def request_cell_action(self, strategy_id: str, operation: str, boundary: str) -> dict:
        return self._request(
            "POST",
            f"/strategies/{strategy_id}/cell-actions",
            json={"operation": operation, "boundary": boundary},
        )

    def start_strategy(self, strategy_id: str) -> dict:
        return self._request("POST", f"/strategies/{strategy_id}/start")

    def stop_strategy(self, strategy_id: str) -> dict:
        return self._request("POST", f"/strategies/{strategy_id}/stop")

    def refresh_price(self, strategy_id: str) -> dict:
        return self._request("POST", f"/strategies/{strategy_id}/refresh-price")

    def archive_strategy(self, strategy_id: str) -> dict:
        return self._request("POST", f"/strategies/{strategy_id}/archive")

    def delete_strategy(self, strategy_id: str) -> dict:
        return self._request("DELETE", f"/strategies/{strategy_id}")

    def _request(self, method: str, path: str, **kwargs: Any):
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                timeout=self.timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise GridApiError(f"无法连接后端服务：{self.base_url}") from exc
        if response.ok:
            return response.json()
        try:
            detail = response.json().get("detail")
        except (ValueError, AttributeError):
            detail = None
        message = str(detail or f"HTTP {response.status_code}")
        raise GridApiError(message)
