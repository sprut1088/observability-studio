from __future__ import annotations

from typing import Any

import requests


class JaegerClient:
    def __init__(self, url: str, timeout: int = 15):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def services(self) -> list[str]:
        resp = requests.get(f"{self.url}/api/services", timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", []) or []

    def operations(self, service: str) -> list[str]:
        resp = requests.get(
            f"{self.url}/api/operations",
            params={"service": service},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        ops = data.get("data", []) or []
        values: list[str] = []
        for item in ops:
            if isinstance(item, str):
                values.append(item)
            elif isinstance(item, dict):
                name = item.get("name") or item.get("operationName")
                if name:
                    values.append(name)
        return sorted(set(values))

    def traces(self, service: str, limit: int = 50) -> list[dict[str, Any]]:
        resp = requests.get(
            f"{self.url}/api/traces",
            params={"service": service, "limit": limit},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", []) or []