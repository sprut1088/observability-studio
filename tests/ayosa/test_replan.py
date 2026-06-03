"""Tests for the AYOSA re-plan loop.

Covers the pure replanner helpers and the AyosaAgent's iterative
behaviour with mock adapters that simulate empty / failing first-pass
results.
"""

from __future__ import annotations

import pytest

from accelerators.ayosa.agent import AyosaAgent
from accelerators.ayosa.agent.replanner import (
    build_replan,
    derived_input_for_replan,
    find_additional_tools,
    gap_signals,
)
from accelerators.ayosa.agent.schemas import (
    AgentInput,
    AgentToolConfig,
    Observation,
    Plan,
    ReflectionNote,
)
from accelerators.ayosa.agent.tool_dispatcher import ToolDispatcher


# --------------------------------------------------------------------------- #
# Fake adapters
# --------------------------------------------------------------------------- #
class _EmptyAdapter:
    """Returns no findings — simulates a tool that produced nothing."""
    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        pass

    def investigate(self, **kwargs):  # noqa: ARG002
        return []


class _OkLogs:
    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        pass

    def investigate(self, **kwargs):  # noqa: ARG002
        return [{
            "source": "loki",
            "signal": "logs",
            "finding": "5 ERROR-level entries",
            "status": "ok",
            "raw": {"timestamp": "2026-06-03T10:00:00Z"},
        }]


class _OkAlerts:
    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        pass

    def investigate(self, **kwargs):  # noqa: ARG002
        return [{
            "source": "alertmanager",
            "signal": "alerts",
            "finding": "No firing alerts",
            "status": "ok",
        }]


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_gap_signals_returns_missing_and_empty():
    refs = [
        ReflectionNote(signal="logs", status="missing", note=""),
        ReflectionNote(signal="metrics", status="sufficient", note=""),
        ReflectionNote(signal="alerts", status="empty", note=""),
        ReflectionNote(signal="traces", status="partial", note=""),
    ]
    assert gap_signals(refs) == ["logs", "alerts"]


def test_find_additional_tools_skips_already_dispatched():
    agent_input = AgentInput(
        message="x",
        tools=[
            AgentToolConfig(tool="elasticsearch", base_url="http://e"),
            AgentToolConfig(tool="loki", base_url="http://l"),
        ],
    )
    additional, covered = find_additional_tools(
        agent_input, gaps=["logs"], already_dispatched={"elasticsearch"}
    )
    assert additional == ["loki"]
    assert covered == ["logs"]


def test_find_additional_tools_returns_empty_when_nothing_left():
    agent_input = AgentInput(
        message="x",
        tools=[AgentToolConfig(tool="elasticsearch", base_url="http://e")],
    )
    additional, covered = find_additional_tools(
        agent_input, gaps=["logs"], already_dispatched={"elasticsearch"}
    )
    assert additional == []
    assert covered == []


def test_build_replan_returns_none_when_no_gaps():
    plan = Plan(intent="active_alerts", service=None, time_range="30m",
                required_signals=["alerts"])
    agent_input = AgentInput(
        message="x",
        tools=[AgentToolConfig(tool="alertmanager", base_url="http://a")],
    )
    refs = [ReflectionNote(signal="alerts", status="sufficient", note="ok")]
    assert build_replan(plan, agent_input, refs, {"alertmanager"}) is None


def test_build_replan_returns_narrow_plan_when_gap_exists():
    plan = Plan(intent="error_investigation", service=None, time_range="30m",
                required_signals=["logs", "metrics"])
    agent_input = AgentInput(
        message="x",
        tools=[
            AgentToolConfig(tool="elasticsearch", base_url="http://e"),
            AgentToolConfig(tool="loki", base_url="http://l"),
            AgentToolConfig(tool="prometheus", base_url="http://p"),
        ],
    )
    refs = [
        ReflectionNote(signal="logs", status="empty", note=""),
        ReflectionNote(signal="metrics", status="sufficient", note=""),
    ]
    new_plan = build_replan(plan, agent_input, refs, {"elasticsearch", "prometheus"})
    assert new_plan is not None
    assert new_plan.intent == "error_investigation"
    assert new_plan.selected_tools == ["loki"]
    assert new_plan.covered_signals == ["logs"]
    assert "Re-plan" in new_plan.explanation


def test_derived_input_filters_tools():
    agent_input = AgentInput(
        message="x",
        tools=[
            AgentToolConfig(tool="elasticsearch", base_url="http://e"),
            AgentToolConfig(tool="loki", base_url="http://l"),
        ],
    )
    narrowed = derived_input_for_replan(agent_input, ["loki"])
    assert [t.tool for t in narrowed.tools] == ["loki"]
    # Original unchanged.
    assert [t.tool for t in agent_input.tools] == ["elasticsearch", "loki"]


# --------------------------------------------------------------------------- #
# AyosaAgent.run end-to-end (mocked adapters)
# --------------------------------------------------------------------------- #
def _agent(adapters: dict, max_iterations: int = 2) -> AyosaAgent:
    return AyosaAgent(
        dispatcher=ToolDispatcher(adapters=adapters),
        max_iterations=max_iterations,
    )


def test_agent_triggers_replan_when_first_logs_tool_empty():
    """First-pass logs tool (elasticsearch) returns nothing; loki should be
    invoked in iteration 1 to cover the logs gap."""
    adapters = {"elasticsearch": _EmptyAdapter, "loki": _OkLogs}
    agent = _agent(adapters)
    agent_input = AgentInput(
        message="latest error in checkout",  # intent: latest_error -> needs logs
        service="checkout",
        time_range="30m",
        tools=[
            AgentToolConfig(tool="elasticsearch", base_url="http://e"),
            AgentToolConfig(tool="loki", base_url="http://l"),
        ],
    )
    result = agent.run(agent_input)

    assert result.iterations == 2
    assert result.replan_reason and "logs" in result.replan_reason
    # both tools should appear among steps, with different iteration tags
    tool_iterations = {s.tool.lower(): s.iteration for s in result.tool_steps
                       if s.status == "done"}
    assert tool_iterations.get("loki") == 1
    # at least one finding from loki must be present
    assert any(o.source == "loki" and o.status == "ok" for o in result.observations)


def test_agent_skips_replan_when_first_pass_sufficient():
    """First-pass tool returns findings → no second iteration."""
    adapters = {"alertmanager": _OkAlerts}
    agent = _agent(adapters)
    agent_input = AgentInput(
        message="any firing alerts",  # intent: active_alerts -> needs alerts
        time_range="30m",
        tools=[AgentToolConfig(tool="alertmanager", base_url="http://a")],
    )
    result = agent.run(agent_input)

    assert result.iterations == 1
    assert result.replan_reason is None


def test_agent_skips_replan_when_no_alternative_tool_configured():
    """First-pass tool empty but no other tool configured for the signal."""
    adapters = {"elasticsearch": _EmptyAdapter}
    agent = _agent(adapters)
    agent_input = AgentInput(
        message="latest error",
        time_range="30m",
        tools=[AgentToolConfig(tool="elasticsearch", base_url="http://e")],
    )
    result = agent.run(agent_input)

    assert result.iterations == 1
    assert result.replan_reason is None


def test_agent_max_iterations_one_disables_replan():
    """max_iterations=1 keeps the legacy single-pass behaviour."""
    adapters = {"elasticsearch": _EmptyAdapter, "loki": _OkLogs}
    agent = _agent(adapters, max_iterations=1)
    agent_input = AgentInput(
        message="latest error",
        time_range="30m",
        tools=[
            AgentToolConfig(tool="elasticsearch", base_url="http://e"),
            AgentToolConfig(tool="loki", base_url="http://l"),
        ],
    )
    result = agent.run(agent_input)

    assert result.iterations == 1
    assert result.replan_reason is None
    # Loki must NOT have been invoked (no alternative attempted).
    assert not any(o.source == "loki" and o.status == "ok" for o in result.observations)
