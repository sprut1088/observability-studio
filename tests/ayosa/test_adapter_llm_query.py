"""Step 10 tests — plan-accepting adapters consume the LLM-chosen
``query`` carried under ``plan['active_tool_args']``.

Adapters covered:

* ``PrometheusAdapter`` — executes the LLM PromQL as an additional
  evidence row PRE-pended to the intent-driven probes (additive, never
  reduces coverage).
* ``SplunkAdapter``     — replaces the heuristic SPL with the LLM one
  when present; keeps the heuristic when no LLM query was emitted.

Each test patches ``requests.get`` / ``requests.post`` so no real HTTP
calls are made.
"""

from __future__ import annotations

from typing import Any

import pytest

from accelerators.ayosa.adapters.prometheus import PrometheusAdapter
from accelerators.ayosa.adapters.splunk import SplunkAdapter


# ──────────────────────────────────────────────────────────────────────── #
# Tiny fake response object — mimics the .json() / raise_for_status()
# surface that the adapters use.
# ──────────────────────────────────────────────────────────────────────── #
class _FakeResp:
    def __init__(self, payload: Any, *, text: str | None = None) -> None:
        self._payload = payload
        self.text = text if text is not None else ""

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        return None


# ──────────────────────────────────────────────────────────────────────── #
# PrometheusAdapter
# ──────────────────────────────────────────────────────────────────────── #
class TestPrometheusLLMQuery:
    def _patch_requests(self, monkeypatch, recorder):
        def fake_get(url, headers=None, params=None, timeout=None):
            recorder.append({"url": url, "params": params})
            return _FakeResp({
                "data": {
                    "result": [{"metric": {}, "value": [0, "1"]}],
                }
            })
        monkeypatch.setattr(
            "accelerators.ayosa.adapters.prometheus.requests.get",
            fake_get,
        )

    def test_llm_query_prepended_to_evidence(self, monkeypatch):
        calls: list[dict] = []
        self._patch_requests(monkeypatch, calls)
        adapter = PrometheusAdapter(base_url="http://prom")
        plan = {
            "intent": "service_health",
            "active_tool_args": {
                "query": 'sum(rate(http_requests_total{job="api"}[5m]))',
                "reason": "rps for api service",
            },
        }
        out = adapter.investigate(
            service="api", time_range="30m", message="how is api?", plan=plan,
        )
        # First evidence row is the LLM-selected query.
        assert out, "expected non-empty evidence"
        first = out[0]
        assert first["source"] == "prometheus"
        assert first["query"] == 'sum(rate(http_requests_total{job="api"}[5m]))'
        assert "LLM-selected PromQL" in first["finding"]
        assert "rps for api service" in first["finding"]
        # Intent-driven probes still ran below.
        assert len(out) > 1
        # The first issued HTTP call carried the LLM query.
        assert calls and calls[0]["params"]["query"].startswith("sum(rate(")

    def test_no_llm_query_keeps_existing_behaviour(self, monkeypatch):
        calls: list[dict] = []
        self._patch_requests(monkeypatch, calls)
        adapter = PrometheusAdapter(base_url="http://prom")
        out = adapter.investigate(
            service="api", time_range="30m", message="how is api?", plan={"intent": "service_health"},
        )
        # No LLM query → no "LLM-selected" rows.
        assert all("LLM-selected" not in r["finding"] for r in out)

    def test_blank_llm_query_ignored(self, monkeypatch):
        calls: list[dict] = []
        self._patch_requests(monkeypatch, calls)
        adapter = PrometheusAdapter(base_url="http://prom")
        plan = {
            "intent": "service_health",
            "active_tool_args": {"query": "   ", "reason": "blank"},
        }
        out = adapter.investigate(
            service="api", time_range="30m", message="x", plan=plan,
        )
        assert all("LLM-selected" not in r["finding"] for r in out)

    def test_llm_query_failure_surfaces_error_row(self, monkeypatch):
        def fake_get(url, headers=None, params=None, timeout=None):
            # Fail only the first (LLM) call; succeed the rest.
            if params and params.get("query", "").startswith("sum(rate(http_requests_total"):
                raise RuntimeError("boom")
            return _FakeResp({"data": {"result": []}})
        monkeypatch.setattr(
            "accelerators.ayosa.adapters.prometheus.requests.get",
            fake_get,
        )
        adapter = PrometheusAdapter(base_url="http://prom")
        plan = {
            "intent": "service_health",
            "active_tool_args": {
                "query": "sum(rate(http_requests_total[5m]))",
                "reason": "rps",
            },
        }
        out = adapter.investigate(
            service="api", time_range="30m", message="x", plan=plan,
        )
        first = out[0]
        assert first["status"] == "error"
        assert "LLM-selected PromQL failed" in first["finding"]
        # Adapter did not crash — subsequent intent probes still appended.
        assert len(out) > 1

    def test_stability_ranking_still_runs_with_llm_query(self, monkeypatch):
        """When intent is service_stability_ranking the adapter takes a
        specialised return path. The LLM query must still be honoured
        and prepended to the stability table."""
        # First call (LLM) returns data; stability calls return empty so
        # we exercise the prepend without depending on stability output.
        seen: list[dict] = []

        def fake_get(url, headers=None, params=None, timeout=None):
            seen.append(params or {})
            return _FakeResp({"data": {"result": []}})
        monkeypatch.setattr(
            "accelerators.ayosa.adapters.prometheus.requests.get",
            fake_get,
        )
        adapter = PrometheusAdapter(base_url="http://prom")
        plan = {
            "intent": "service_stability_ranking",
            "threshold_percent": 1.0,
            "active_tool_args": {
                "query": "up == 0",
                "reason": "down targets only",
            },
        }
        out = adapter.investigate(
            service=None, time_range="24h", message="x", plan=plan,
        )
        # First row is the LLM-selected query result.
        assert out[0]["query"] == "up == 0"


# ──────────────────────────────────────────────────────────────────────── #
# SplunkAdapter
# ──────────────────────────────────────────────────────────────────────── #
class TestSplunkLLMQuery:
    def test_llm_query_replaces_heuristic_search(self, monkeypatch):
        captured: dict[str, Any] = {}

        def fake_post(url, headers=None, data=None, verify=False, timeout=None):
            captured["data"] = dict(data or {})
            return _FakeResp(None, text='{"result": {"_raw": "log line"}}\n')

        monkeypatch.setattr(
            "accelerators.ayosa.adapters.splunk.requests.post",
            fake_post,
        )
        adapter = SplunkAdapter(base_url="https://splunk:8089", auth_token="t")
        plan = {
            "intent": "error_investigation",
            "active_tool_args": {
                "query": 'search index=app status>=500 | head 20',
                "reason": "5xx scan",
            },
        }
        out = adapter.investigate(
            service="api", time_range="15m", message="errors",
            plan=plan,
        )
        # Splunk got the LLM SPL verbatim, not the heuristic.
        assert captured["data"]["search"] == 'search index=app status>=500 | head 20'
        # Evidence echoes the LLM query string.
        assert out and out[0]["query"] == 'search index=app status>=500 | head 20'

    def test_no_llm_query_falls_back_to_heuristic(self, monkeypatch):
        captured: dict[str, Any] = {}

        def fake_post(url, headers=None, data=None, verify=False, timeout=None):
            captured["data"] = dict(data or {})
            return _FakeResp(None, text="")

        monkeypatch.setattr(
            "accelerators.ayosa.adapters.splunk.requests.post",
            fake_post,
        )
        adapter = SplunkAdapter(base_url="https://splunk:8089", auth_token="t")
        out = adapter.investigate(
            service="api", time_range="15m", message="errors on api",
            plan={"intent": "error_investigation"},
        )
        # Heuristic SPL builder produced a non-LLM search string.
        spl = captured.get("data", {}).get("search", "")
        assert spl and "search index=" in spl
        # Sanity: the LLM marker is absent.
        assert "| head 20" not in spl

    def test_blank_llm_query_ignored(self, monkeypatch):
        captured: dict[str, Any] = {}

        def fake_post(url, headers=None, data=None, verify=False, timeout=None):
            captured["data"] = dict(data or {})
            return _FakeResp(None, text="")

        monkeypatch.setattr(
            "accelerators.ayosa.adapters.splunk.requests.post",
            fake_post,
        )
        adapter = SplunkAdapter(base_url="https://splunk:8089", auth_token="t")
        adapter.investigate(
            service="api", time_range="15m", message="errors",
            plan={
                "intent": "error_investigation",
                "active_tool_args": {"query": "   ", "reason": "blank"},
            },
        )
        spl = captured.get("data", {}).get("search", "")
        # Fell back to heuristic.
        assert "search index=" in spl
