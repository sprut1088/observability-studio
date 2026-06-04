"""Step 11 tests — Elasticsearch and Jaeger adapters consume the
LLM-emitted ``query`` carried under ``plan['active_tool_args']``.

Per-adapter contract:
* Elasticsearch — JSON-object query → used as the request body;
  otherwise wrapped in a Lucene ``query_string`` query. Additive to
  the existing intent-driven probes.
* Jaeger — JSON-object query → merged as ``/api/traces`` params;
  otherwise used as the ``operation`` parameter (scoped to ``service``
  when provided). Additive.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from accelerators.ayosa.adapters.elasticsearch import ElasticsearchAdapter
from accelerators.ayosa.adapters.jaeger import JaegerAdapter


class _FakeResp:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        return None


# ──────────────────────────────────────────────────────────────────────── #
# Elasticsearch
# ──────────────────────────────────────────────────────────────────────── #
class TestElasticsearchLLMQuery:
    def _patch_post(self, monkeypatch, recorder, *, payload=None):
        if payload is None:
            payload = {"hits": {"hits": [{"_source": {"message": "x"}}]}}

        def fake_post(url, headers=None, json=None, verify=False, timeout=None):
            recorder.append({"url": url, "body": json})
            return _FakeResp(payload)

        monkeypatch.setattr(
            "accelerators.ayosa.adapters.elasticsearch.requests.post",
            fake_post,
        )

    def test_lucene_string_wrapped_in_query_string(self, monkeypatch):
        calls: list[dict] = []
        self._patch_post(monkeypatch, calls)
        adapter = ElasticsearchAdapter(base_url="http://es:9200")
        plan = {
            "intent": "latest_error",
            "active_tool_args": {
                "query": 'status:error AND service:payments',
                "reason": "narrow scan",
            },
        }
        out = adapter.investigate(
            service="payments", time_range="15m", message="errors", plan=plan,
        )
        # First evidence row is the LLM-selected query.
        assert out and "LLM-selected ES query executed" in out[0]["finding"]
        assert out[0]["query"] == 'status:error AND service:payments'
        # The first POST body wraps the Lucene string in a query_string clause.
        first_body = calls[0]["body"]
        must = first_body["query"]["bool"]["must"]
        assert any(
            "query_string" in clause and
            clause["query_string"]["query"] == 'status:error AND service:payments'
            for clause in must
        )
        # Additional intent-driven probes still ran below.
        assert len(out) > 1

    def test_json_object_used_as_body_verbatim(self, monkeypatch):
        calls: list[dict] = []
        self._patch_post(monkeypatch, calls)
        adapter = ElasticsearchAdapter(base_url="http://es:9200")
        body = {
            "query": {"term": {"http.status_code": "500"}},
            "size": 5,
        }
        plan = {
            "intent": "error_investigation",
            "active_tool_args": {
                "query": json.dumps(body),
                "reason": "5xx only",
            },
        }
        adapter.investigate(
            service="api", time_range="15m", message="errors", plan=plan,
        )
        first_body = calls[0]["body"]
        assert first_body["query"] == {"term": {"http.status_code": "500"}}
        assert first_body["size"] == 5

    def test_json_object_without_size_gets_default(self, monkeypatch):
        calls: list[dict] = []
        self._patch_post(monkeypatch, calls)
        adapter = ElasticsearchAdapter(base_url="http://es:9200")
        body = {"query": {"match_all": {}}}
        plan = {
            "intent": "error_investigation",
            "active_tool_args": {"query": json.dumps(body), "reason": "all"},
        }
        adapter.investigate(
            service=None, time_range="15m", message="x", plan=plan,
        )
        first_body = calls[0]["body"]
        # Default size injected when LLM omitted it.
        assert first_body["size"] == 20

    def test_no_llm_query_keeps_existing_behaviour(self, monkeypatch):
        calls: list[dict] = []
        self._patch_post(monkeypatch, calls)
        adapter = ElasticsearchAdapter(base_url="http://es:9200")
        out = adapter.investigate(
            service="api", time_range="15m", message="errors",
            plan={"intent": "error_investigation"},
        )
        assert all("LLM-selected ES query" not in r["finding"] for r in out)

    def test_blank_llm_query_ignored(self, monkeypatch):
        calls: list[dict] = []
        self._patch_post(monkeypatch, calls)
        adapter = ElasticsearchAdapter(base_url="http://es:9200")
        plan = {
            "intent": "error_investigation",
            "active_tool_args": {"query": "   ", "reason": "blank"},
        }
        out = adapter.investigate(
            service="api", time_range="15m", message="x", plan=plan,
        )
        assert all("LLM-selected ES query" not in r["finding"] for r in out)

    def test_llm_query_failure_surfaces_error_row(self, monkeypatch):
        # Make the first POST (LLM) raise, the subsequent ones succeed.
        state = {"calls": 0}

        def fake_post(url, headers=None, json=None, verify=False, timeout=None):
            state["calls"] += 1
            if state["calls"] == 1:
                raise RuntimeError("boom")
            return _FakeResp({"hits": {"hits": []}})

        monkeypatch.setattr(
            "accelerators.ayosa.adapters.elasticsearch.requests.post",
            fake_post,
        )
        adapter = ElasticsearchAdapter(base_url="http://es:9200")
        plan = {
            "intent": "error_investigation",
            "active_tool_args": {"query": "status:500", "reason": "5xx"},
        }
        out = adapter.investigate(
            service="api", time_range="15m", message="x", plan=plan,
        )
        first = out[0]
        assert first["status"] == "error"
        assert "LLM-selected ES query failed" in first["finding"]


# ──────────────────────────────────────────────────────────────────────── #
# Jaeger
# ──────────────────────────────────────────────────────────────────────── #
class TestJaegerLLMQuery:
    def _patch_get(self, monkeypatch, recorder, *, payload=None):
        if payload is None:
            payload = {"data": [{"traceID": "abc"}]}

        def fake_get(url, params=None, timeout=None):
            recorder.append({"url": url, "params": params or {}})
            return _FakeResp(payload)

        monkeypatch.setattr(
            "accelerators.ayosa.adapters.jaeger.requests.get",
            fake_get,
        )

    def test_string_query_used_as_operation(self, monkeypatch):
        calls: list[dict] = []
        self._patch_get(monkeypatch, calls)
        adapter = JaegerAdapter(base_url="http://jaeger:16686")
        plan = {
            "intent": "trace_lookup",
            "active_tool_args": {
                "query": "POST /api/v1/pay",
                "reason": "payment endpoint traces",
            },
        }
        out = adapter.investigate(
            service="payments", time_range="30m", message="traces", plan=plan,
        )
        first = out[0]
        assert "LLM-selected Jaeger query executed" in first["finding"]
        assert first["query"] == "POST /api/v1/pay"
        # First request was the LLM operation lookup, scoped to service.
        assert calls[0]["params"]["operation"] == "POST /api/v1/pay"
        assert calls[0]["params"]["service"] == "payments"
        # Default intent-driven call followed.
        assert len(out) > 1

    def test_json_query_merged_as_params(self, monkeypatch):
        calls: list[dict] = []
        self._patch_get(monkeypatch, calls)
        adapter = JaegerAdapter(base_url="http://jaeger:16686")
        plan = {
            "intent": "trace_lookup",
            "active_tool_args": {
                "query": json.dumps({
                    "service": "checkout",
                    "operation": "GET /cart",
                    "minDuration": "200ms",
                }),
                "reason": "slow cart fetches",
            },
        }
        adapter.investigate(
            service="payments", time_range="30m", message="traces", plan=plan,
        )
        first_params = calls[0]["params"]
        # JSON service overrides the planner service in the LLM call.
        assert first_params["service"] == "checkout"
        assert first_params["operation"] == "GET /cart"
        assert first_params["minDuration"] == "200ms"
        assert first_params["limit"] == 20

    def test_string_query_without_service_is_skipped(self, monkeypatch):
        calls: list[dict] = []
        self._patch_get(monkeypatch, calls)
        adapter = JaegerAdapter(base_url="http://jaeger:16686")
        plan = {
            "intent": "trace_lookup",
            "active_tool_args": {
                "query": "POST /api/v1/pay",
                "reason": "no service supplied",
            },
        }
        out = adapter.investigate(
            service=None, time_range="30m", message="traces", plan=plan,
        )
        # LLM row marked skipped, no /api/traces call issued for it.
        first = out[0]
        assert first["status"] == "skipped"
        assert "needs a service" in first["finding"]
        # The /api/services fallback may still have been called by the
        # default intent path; but the first call (if any) was not the
        # LLM-driven /api/traces with operation=...
        assert not any(
            (c.get("params") or {}).get("operation") == "POST /api/v1/pay"
            for c in calls
        )

    def test_no_llm_query_keeps_existing_behaviour(self, monkeypatch):
        calls: list[dict] = []
        self._patch_get(monkeypatch, calls)
        adapter = JaegerAdapter(base_url="http://jaeger:16686")
        out = adapter.investigate(
            service="payments", time_range="30m", message="x",
            plan={"intent": "trace_lookup"},
        )
        assert all("LLM-selected Jaeger query" not in r["finding"] for r in out)

    def test_blank_llm_query_ignored(self, monkeypatch):
        calls: list[dict] = []
        self._patch_get(monkeypatch, calls)
        adapter = JaegerAdapter(base_url="http://jaeger:16686")
        plan = {
            "intent": "trace_lookup",
            "active_tool_args": {"query": "  ", "reason": "blank"},
        }
        out = adapter.investigate(
            service="payments", time_range="30m", message="x", plan=plan,
        )
        assert all("LLM-selected Jaeger query" not in r["finding"] for r in out)

    def test_llm_query_failure_surfaces_error_row(self, monkeypatch):
        state = {"calls": 0}

        def fake_get(url, params=None, timeout=None):
            state["calls"] += 1
            if state["calls"] == 1:
                raise RuntimeError("boom")
            return _FakeResp({"data": []})

        monkeypatch.setattr(
            "accelerators.ayosa.adapters.jaeger.requests.get",
            fake_get,
        )
        adapter = JaegerAdapter(base_url="http://jaeger:16686")
        plan = {
            "intent": "trace_lookup",
            "active_tool_args": {"query": "POST /pay", "reason": "x"},
        }
        out = adapter.investigate(
            service="payments", time_range="30m", message="x", plan=plan,
        )
        first = out[0]
        assert first["status"] == "error"
        assert "LLM-selected Jaeger query failed" in first["finding"]
