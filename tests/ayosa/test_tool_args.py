"""Step 9 tests — per-tool arguments emitted by the LLM tool-selector
flow through to the dispatcher.

Covered:

* Selector parses the rich ``tool_calls`` shape and validates each call
  via ``validate_tool_call``; invalid entries are dropped.
* ``tool_args`` round-trips through ``Plan`` (incl. re-plan).
* Dispatcher overrides ``service`` and ``time_range`` per-tool, and
  surfaces ``query`` / ``reason`` on the carried ``plan`` payload via
  the ``active_tool_args`` key.
* Backward compatibility: legacy ``tool_names`` responses still work
  and produce ``tool_args == None``.
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
    Plan,
)
from accelerators.ayosa.agent.tool_dispatcher import ToolDispatcher
from accelerators.ayosa.agent.tool_selector_llm import (
    SelectorResult,
    select_tools_llm,
)


# ──────────────────────────────────────────────────────────────────────── #
# Helpers
# ──────────────────────────────────────────────────────────────────────── #
def _llm_cfg(enabled: bool = True) -> AgentAIConfig:
    return AgentAIConfig(
        enabled=enabled,
        provider="anthropic",
        api_key="fake",
        model="claude-sonnet-4-6",
    )


def _agent_input(message: str, *, tools: list[str]) -> AgentInput:
    return AgentInput(
        message=message,
        service="api",
        time_range="30m",
        tools=[AgentToolConfig(tool=t, base_url=f"http://{t}") for t in tools],
        llm=_llm_cfg(),
    )


class _RecordingAdapter:
    """Fake adapter capturing kwargs passed to ``investigate``."""

    last_kwargs: dict[str, Any] | None = None

    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        self.base_url = base_url
        self.auth_token = auth_token

    def investigate(
        self,
        service: str | None,
        time_range: str,
        message: str,
        plan: dict | None = None,
    ) -> list[dict]:
        _RecordingAdapter.last_kwargs = {
            "service": service,
            "time_range": time_range,
            "message": message,
            "plan": plan,
        }
        return []


# ──────────────────────────────────────────────────────────────────────── #
# Selector — parsing the rich tool_calls shape
# ──────────────────────────────────────────────────────────────────────── #
class TestSelectorRichParsing:
    def test_tool_calls_populate_tool_args(self, monkeypatch):
        monkeypatch.setattr(
            "accelerators.ayosa.agent.tool_selector_llm._call_anthropic",
            lambda *a, **kw: json.dumps({
                "tool_calls": [
                    {
                        "name": "prometheus",
                        "service": "payments",
                        "time_range": "15m",
                        "query": "rate(http_requests_total[5m])",
                        "reason": "p95 latency spike on payments",
                    },
                    {
                        "name": "loki",
                        "service": None,
                        "time_range": "30m",
                        "query": '{job="payments"} |= "error"',
                        "reason": "correlate with error logs",
                    },
                ],
                "reasoning": "metrics + logs for latency",
            }),
        )
        out = select_tools_llm(
            message="why is payments slow?",
            intent="latency_issues",
            configured_tools=["prometheus", "loki"],
            required_signals=["metrics", "logs"],
            llm_config=_llm_cfg(),
        )
        assert isinstance(out, SelectorResult)
        assert out.tool_names == ["prometheus", "loki"]
        assert out.tool_args["prometheus"]["service"] == "payments"
        assert out.tool_args["prometheus"]["time_range"] == "15m"
        assert "rate(" in out.tool_args["prometheus"]["query"]
        assert out.tool_args["prometheus"]["reason"].startswith("p95")
        assert out.tool_args["loki"]["service"] is None
        assert out.tool_args["loki"]["time_range"] == "30m"

    def test_invalid_tool_call_dropped_by_validate(self, monkeypatch):
        # First entry is missing the required ``reason`` field; second is
        # well-formed. The selector must drop the first and keep the second.
        monkeypatch.setattr(
            "accelerators.ayosa.agent.tool_selector_llm._call_anthropic",
            lambda *a, **kw: json.dumps({
                "tool_calls": [
                    {"name": "prometheus", "service": "api"},
                    {"name": "loki", "reason": "logs"},
                ],
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
        assert out.tool_names == ["loki"]
        assert "prometheus" not in out.tool_args
        assert "loki" in out.tool_args

    def test_unknown_tool_name_in_call_dropped(self, monkeypatch):
        monkeypatch.setattr(
            "accelerators.ayosa.agent.tool_selector_llm._call_anthropic",
            lambda *a, **kw: json.dumps({
                "tool_calls": [
                    {"name": "datadog", "reason": "not configured"},
                    {"name": "prometheus", "reason": "metrics"},
                ],
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
        assert list(out.tool_args.keys()) == ["prometheus"]

    def test_duplicate_tool_call_keeps_first(self, monkeypatch):
        monkeypatch.setattr(
            "accelerators.ayosa.agent.tool_selector_llm._call_anthropic",
            lambda *a, **kw: json.dumps({
                "tool_calls": [
                    {"name": "prometheus", "service": "api", "reason": "first"},
                    {"name": "prometheus", "service": "checkout", "reason": "dup"},
                ],
                "reasoning": "...",
            }),
        )
        out = select_tools_llm(
            message="latency",
            intent="latency_issues",
            configured_tools=["prometheus"],
            required_signals=["metrics"],
            llm_config=_llm_cfg(),
        )
        assert out is not None
        assert out.tool_names == ["prometheus"]
        assert out.tool_args["prometheus"]["service"] == "api"

    def test_legacy_tool_names_response_still_works(self, monkeypatch):
        """Backward compat — Step 7 response shape must keep working but
        leave ``tool_args`` empty for each picked tool."""
        monkeypatch.setattr(
            "accelerators.ayosa.agent.tool_selector_llm._call_anthropic",
            lambda *a, **kw: json.dumps({
                "tool_names": ["prometheus"],
                "reasoning": "legacy shape",
            }),
        )
        out = select_tools_llm(
            message="latency",
            intent="latency_issues",
            configured_tools=["prometheus", "loki"],
            required_signals=["metrics"],
            llm_config=_llm_cfg(),
        )
        assert out is not None
        assert out.tool_names == ["prometheus"]
        # Each legacy pick gets an empty args dict — no overrides.
        assert out.tool_args == {"prometheus": {}}


# ──────────────────────────────────────────────────────────────────────── #
# Planner — tool_args round-trip
# ──────────────────────────────────────────────────────────────────────── #
class TestPlannerCarriesArgs:
    def test_tool_args_attached_to_plan(self, monkeypatch):
        monkeypatch.setattr(
            "accelerators.ayosa.agent.tool_selector_llm._call_anthropic",
            lambda *a, **kw: json.dumps({
                "tool_calls": [
                    {
                        "name": "prometheus",
                        "service": "payments",
                        "time_range": "5m",
                        "query": "up",
                        "reason": "service health probe",
                    },
                ],
                "reasoning": "...",
            }),
        )
        ai = _agent_input("is payments healthy?", tools=["prometheus", "loki"])
        plan = Planner().build(ai, intent_hint="service_health")
        assert plan.tool_args is not None
        assert "prometheus" in plan.tool_args
        assert plan.tool_args["prometheus"]["service"] == "payments"
        assert plan.tool_args["prometheus"]["time_range"] == "5m"
        assert plan.tool_args["prometheus"]["query"] == "up"

    def test_legacy_response_leaves_tool_args_none(self, monkeypatch):
        monkeypatch.setattr(
            "accelerators.ayosa.agent.tool_selector_llm._call_anthropic",
            lambda *a, **kw: json.dumps({
                "tool_names": ["prometheus"],
                "reasoning": "legacy",
            }),
        )
        ai = _agent_input("latency", tools=["prometheus", "loki"])
        plan = Planner().build(ai, intent_hint="latency_issues")
        # Legacy path returns empty-dict args for each pick → treated as
        # "no overrides" and stored as None on the plan.
        assert plan.tool_args is None


# ──────────────────────────────────────────────────────────────────────── #
# Dispatcher — per-tool overrides
# ──────────────────────────────────────────────────────────────────────── #
class TestDispatcherOverrides:
    def _make_plan(self, tool_args: dict | None) -> Plan:
        return Plan(
            intent="latency_issues",
            service="api",
            time_range="30m",
            required_signals=["metrics"],
            selected_tools=["prometheus"],
            tool_args=tool_args,
        )

    def test_dispatcher_overrides_service_and_time_range(self):
        _RecordingAdapter.last_kwargs = None
        ai = AgentInput(
            message="why is payments slow",
            service="api",        # planner default
            time_range="30m",     # planner default
            tools=[AgentToolConfig(tool="prometheus", base_url="http://p")],
        )
        plan = self._make_plan({
            "prometheus": {
                "service": "payments",
                "time_range": "5m",
                "query": "rate(http_requests_total[5m])",
                "reason": "narrow window for spike",
            }
        })
        ToolDispatcher({"prometheus": _RecordingAdapter}).dispatch(plan, ai)
        kw = _RecordingAdapter.last_kwargs
        assert kw is not None
        assert kw["service"] == "payments"     # overridden
        assert kw["time_range"] == "5m"        # overridden
        # The plan-accepting adapter received the per-tool args under a
        # stable key so it can read ``query`` / ``reason``.
        assert kw["plan"] is not None
        assert kw["plan"]["active_tool_args"]["query"].startswith("rate(")
        assert kw["plan"]["active_tool_args"]["reason"] == "narrow window for spike"

    def test_dispatcher_falls_back_to_planner_defaults(self):
        _RecordingAdapter.last_kwargs = None
        ai = AgentInput(
            message="errors",
            service="api",
            time_range="30m",
            tools=[AgentToolConfig(tool="prometheus", base_url="http://p")],
        )
        # No tool_args → planner defaults must be used as before.
        plan = self._make_plan(None)
        ToolDispatcher({"prometheus": _RecordingAdapter}).dispatch(plan, ai)
        kw = _RecordingAdapter.last_kwargs
        assert kw is not None
        assert kw["service"] == "api"
        assert kw["time_range"] == "30m"
        # No per-tool args ⇒ no active_tool_args injected.
        assert "active_tool_args" not in (kw["plan"] or {})

    def test_dispatcher_partial_override_only_time_range(self):
        _RecordingAdapter.last_kwargs = None
        ai = AgentInput(
            message="errors",
            service="api",
            time_range="30m",
            tools=[AgentToolConfig(tool="prometheus", base_url="http://p")],
        )
        plan = self._make_plan({
            "prometheus": {
                "service": None,           # null → use planner default
                "time_range": "1h",        # override
                "query": None,
                "reason": "wider lookback",
            }
        })
        ToolDispatcher({"prometheus": _RecordingAdapter}).dispatch(plan, ai)
        kw = _RecordingAdapter.last_kwargs
        assert kw is not None
        assert kw["service"] == "api"      # null tool_args ⇒ default
        assert kw["time_range"] == "1h"    # overridden
