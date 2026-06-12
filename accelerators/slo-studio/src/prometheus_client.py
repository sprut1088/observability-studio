from __future__ import annotations

import requests
from typing import Any


class PrometheusClient:
    def __init__(self, url: str, timeout: int = 10):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def query(self, promql: str) -> Any:
        resp = requests.get(
            f"{self.url}/api/v1/query",
            params={"query": promql},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def rules(self) -> list[dict[str, Any]]:
        resp = requests.get(
            f"{self.url}/api/v1/rules",
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        groups = data.get("data", {}).get("groups", [])
        rules: list[dict[str, Any]] = []
        for group in groups:
            for rule in group.get("rules", []):
                rules.append(rule)
        return rules

    def alerts(self) -> list[dict[str, Any]]:
        resp = requests.get(
            f"{self.url}/api/v1/alerts",
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {}).get("alerts", [])

    def label_values(self, label: str) -> list[str]:
        resp = requests.get(
            f"{self.url}/api/v1/label/{label}/values",
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", [])