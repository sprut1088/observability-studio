"""Tests for the LLM tool-selector and its wiring into the Planner.

Step 7 — Replaces deterministic ``select_tools_for_signals`` with an LLM
tool-call when an LLM is configured. The selector itself is a pure
function (``select_tools_llm``) so we patch the provider call.

Safety guarantees we assert:

* When the LLM is disabled → returns ``None`` → Planner uses the
  deterministic selector.
* Empty configured-tool list → returns ``None``.
* Unknown tool names emitted by the LLM are dropped.
* When the LLM picks zero tools for an intent that requires signals →
  Planner falls back to the deterministic selector.
* When the LLM picks valid tools → Planner uses them and sets
  ``selection_meta.mode == "llm"``.
* The JSON-Schema enum is the configured tool set.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from accelerators.ayosa.agent.planner import Planner
from accelerators.ayosa.agent.schemas import (
    AgentAIConfig,
    AgentInput,
    AgentToolConfig,
)
from accelerators.ayosa.agent.tool_selector_llm import (
    SelectorResult,
    TOOL_SELECTOR_TOOL_NAME,
    anthropic_tool_selector_tool,
    openai_tool_selector_tool,
    select_tools_llm,
)


# ──────────────────────────────────────────────────────────────────────── #
# Helpers
# ──────────────────────────────────────────────────────────────────────── #
def _agent_input(message: str, *, tools: list[str], llm: bool = True) -> AgentInput:
    return AgentInput(
        message=message,
        service="api",
        time_range="30m",
        tools=[AgentToolConfig(tool=t, base_url=f"http://{t}") for t in tools],
        llm=AgentAIConfig(
            enabled=llm,
            provider="anthropic",
            api_key="fake",
            model="claude-sonnet-4-6",
        ),
    )


def _llm_cfg(enabled: bool = True) -> AgentAIConfig:
    return AgentAIConfig(
        enabled=enabled,
        provider="anthropic",
        api_key="fake",
        model="claude-sonnet-4-6",
    )


# ──────────────────────────────────────────────────────────────────────── #
# Schema shape
# ──────────────────────────────────────────────────────────────────────── #
class TestSchemaShape:
    def test_anthropic_schema_constrains_enum(self):
        spec = anthropic_tool_selector_tool(["prometheus", "loki"])
        assert spec["name"] == TOOL_SELECTOR_TOOL_NAME
        schema = spec["input_schema"]
        # Step 9: per-tool call array, each item's `name` enum-constrained.
        call_item = schema["properties"]["tool_calls"]["items"]
        assert call_item["properties"]["name"]["enum"] == ["prometheus", "loki"]
        assert set(call_item["required"]) == {"name", "reason"}
        assert set(call_item["properties"].keys()) == {
            "name", "service", "time_range", "query", "reason",
        }
        assert schema["required"] == ["tool_calls", "reasoning"]
        assert schema["additionalProperties"] is False

    def test_openai_schema_constrains_enum(self):
        spec = openai_tool_selector_tool(["grafana"])
        assert spec["type"] == "function"
        params = spec["function"]["parameters"]
        call_item = params["properties"]["tool_calls"]["items"]
        assert call_item["properties"]["name"]["enum"] == ["grafana"]


# ──────────────────────────────────────────────────────────────────────── #
# select_tools_llm — guards
# ──────────────────────────────────────────────────────────────────────── #
class TestSelectorGuards:
    def test_returns_none_when_llm_disabled(self):
        out = select_tools_llm(
            message="latency on api",
            intent="latency_issues",
            configured_tools=["prometheus"],
            required_signals=["metrics"],
            llm_config=_llm_cfg(enabled=False),
        )
        assert out is None

    def test_returns_none_when_no_configured_tools(self):
        out = select_tools_llm(
            message="latency on api",
            intent="latency_issues",
            configured_tools=[],
            required_signals=["metrics"],
            llm_config=_llm_cfg(),
        )
        assert out is None

    def test_returns_none_when_message_empty(self):
        out = select_tools_llm(
            message="   ",
            intent="latency_issues",
            configured_tools=["prometheus"],
            required_signals=["metrics"],
            llm_config=_llm_cfg(),
        )
        assert out is None

    def test_returns_none_on_provider_exception(self, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("simulated provider failure")
        monkeypatch.setattr(
            "accelerators.ayosa.agent.tool_selector_llm._call_anthropic",
            boom,
        )
        out = select_tools_llm(
            message="latency on api",
            intent="latency_issues",
            configured_tools=["prometheus"],
            required_signals=["metrics"],
            llm_config=_llm_cfg(),
        )
        assert out is None


# ──────────────────────────────────────────────────────────────────────── #
# select_tools_llm — parsing + allowlist
# ──────────────────────────────────────────────────────────────────────── #
class TestSelectorParsing:
    def test_valid_response_returns_selection(self, monkeypatch):
        monkeypatch.setattr(
            "accelerators.ayosa.agent.tool_selector_llm._call_anthropic",
            lambda *a, **kw: json.dumps({
                "tool_names": ["prometheus", "loki"],
                "reasoning": "metrics + logs needed",
            }),
        )
        out = select_tools_llm(
            message="why are payments slow",
            intent="latency_issues",
            configured_tools=["prometheus", "loki", "grafana"],
            required_signals=["metrics", "logs"],
            llm_config=_llm_cfg(),
        )
        assert out is not None
        assert isinstance(out, SelectorResult)
        assert out.tool_names == ["prometheus", "loki"]
        assert "metrics" in out.reasoning
        assert out.provider == "anthropic"

    def test_unknown_tool_names_are_dropped(self, monkeypatch):
        monkeypatch.setattr(
            "accelerators.ayosa.agent.tool_selector_llm._call_anthropic",
            lambda *a, **kw: json.dumps({
                "tool_names": ["prometheus", "splunk", "datadog"],
                "reasoning": "...",
            }),
        )
        out = select_tools_llm(
            message="errors",
            intent="error_investigation",
            configured_tools=["prometheus", "loki"],
            required_signals=["logs"],
            llm_config=_llm_cfg(),
        )
        assert out is not None
        assert out.tool_names == ["prometheus"]

    def test_duplicates_collapsed_and_case_normalised(self, monkeypatch):
        monkeypatch.setattr(
            "accelerators.ayosa.agent.tool_selector_llm._call_anthropic",
            lambda *a, **kw: json.dumps({
                "tool_names": ["Prometheus", "PROMETHEUS", "loki"],
                "reasoning": "...",
            }),
        )
        out = select_tools_llm(
            message="errors",
            intent="error_investigation",
            configured_tools=["prometheus", "loki"],
            required_signals=["logs"],
            llm_config=_llm_cfg(),
        )
        assert out is not None
        assert out.tool_names == ["prometheus", "loki"]

    def test_garbage_response_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            "accelerators.ayosa.agent.tool_selector_llm._call_anthropic",
            lambda *a, **kw: "not json",
        )
        out = select_tools_llm(
            message="errors",
            intent="error_investigation",
            configured_tools=["prometheus"],
            required_signals=["logs"],
            llm_config=_llm_cfg(),
        )
        assert out is None


# ──────────────────────────────────────────────────────────────────────── #
# Planner wiring — fallback + LLM path
# ──────────────────────────────────────────────────────────────────────── #
class TestPlannerWiring:
    def test_deterministic_fallback_when_no_llm_config(self):
        ai = AgentInput(
            message="latency on api",
            service="api",
            time_range="30m",
            tools=[
                AgentToolConfig(tool="prometheus", base_url="http://p"),
                AgentToolConfig(tool="loki", base_url="http://l"),
            ],
            llm=None,
        )
        plan = Planner().build(ai, intent_hint="latency_issues")
        # Deterministic mode marker
        assert plan.selection_meta == {"mode": "deterministic"}
        # Prometheus covers metrics (deterministic path picks it)
        assert "prometheus" in plan.selected_tools

    def test_llm_pick_replaces_deterministic_selection(self, monkeypatch):
        """LLM picks only `loki`; planner must honour that even though
        deterministic would have included prometheus for metrics."""
        def fake_call(*a, **kw):
            return json.dumps({
                "tool_names": ["loki"],
                "reasoning": "Logs-first investigation for this question",
            })
        monkeypatch.setattr(
            "accelerators.ayosa.agent.tool_selector_llm._call_anthropic",
            fake_call,
        )
        ai = _agent_input(
            "find error logs around the latency spike",
            tools=["prometheus", "loki"],
        )
        plan = Planner().build(ai, intent_hint="error_investigation")
        assert plan.selected_tools == ["loki"]
        assert plan.selection_meta is not None
        assert plan.selection_meta["mode"] == "llm"
        assert plan.selection_meta["provider"] == "anthropic"
        assert "Logs-first" in plan.selection_meta["reasoning"]
        # `prometheus` is now in skipped_tools since LLM didn't pick it
        assert "prometheus" in plan.skipped_tools

    def test_empty_llm_pick_falls_back_when_signals_required(self, monkeypatch):
        monkeypatch.setattr(
            "accelerators.ayosa.agent.tool_selector_llm._call_anthropic",
            lambda *a, **kw: json.dumps({"tool_names": [], "reasoning": "nope"}),
        )
        ai = _agent_input(
            "errors on api in last hour",
            tools=["prometheus", "loki"],
        )
        plan = Planner().build(ai, intent_hint="error_investigation")
        # Required signals exist → empty LLM pick must trigger fallback
        assert plan.selection_meta == {"mode": "deterministic"}
        assert plan.selected_tools  # deterministic produced something

    def test_llm_failure_falls_back_silently(self, monkeypatch):
        def boom(*a, **kw):
            raise RuntimeError("provider down")
        monkeypatch.setattr(
            "accelerators.ayosa.agent.tool_selector_llm._call_anthropic",
            boom,
        )
        ai = _agent_input(
            "errors on api",
            tools=["prometheus", "loki"],
        )
        plan = Planner().build(ai, intent_hint="error_investigation")
        assert plan.selection_meta == {"mode": "deterministic"}

    def test_covered_signals_recomputed_from_llm_selection(self, monkeypatch):
        """When LLM picks only `loki`, the plan's covered_signals must
        reflect loki's signal types (logs), not what deterministic
        selection would have produced."""
        monkeypatch.setattr(
            "accelerators.ayosa.agent.tool_selector_llm._call_anthropic",
            lambda *a, **kw: json.dumps({
                "tool_names": ["loki"],
                "reasoning": "logs only",
            }),
        )
        ai = _agent_input(
            "find error logs",
            tools=["prometheus", "loki"],
        )
        plan = Planner().build(ai, intent_hint="error_investigation")
        # `logs` should be covered (loki provides logs).
        # `metrics`, if required, should appear in missing_signals.
        assert "logs" in plan.covered_signals
        if "metrics" in plan.required_signals:
            assert "metrics" in plan.missing_signals
