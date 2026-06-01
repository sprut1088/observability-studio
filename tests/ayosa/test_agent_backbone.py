"""Unit tests for the AYOSA agent backbone.

These tests exercise the agent's pure functions and the dispatcher with
mocked adapters — they do NOT touch real observability tools or any
network, and they do NOT depend on the existing AyosaService chat path.
"""

from __future__ import annotations

import pytest

from accelerators.ayosa.agent import (
    AyosaAgent,
    classify_intent,
    reflect_on_observations,
)
from accelerators.ayosa.agent.context_manager import ContextManager
from accelerators.ayosa.agent.planner import Planner
from accelerators.ayosa.agent.schemas import (
    AgentInput,
    AgentToolConfig,
    ConversationTurn,
    Observation,
    Plan,
)
from accelerators.ayosa.agent.synthesizer import (
    Synthesizer,
    build_snapshot,
    calculate_confidence,
    compose_deterministic_answer,
)
from accelerators.ayosa.agent.tool_dispatcher import (
    ToolDispatcher,
    normalise_observation,
)


# ──────────────────────────────────────────────────────────────────────── #
# Fakes
# ──────────────────────────────────────────────────────────────────────── #
class _FakePrometheus:
    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        self.base_url = base_url

    def investigate(self, service, time_range, message, plan=None):  # noqa: ARG002
        return [
            {
                "source": "prometheus",
                "signal": "metrics",
                "finding": "rate(http_5xx) = 0.02 over 5m",
                "query": "rate(http_requests_total{status=~\"5..\"}[5m])",
                "status": "ok",
                "raw": {"timestamp": "2026-06-01T10:00:00Z"},
            }
        ]


class _FakeAlertmanager:
    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        self.base_url = base_url

    def investigate(self, service, time_range, message):  # no plan kw
        return [
            {
                "source": "alertmanager",
                "signal": "alerts",
                "finding": "No firing alerts",
                "status": "ok",
            }
        ]


class _FakeFailing:
    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        pass

    def investigate(self, **kwargs):  # noqa: ARG002
        raise RuntimeError("boom")


FAKE_ADAPTERS = {
    "prometheus": _FakePrometheus,
    "alertmanager": _FakeAlertmanager,
    "splunk": _FakeFailing,
}


def _input(message: str, tools: list[str] | None = None) -> AgentInput:
    return AgentInput(
        message=message,
        service="checkout",
        time_range="15m",
        tools=[
            AgentToolConfig(tool=t, base_url=f"http://{t}.example", auth_token=None)
            for t in (tools or ["prometheus", "alertmanager"])
        ],
        session_id="t-1",
    )


# ──────────────────────────────────────────────────────────────────────── #
# Intent classifier
# ──────────────────────────────────────────────────────────────────────── #
class TestIntentClassifier:
    def test_active_alerts(self):
        assert classify_intent("are there any alerts firing?") == "active_alerts"

    def test_latency(self):
        assert classify_intent("p99 latency is slow") == "latency_issues"

    def test_fallback(self):
        assert (
            classify_intent("tell me about my system")
            == "general_observability_question"
        )


# ──────────────────────────────────────────────────────────────────────── #
# Planner
# ──────────────────────────────────────────────────────────────────────── #
class TestPlanner:
    def test_selects_metric_tool_for_latency(self):
        plan = Planner().build(_input("p99 latency for checkout"), "latency_issues")
        assert plan.intent == "latency_issues"
        assert "prometheus" in plan.selected_tools
        assert plan.should_query_metrics is True

    def test_selects_alert_tool_for_active_alerts(self):
        plan = Planner().build(_input("any active alerts?"), "active_alerts")
        assert "alertmanager" in plan.selected_tools
        assert plan.should_query_alerts is True

    def test_no_signals_for_current_time(self):
        plan = Planner().build(_input("what time is it?"), "current_time")
        assert plan.required_signals == []


# ──────────────────────────────────────────────────────────────────────── #
# Tool dispatcher
# ──────────────────────────────────────────────────────────────────────── #
class TestToolDispatcher:
    def test_dispatches_only_selected_tools(self):
        agent_in = _input("errors in last 15m", tools=["prometheus", "alertmanager"])
        plan = Plan(
            intent="error_investigation",
            service="checkout",
            time_range="15m",
            required_signals=["metrics"],
            selected_tools=["prometheus"],
        )
        steps, observations = ToolDispatcher(FAKE_ADAPTERS).dispatch(plan, agent_in)

        statuses = {s.tool: s.status for s in steps}
        assert statuses["prometheus"] == "done"
        assert statuses["alertmanager"] == "skipped"
        assert len(observations) == 1
        assert observations[0].source == "prometheus"

    def test_adapter_failure_captured_as_error_observation(self):
        agent_in = _input("logs", tools=["splunk"])
        plan = Plan(
            intent="error_investigation",
            service=None,
            time_range="15m",
            required_signals=["logs"],
            selected_tools=["splunk"],
        )
        steps, observations = ToolDispatcher(FAKE_ADAPTERS).dispatch(plan, agent_in)
        assert steps[0].status == "error"
        assert observations[0].status == "error"
        assert "boom" in observations[0].finding

    def test_unknown_tool_emits_error_observation(self):
        agent_in = _input("anything", tools=["nope"])
        plan = Plan(
            intent="general_observability_question",
            service=None,
            time_range="15m",
            required_signals=["metrics"],
            selected_tools=["nope"],
        )
        steps, observations = ToolDispatcher(FAKE_ADAPTERS).dispatch(plan, agent_in)
        assert steps[0].status == "error" or observations[0].status == "error"

    def test_normalise_observation_from_dict(self):
        obs = normalise_observation({"source": "x", "signal": "metrics", "finding": "ok"})
        assert isinstance(obs, Observation)
        assert obs.source == "x"


# ──────────────────────────────────────────────────────────────────────── #
# Reflect
# ──────────────────────────────────────────────────────────────────────── #
class TestReflect:
    def test_missing_signal(self):
        plan = Plan(
            intent="error_investigation",
            service=None,
            time_range="15m",
            required_signals=["metrics", "logs"],
        )
        notes = reflect_on_observations(plan, [])
        statuses = {n.signal: n.status for n in notes}
        assert statuses == {"metrics": "missing", "logs": "missing"}

    def test_sufficient_and_partial(self):
        plan = Plan(
            intent="error_investigation",
            service=None,
            time_range="15m",
            required_signals=["metrics", "logs"],
        )
        observations = [
            Observation(source="prom", signal="metrics", finding="ok", status="ok"),
            Observation(source="es", signal="logs", finding="ok", status="ok"),
            Observation(source="es2", signal="logs", finding="bad", status="error"),
        ]
        notes = reflect_on_observations(plan, observations)
        statuses = {n.signal: n.status for n in notes}
        assert statuses["metrics"] == "sufficient"
        assert statuses["logs"] == "partial"


# ──────────────────────────────────────────────────────────────────────── #
# Synthesizer (pure functions)
# ──────────────────────────────────────────────────────────────────────── #
class TestSynthesizerPure:
    def test_confidence_no_required_signals(self):
        plan = Plan(intent="current_time", service=None, time_range="5m")
        assert calculate_confidence(plan, [], []) == 1.0

    def test_confidence_sufficient(self):
        plan = Plan(
            intent="error_investigation",
            service=None,
            time_range="15m",
            required_signals=["metrics", "logs"],
        )
        obs = [
            Observation(source="prom", signal="metrics", finding="ok"),
            Observation(source="es", signal="logs", finding="ok"),
        ]
        notes = reflect_on_observations(plan, obs)
        assert calculate_confidence(plan, obs, notes) == 1.0

    def test_answer_no_evidence(self):
        plan = Plan(
            intent="error_investigation",
            service=None,
            time_range="15m",
            required_signals=["logs"],
        )
        notes = reflect_on_observations(plan, [])
        text = compose_deterministic_answer(plan, [], notes)
        assert "No usable evidence" in text
        assert "logs" in text

    def test_snapshot_coverage(self):
        plan = Plan(intent="error_investigation", service=None, time_range="15m")
        obs = [Observation(source="prom", signal="metrics", finding="ok")]
        snap = build_snapshot(plan, obs, 0.5)
        assert snap.coverage == {"metrics": ["prom"]}
        assert snap.confidence == 0.5


# ──────────────────────────────────────────────────────────────────────── #
# Agent end-to-end (with fake adapters, no LLM)
# ──────────────────────────────────────────────────────────────────────── #
class TestAgentLoop:
    def test_full_loop_no_llm(self):
        agent = AyosaAgent(dispatcher=ToolDispatcher(FAKE_ADAPTERS))
        agent_in = _input("p99 latency for checkout")
        result = agent.run(agent_in)

        assert result.intent == "latency_issues"
        assert result.plan.should_query_metrics is True
        # prometheus was dispatched, alertmanager skipped
        statuses = {s.tool: s.status for s in result.tool_steps}
        assert statuses.get("prometheus") == "done"
        assert any(o.source == "prometheus" for o in result.observations)
        assert result.llm_used is False
        assert result.llm_analysis is None
        # Snapshot built from observations
        assert result.snapshot is not None

    def test_session_history_persisted(self):
        ctx = ContextManager()
        agent = AyosaAgent(
            dispatcher=ToolDispatcher(FAKE_ADAPTERS), context=ctx
        )
        agent.run(_input("any active alerts?"))
        agent.run(_input("p99 latency"))
        history = ctx.history("t-1")
        assert len(history) == 2
        assert history[0].intent == "active_alerts"
        assert history[1].intent == "latency_issues"

    def test_llm_skipped_when_no_evidence(self, monkeypatch):
        """LLM must not be called when there's zero ok evidence."""
        agent = AyosaAgent(
            dispatcher=ToolDispatcher({"splunk": _FakeFailing}),
            synthesizer=Synthesizer(),
        )
        called = {"n": 0}

        def fake_invoke(self, llm_cfg, result):  # noqa: ARG001
            called["n"] += 1
            return {"executive_summary": "should not run"}

        monkeypatch.setattr(Synthesizer, "_invoke_llm", fake_invoke)

        agent_in = AgentInput(
            message="any errors?",
            service="checkout",
            time_range="15m",
            tools=[AgentToolConfig(tool="splunk", base_url="http://x")],
            llm={"enabled": True, "provider": "anthropic", "api_key": "sk-test"},
            session_id="t-2",
        )
        result = agent.run(agent_in)
        assert called["n"] == 0
        assert result.llm_used is False

    def test_llm_invoked_when_evidence_present(self, monkeypatch):
        agent = AyosaAgent(dispatcher=ToolDispatcher(FAKE_ADAPTERS))

        def fake_invoke(self, llm_cfg, result):  # noqa: ARG001
            return {
                "executive_summary": "LLM summary",
                "reasoning": "",
                "missing_information": [],
                "recommended_next_steps": [],
            }

        monkeypatch.setattr(Synthesizer, "_invoke_llm", fake_invoke)

        agent_in = AgentInput(
            message="p99 latency for checkout",
            service="checkout",
            time_range="15m",
            tools=[AgentToolConfig(tool="prometheus", base_url="http://p")],
            llm={"enabled": True, "provider": "anthropic", "api_key": "sk-test"},
            session_id="t-3",
        )
        result = agent.run(agent_in)
        assert result.llm_used is True
        assert result.final_response == "LLM summary"


# ──────────────────────────────────────────────────────────────────────── #
# Async variant — driven via asyncio.run to avoid plugin dependency
# ───────────────────────────────────────────────────────────────────── #
def test_async_run_matches_sync():
    import asyncio

    agent = AyosaAgent(dispatcher=ToolDispatcher(FAKE_ADAPTERS))
    agent_in = _input("any active alerts?")
    result = asyncio.run(agent.arun(agent_in))
    assert result.intent == "active_alerts"
