from __future__ import annotations

import time
from typing import Any

import requests


class PrometheusClient:
    def __init__(self, url: str, timeout: int = 12):
        self.url = url.rstrip("/")
        self.timeout = timeout

    def query(self, promql: str) -> dict[str, Any]:
        resp = requests.get(
            f"{self.url}/api/v1/query",
            params={"query": promql},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def query_range(self, promql: str, start: int, end: int, step: str = "5m") -> dict[str, Any]:
        resp = requests.get(
            f"{self.url}/api/v1/query_range",
            params={"query": promql, "start": start, "end": end, "step": step},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()
    
    def series(self, matchers: list[str], start: int, end: int) -> dict[str, Any]:
        params: list[tuple[str, str | int]] = [
            ("start", start),
            ("end", end),
        ]

        for matcher in matchers:
            params.append(("match[]", matcher))

        resp = requests.get(
            f"{self.url}/api/v1/series",
            params=params,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def rules(self) -> list[dict[str, Any]]:
        resp = requests.get(f"{self.url}/api/v1/rules", timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        groups = data.get("data", {}).get("groups", [])
        rules: list[dict[str, Any]] = []
        for group in groups:
            for rule in group.get("rules", []):
                rules.append(rule)
        return rules

    def alerts(self) -> list[dict[str, Any]]:
        resp = requests.get(f"{self.url}/api/v1/alerts", timeout=self.timeout)
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

    def range_window(self, lookback_days: int) -> tuple[int, int]:
        end = int(time.time())
        start = end - int(lookback_days * 24 * 60 * 60)
        return start, end