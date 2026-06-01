"""Contract tests for the /api/ayosa/chat agent-mode integration.

These tests exercise the bridge layer end-to-end without hitting any
network. They prove:

  * agent_mode=False preserves the deterministic path (AyosaService).
  * agent_mode=True routes through the AyosaAgent backbone.
  * The response shape contains every contract field.
  * LLM failure is non-fatal (deterministic answer still returned).
  * No generic RCA is fabricated for non-investigation intents.
"""

from __future__ import annotations

import pytest

from accelerators.ayosa.agent import AyosaAgent
from accelerators.ayosa.agent.tool_dispatcher import ToolDispatcher
from accelerators.ayosa.agent_bridge import run_agent_chat
from accelerators.ayosa.models import (
    AyosaAIConfig,
    AyosaChatRequest,
    AyosaToolConfig,
)


# ──────────────────────────────────────────────────────────────────────── #
# Fakes
# ──────────────────────────────────────────────────────────────────────── #
class _FakePromAdapter:
    """Returns a single OK metrics finding."""

    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        self.base_url = base_url

    def investigate(self, service, time_range, message, plan=None):  # noqa: ARG002
        return [
            {
                "source": "prometheus",
                "signal": "metrics",
                "finding": "rate(http_5xx) = 0.01 over 5m",
                "query": "rate(http_requests_total[5m])",
                "status": "ok",
                "raw": {"timestamp": "2026-06-01T10:00:00Z"},
            }
        ]


class _FakeAlertmanagerAdapter:
    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        self.base_url = base_url

    def investigate(self, service, time_range, message, plan=None):  # noqa: ARG002
        return [
            {
                "source": "alertmanager",
                "signal": "alerts",
                "finding": "1 firing alert: HighErrorRate",
                "query": None,
                "status": "ok",
                "raw": {"severity": "critical"},
            }
        ]


def _agent_with(adapters: dict[str, type]) -> AyosaAgent:
    return AyosaAgent(dispatcher=ToolDispatcher(adapters))


def _req(message: str, *, agent_mode: bool, ai: AyosaAIConfig | None = None,
         tools: list[AyosaToolConfig] | None = None) -> AyosaChatRequest:
    return AyosaChatRequest(
        message=message,
        service="payments",
        time_range="15m",
        tools=tools
        or [AyosaToolConfig(tool="prometheus", base_url="http://prom.local")],
        ai=ai,
        agent_mode=agent_mode,
    )


# ──────────────────────────────────────────────────────────────────────── #
# Request schema
# ──────────────────────────────────────────────────────────────────────── #
class TestRequestSchema:
    def test_agent_mode_defaults_false(self):
        req = AyosaChatRequest(
            message="hi",
            tools=[AyosaToolConfig(tool="prometheus", base_url="http://x")],
        )
        assert req.agent_mode is False

    def test_agent_mode_accepts_true(self):
        req = AyosaChatRequest(
            message="hi",
            tools=[AyosaToolConfig(tool="prometheus", base_url="http://x")],
            agent_mode=True,
        )
        assert req.agent_mode is True


# ──────────────────────────────────────────────────────────────────────── #
# Response contract — agent mode
# ──────────────────────────────────────────────────────────────────────── #
REQUIRED_RESPONSE_KEYS = {
    "mode",
    "intent",
    "plan",
    "tool_steps",
    "observations",
    "answer",
    "charts",
    "evidence",
    "timeline",
    "incident_snapshot",
    "missing_signals",
    "suggested_actions",
}


class TestAgentResponseShape:
    def test_response_has_all_contract_fields(self):
        agent = _agent_with({"prometheus": _FakePromAdapter})
        out = run_agent_chat(
            _req("what is the p99 latency?", agent_mode=True), agent=agent
        )
        missing = REQUIRED_RESPONSE_KEYS - set(out.keys())
        assert not missing, f"Missing contract fields: {missing}"
        assert out["mode"] == "agent"
        assert out["intent"] == "latency_issues"
        assert isinstance(out["plan"], dict)
        assert isinstance(out["tool_steps"], list)
        assert isinstance(out["observations"], list)
        assert isinstance(out["evidence"], list)
        assert isinstance(out["timeline"], list)
        assert isinstance(out["missing_signals"], list)
        assert isinstance(out["suggested_actions"], list)
        assert isinstance(out["charts"], list)

    def test_tool_steps_emitted(self):
        agent = _agent_with({"prometheus": _FakePromAdapter})
        out = run_agent_chat(
            _req("p99 latency?", agent_mode=True), agent=agent
        )
        assert len(out["tool_steps"]) >= 1
        assert any(
            s["tool"] == "prometheus" and s["status"] == "done"
            for s in out["tool_steps"]
        )

    def test_observations_match_evidence(self):
        agent = _agent_with({"prometheus": _FakePromAdapter})
        out = run_agent_chat(
            _req("p99 latency?", agent_mode=True), agent=agent
        )
        assert out["observations"] == out["evidence"]
        assert out["observations"][0]["source"] == "prometheus"


# ──────────────────────────────────────────────────────────────────────── #
# Mode selection — agent_mode=False preserves deterministic path
# ──────────────────────────────────────────────────────────────────────── #
class TestModeSelection:
    def test_deterministic_path_used_when_agent_mode_false(self, monkeypatch):
        """The bridge MUST NOT be invoked when agent_mode is False."""
        from accelerators.ayosa import router as router_module

        called = {"agent": False, "service": False}

        def _bridge_spy(*args, **kwargs):
            called["agent"] = True
            return {}

        class _ServiceSpy:
            def investigate(self, request):
                called["service"] = True
                return {
                    "answer": "deterministic-answer",
                    "service": request.service,
                    "time_range": request.time_range,
                    "confidence": 0.5,
                    "probable_root_cause": "",
                    "impact": "",
                    "detected_patterns": [],
                    "timeline": [],
                    "related_artifacts": [],
                    "evidence": [],
                    "suggested_actions": [],
                    "signal_coverage": {},
                    "missing_signals": [],
                    "intent": "service_health",
                }

        monkeypatch.setattr(router_module, "run_agent_chat", _bridge_spy)
        monkeypatch.setattr(router_module, "AyosaService", _ServiceSpy)

        out = router_module.chat(
            _req("what is the env health?", agent_mode=False)
        )
        assert called["service"] is True
        assert called["agent"] is False
        assert out["mode"] == "deterministic"

    def test_agent_path_used_when_agent_mode_true(self, monkeypatch):
        from accelerators.ayosa import router as router_module

        called = {"agent": False, "service": False}

        def _bridge_spy(request):
            called["agent"] = True
            return {"mode": "agent", "answer": "ok"}

        class _ServiceSpy:
            def investigate(self, request):  # pragma: no cover - must not run
                called["service"] = True
                return {}

        monkeypatch.setattr(router_module, "run_agent_chat", _bridge_spy)
        monkeypatch.setattr(router_module, "AyosaService", _ServiceSpy)

        out = router_module.chat(
            _req("p99 latency?", agent_mode=True)
        )
        assert called["agent"] is True
        assert called["service"] is False
        assert out["mode"] == "agent"


# ──────────────────────────────────────────────────────────────────────── #
# Requirement #6 — LLM failure must not break the agent response
# ──────────────────────────────────────────────────────────────────────── #
class TestLLMIsAdditive:
    def test_llm_failure_leaves_deterministic_answer(self, monkeypatch):
        from accelerators.ayosa.agent import synthesizer as syn

        def _boom(self, llm_cfg, result):
            raise RuntimeError("simulated LLM outage")

        # The Synthesizer wraps its LLM call in try/except, but we patch
        # it to raise *outside* that wrapper to prove the bridge layer
        # also tolerates a hard failure (defence in depth).
        original = syn.Synthesizer._invoke_llm
        try:
            syn.Synthesizer._invoke_llm = _boom  # type: ignore[assignment]
            agent = _agent_with({"prometheus": _FakePromAdapter})
            ai = AyosaAIConfig(enabled=True, provider="anthropic", api_key="x")
            out = run_agent_chat(
                _req("p99 latency?", agent_mode=True, ai=ai),
                agent=agent,
            )
        finally:
            syn.Synthesizer._invoke_llm = original  # type: ignore[assignment]

        # Deterministic answer still present, no LLM analysis attached.
        assert out["answer"]
        assert out["llm_analysis"] is None
        assert out["mode"] == "agent"
        assert out["observations"], "evidence must still be present"

    def test_llm_disabled_returns_deterministic_only(self):
        agent = _agent_with({"prometheus": _FakePromAdapter})
        out = run_agent_chat(
            _req("p99 latency?", agent_mode=True, ai=None),
            agent=agent,
        )
        assert out["llm_analysis"] is None
        assert out["answer"]


# ──────────────────────────────────────────────────────────────────────── #
# Requirement #7 — No generic RCA unless intent requires investigation
# ──────────────────────────────────────────────────────────────────────── #
class TestNoGenericRCA:
    def test_current_time_intent_has_no_rca(self):
        agent = _agent_with({"prometheus": _FakePromAdapter})
        out = run_agent_chat(
            _req("what time is it?", agent_mode=True), agent=agent
        )
        assert out["intent"] == "current_time"
        assert out["probable_root_cause"] == ""
        assert out["impact"] == ""
        assert out["incident_snapshot"] is None

    def test_dashboard_lookup_intent_has_no_rca(self):
        agent = _agent_with({"prometheus": _FakePromAdapter})
        out = run_agent_chat(
            _req("show me a dashboard", agent_mode=True), agent=agent
        )
        # dashboard_lookup is not in _INVESTIGATION_INTENTS
        assert out["intent"] == "dashboard_lookup"
        assert out["probable_root_cause"] == ""
        assert out["impact"] == ""
        assert out["incident_snapshot"] is None

    def test_investigation_intent_may_populate_snapshot(self):
        agent = _agent_with({"alertmanager": _FakeAlertmanagerAdapter})
        tools = [AyosaToolConfig(tool="alertmanager", base_url="http://am.local")]
        out = run_agent_chat(
            _req("any active alerts?", agent_mode=True, tools=tools),
            agent=agent,
        )
        assert out["intent"] == "active_alerts"
        # We DON'T assert root_cause is non-empty — only the LLM fills it.
        # We DO assert the snapshot is present because evidence exists.
        assert out["incident_snapshot"] is not None
        assert "coverage" in out["incident_snapshot"]


# ──────────────────────────────────────────────────────────────────────── #
# Requirement #4 — agent must walk every loop stage
# ──────────────────────────────────────────────────────────────────────── #
class TestAgentLoopStages:
    def test_loop_runs_all_phases(self):
        agent = _agent_with({"prometheus": _FakePromAdapter})
        out = run_agent_chat(
            _req("p99 latency?", agent_mode=True), agent=agent
        )
        # classify → intent populated
        assert out["intent"] == "latency_issues"
        # plan → metrics required + selected
        assert "metrics" in out["plan"]["required_signals"]
        assert "prometheus" in out["plan"]["selected_tools"]
        # execute → at least one tool step done
        assert any(s["status"] == "done" for s in out["tool_steps"])
        # observe → evidence collected
        assert out["evidence"]
        # reflect+synthesize → confidence + answer
        assert out["confidence"] > 0
        assert out["answer"]
