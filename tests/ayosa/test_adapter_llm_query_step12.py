"""Step 12(a) tests — Loki, Tempo, Grafana, Alertmanager, Datadog,
Dynatrace, and AppDynamics adapters now accept ``plan`` and prepend an
LLM-emitted query row when ``plan['active_tool_args']['query']`` is set.
"""

from __future__ import annotations

from typing import Any

import pytest

from accelerators.ayosa.adapters.alertmanager import AlertmanagerAdapter
from accelerators.ayosa.adapters.appdynamics import AppDynamicsAdapter
from accelerators.ayosa.adapters.datadog import DatadogAdapter
from accelerators.ayosa.adapters.dynatrace import DynatraceAdapter
from accelerators.ayosa.adapters.grafana import GrafanaAdapter
from accelerators.ayosa.adapters.loki import LokiAdapter
from accelerators.ayosa.adapters.tempo import TempoAdapter


class _FakeResp:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        return None


def _plan(query: str | None, *, reason: str = "r") -> dict:
    if query is None:
        return {"intent": "x"}
    return {"intent": "x", "active_tool_args": {"query": query, "reason": reason}}


# ──────────────────────────────────────────────────────────────────────── #
# Loki
# ──────────────────────────────────────────────────────────────────────── #
class TestLokiLLMQuery:
    def _patch(self, monkeypatch, recorder, *, payload=None):
        payload = payload if payload is not None else {"status": "success", "data": {"result": []}}

        def fake_get(url, headers=None, params=None, timeout=None):
            recorder.append({"url": url, "params": params or {}})
            return _FakeResp(payload)

        monkeypatch.setattr("accelerators.ayosa.adapters.loki.requests.get", fake_get)

    def test_llm_logql_query_executed_and_prepended(self, monkeypatch):
        calls: list[dict] = []
        self._patch(monkeypatch, calls)
        out = LokiAdapter(base_url="http://loki:3100").investigate(
            "payments", "15m", "errors",
            plan=_plan('{service_name="payments"} |= "5xx"', reason="5xx scan"),
        )
        assert "LLM-selected Loki query executed" in out[0]["finding"]
        assert out[0]["query"] == '{service_name="payments"} |= "5xx"'
        assert calls[0]["url"].endswith("/loki/api/v1/query_range")
        assert calls[0]["params"]["query"] == '{service_name="payments"} |= "5xx"'
        # Default error probe still ran second.
        assert len(out) > 1

    def test_no_llm_query_keeps_existing_behaviour(self, monkeypatch):
        calls: list[dict] = []
        self._patch(monkeypatch, calls)
        out = LokiAdapter(base_url="http://loki:3100").investigate(
            "payments", "15m", "x", plan=_plan(None),
        )
        assert all("LLM-selected Loki" not in r["finding"] for r in out)

    def test_failure_surfaces_error_row(self, monkeypatch):
        state = {"calls": 0}

        def fake_get(url, headers=None, params=None, timeout=None):
            state["calls"] += 1
            if state["calls"] == 1:
                raise RuntimeError("boom")
            return _FakeResp({"status": "success", "data": {"result": []}})

        monkeypatch.setattr("accelerators.ayosa.adapters.loki.requests.get", fake_get)
        out = LokiAdapter(base_url="http://loki:3100").investigate(
            "payments", "15m", "x", plan=_plan("{x=y}"),
        )
        assert out[0]["status"] == "error"
        assert "LLM-selected Loki query failed" in out[0]["finding"]


# ──────────────────────────────────────────────────────────────────────── #
# Tempo
# ──────────────────────────────────────────────────────────────────────── #
class TestTempoLLMQuery:
    def _patch(self, monkeypatch, recorder):
        def fake_get(url, headers=None, params=None, timeout=None):
            recorder.append({"url": url, "params": params or {}})
            return _FakeResp({"traces": []})

        monkeypatch.setattr("accelerators.ayosa.adapters.tempo.requests.get", fake_get)

    def test_llm_traceql_executed(self, monkeypatch):
        calls: list[dict] = []
        self._patch(monkeypatch, calls)
        out = TempoAdapter(base_url="http://tempo:3200").investigate(
            "payments", "1h", "slow",
            plan=_plan('{duration > 500ms}', reason="slow"),
        )
        assert "LLM-selected Tempo query executed" in out[0]["finding"]
        assert calls[0]["params"]["q"] == '{duration > 500ms}'
        assert len(out) > 1

    def test_llm_query_runs_even_without_service(self, monkeypatch):
        calls: list[dict] = []
        self._patch(monkeypatch, calls)
        out = TempoAdapter(base_url="http://tempo:3200").investigate(
            None, "1h", "slow", plan=_plan('{duration > 500ms}'),
        )
        # LLM row first; default returns not_implemented row next.
        assert "LLM-selected Tempo query executed" in out[0]["finding"]
        assert out[1]["status"] == "not_implemented"

    def test_no_llm_query_keeps_existing_behaviour(self, monkeypatch):
        calls: list[dict] = []
        self._patch(monkeypatch, calls)
        out = TempoAdapter(base_url="http://tempo:3200").investigate(
            "payments", "1h", "x", plan=_plan(None),
        )
        assert all("LLM-selected Tempo" not in r["finding"] for r in out)


# ──────────────────────────────────────────────────────────────────────── #
# Grafana
# ──────────────────────────────────────────────────────────────────────── #
class TestGrafanaLLMQuery:
    def _patch(self, monkeypatch, recorder):
        def fake_get(url, headers=None, params=None, timeout=None):
            recorder.append({"url": url, "params": params or {}})
            return _FakeResp([{"id": 1, "title": "Payments"}])

        monkeypatch.setattr("accelerators.ayosa.adapters.grafana.requests.get", fake_get)

    def test_llm_search_term_prepended(self, monkeypatch):
        calls: list[dict] = []
        self._patch(monkeypatch, calls)
        out = GrafanaAdapter(base_url="http://grafana:3000").investigate(
            "payments", "1h", "errors", plan=_plan("payments-overview"),
        )
        assert "LLM-selected Grafana query executed" in out[0]["finding"]
        assert calls[0]["params"]["query"] == "payments-overview"
        assert len(out) > 1

    def test_no_llm_query_keeps_existing_behaviour(self, monkeypatch):
        calls: list[dict] = []
        self._patch(monkeypatch, calls)
        out = GrafanaAdapter(base_url="http://grafana:3000").investigate(
            "payments", "1h", "x", plan=_plan(None),
        )
        assert all("LLM-selected Grafana" not in r["finding"] for r in out)


# ──────────────────────────────────────────────────────────────────────── #
# Alertmanager
# ──────────────────────────────────────────────────────────────────────── #
class TestAlertmanagerLLMQuery:
    def _patch(self, monkeypatch, recorder):
        def fake_get(url, params=None, timeout=None):
            recorder.append({"url": url, "params": params})
            return _FakeResp([])

        monkeypatch.setattr("accelerators.ayosa.adapters.alertmanager.requests.get", fake_get)

    def test_llm_matcher_sent_as_filter_param(self, monkeypatch):
        calls: list[dict] = []
        self._patch(monkeypatch, calls)
        out = AlertmanagerAdapter(base_url="http://am:9093").investigate(
            "payments", "15m", "x",
            plan=_plan('severity="critical"\nservice="payments"', reason="crits"),
        )
        assert "LLM-selected Alertmanager query executed" in out[0]["finding"]
        params = calls[0]["params"]
        # Repeated filter params for each matcher line.
        filter_values = [v for k, v in params if k == "filter"]
        assert 'severity="critical"' in filter_values
        assert 'service="payments"' in filter_values
        assert len(out) > 1

    def test_no_llm_query_keeps_existing_behaviour(self, monkeypatch):
        calls: list[dict] = []
        self._patch(monkeypatch, calls)
        out = AlertmanagerAdapter(base_url="http://am:9093").investigate(
            "payments", "15m", "x", plan=_plan(None),
        )
        assert all("LLM-selected Alertmanager" not in r["finding"] for r in out)


# ──────────────────────────────────────────────────────────────────────── #
# Datadog (not yet wired — LLM query surfaces as skipped row)
# ──────────────────────────────────────────────────────────────────────── #
class TestDatadogLLMQuery:
    def test_llm_query_surfaces_skipped_row(self):
        out = DatadogAdapter(base_url="http://dd", auth_token="t").investigate(
            "payments", "1h", "x", plan=_plan("avg:trace.servlet.request.errors{*}"),
        )
        assert out[0]["status"] == "skipped"
        assert "LLM-selected Datadog query skipped" in out[0]["finding"]
        # Existing not_implemented row remains.
        assert len(out) > 1
        assert out[1]["status"] == "not_implemented"

    def test_no_llm_query_keeps_existing_behaviour(self):
        out = DatadogAdapter(base_url="http://dd", auth_token="t").investigate(
            "payments", "1h", "x", plan=_plan(None),
        )
        assert all("LLM-selected Datadog" not in r["finding"] for r in out)


# ──────────────────────────────────────────────────────────────────────── #
# Dynatrace
# ──────────────────────────────────────────────────────────────────────── #
class TestDynatraceLLMQuery:
    def _patch(self, monkeypatch, recorder):
        def fake_get(url, headers=None, params=None, timeout=None):
            recorder.append({"url": url, "params": params or {}})
            return _FakeResp({"problems": []})

        monkeypatch.setattr("accelerators.ayosa.adapters.dynatrace.requests.get", fake_get)

    def test_llm_problem_selector_used(self, monkeypatch):
        calls: list[dict] = []
        self._patch(monkeypatch, calls)
        out = DynatraceAdapter(base_url="http://dt", auth_token="tok").investigate(
            "payments", "1h", "x",
            plan=_plan('status("OPEN"),severity("ERROR")', reason="open errs"),
        )
        assert "LLM-selected Dynatrace query executed" in out[0]["finding"]
        assert calls[0]["params"]["problemSelector"] == 'status("OPEN"),severity("ERROR")'
        assert len(out) > 1

    def test_llm_query_without_token_returns_skipped(self, monkeypatch):
        calls: list[dict] = []
        self._patch(monkeypatch, calls)
        out = DynatraceAdapter(base_url="http://dt", auth_token=None).investigate(
            "payments", "1h", "x", plan=_plan('status("OPEN")'),
        )
        assert out[0]["status"] == "skipped"
        # No GET issued for the LLM call.
        assert all("problemSelector" not in (c["params"] or {}) for c in calls)

    def test_no_llm_query_keeps_existing_behaviour(self, monkeypatch):
        calls: list[dict] = []
        self._patch(monkeypatch, calls)
        out = DynatraceAdapter(base_url="http://dt", auth_token="tok").investigate(
            "payments", "1h", "x", plan=_plan(None),
        )
        assert all("LLM-selected Dynatrace" not in r["finding"] for r in out)


# ──────────────────────────────────────────────────────────────────────── #
# AppDynamics (not yet wired — LLM query surfaces as skipped row)
# ──────────────────────────────────────────────────────────────────────── #
class TestAppDynamicsLLMQuery:
    def test_llm_query_surfaces_skipped_row(self):
        out = AppDynamicsAdapter(base_url="http://ad", auth_token="t").investigate(
            "payments", "1h", "x", plan=_plan("metric-path=Overall Application Performance"),
        )
        assert out[0]["status"] == "skipped"
        assert "LLM-selected AppDynamics query skipped" in out[0]["finding"]
        assert len(out) > 1
        assert out[1]["status"] == "not_implemented"

    def test_no_llm_query_keeps_existing_behaviour(self):
        out = AppDynamicsAdapter(base_url="http://ad", auth_token="t").investigate(
            "payments", "1h", "x", plan=_plan(None),
        )
        assert all("LLM-selected AppDynamics" not in r["finding"] for r in out)
