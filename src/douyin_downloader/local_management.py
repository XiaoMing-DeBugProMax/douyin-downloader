from __future__ import annotations

import httpx


class LocalManagementClient:
    def __init__(self, base_url: str, management_token: str, *, timeout: float) -> None:
        self._base_url = base_url
        self._management_token = management_token
        self._timeout = timeout

    def has_active_tasks(self) -> bool:
        response = self._request("GET", "/api/internal/tasks/active")
        return response.json().get("active") is True

    def pause_all(self) -> None:
        self._request("POST", "/api/internal/tasks/pause-all")

    def interrupt_all(self) -> None:
        self._request("POST", "/api/internal/tasks/interrupt-all")

    def _request(self, method: str, path: str) -> httpx.Response:
        response = httpx.request(
            method,
            f"{self._base_url}{path}",
            headers={"x-management-token": self._management_token},
            timeout=self._timeout,
            trust_env=False,
            follow_redirects=False,
        )
        response.raise_for_status()
        return response
