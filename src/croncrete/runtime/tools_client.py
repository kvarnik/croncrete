"""Scoped HTTP tools client. Anything outside SCOPES raises ScopeError."""

from __future__ import annotations

from typing import Any

import httpx


class ScopeError(RuntimeError):
    pass


class ToolsClient:
    def __init__(self, base_url: str, scopes: list[str]) -> None:
        self.base_url = base_url.rstrip("/")
        self.scopes = list(scopes)

    def call(self, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        if endpoint not in self.scopes:
            raise ScopeError(f"{endpoint} not in scopes {self.scopes}")
        method, _, path = endpoint.partition(" ")
        if method == "GET":
            r = httpx.get(f"{self.base_url}{path}", params=kwargs, timeout=10)
        elif method == "POST":
            r = httpx.post(f"{self.base_url}{path}", json=kwargs, timeout=10)
        else:
            raise ScopeError(f"unsupported method in {endpoint}")
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise ScopeError(f"{endpoint} returned non-object")
        return data
