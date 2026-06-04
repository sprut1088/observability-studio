"""Step 25 — integration tests for the tool-use loop wiring.

These tests verify that the ``use_tool_use_loop`` flag actually routes
``AyosaAgent.run`` (and, by extension, the synchronous chat endpoint)
through the Anthropic tool-use path AND that graceful fallback happens
when the flag is set but the prerequisites are missing.

They use the same fake ``anthropic.Anthropic`` client pattern as
``test_tool_use_loop.py`` and monkeypatch
``build_anthropic_client`` so no network or real LLM is touched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from accelerators.ayosa.agent import AyosaAgent
from accelerators.ayosa.agent import __init__ as agent_init  # noqa: F401 — for clarity
import accelerators.ayosa.agent as agent_pkg
from accelerators.ayosa.agent.schemas import (
    AgentAIConfig,
    AgentInput,
    AgentToolConfig,
)
from accelerators.ayosa.agent.tool_dispatcher import ToolDispatcher
from accelerators.ayosa.agent import tool_use_loop as tul_module


@pytest.fixture(autouse=True)
def _stub_intent_classifier(monkeypatch):
    """Avoid hitting the network in the LLM intent classifier.

    The intent router will otherwise try to reach Anthropic whenever an
    api_key is present, which slows tests down and breaks them on
    machines without network access. Replace with a deterministic stub.
    """
    def _fake_classify(message, llm_config=None, service_hint=None):  # noqa: ARG001
        return ("latency_issues", {"source": "fake", "confidence": 1.0})

    monkeypatch.setattr(
        "accelerators.ayosa.agent.classify_intent_smart", _fake_classify
    )


# ──────────────────────────────────────────────────────────────────────── #
# Fakes (mirror test_tool_use_loop.py shape)
# ──────────────────────────────────────────────────────────────────────── #
class _FakePromAdapter:
    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        self.base_url = base_url

    def investigate(self, service, time_range, message, plan=None):  # noqa: ARG002
        return [
            {
                "source": "prometheus",
                "signal": "metrics",
                "finding": f"http_5xx{{service='{service}'}} = 0.01",
                "status": "ok",
                "raw": {},
            }
        ]


@dataclass
class _FakeMessage:
    content: list[Any]
    stop_reason: str = "end_turn"


@dataclass
class _FakeBlock:
    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] = field(default_factory=dict)


class _FakeMessages:
    def __init__(self, scripted: list[_FakeMessage]) -> None:
        self._scripted = list(scripted)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._scripted:
            raise AssertionError("scripted client exhausted")
        return self._scripted.pop(0)


class _FakeAnthropicClient:
    def __init__(self, scripted: list[_FakeMessage]) -> None:
        self.messages = _FakeMessages(scripted)


# ──────────────────────────────────────────────────────────────────────── #
# Helpers
# ──────────────────────────────────────────────────────────────────────── #
def _ai(*, enabled=True, key="sk-test", flag=True, provider="anthropic"):
    return AgentAIConfig(
        enabled=enabled,
        provider=provider,
        api_key=key,
        use_tool_use_loop=flag,
    )


def _input(llm: AgentAIConfig | None) -> AgentInput:
    return AgentInput(
        message="why is checkout slow?",
        service="checkout",
        time_range="15m",
        tools=[
            AgentToolConfig(
                tool="prometheus",
                base_url="http://prom.local",
                auth_token="t",
            )
        ],
        llm=llm,
    )


def _agent() -> AyosaAgent:
    return AyosaAgent(
        dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}),
        max_iterations=2,
    )


# ──────────────────────────────────────────────────────────────────────── #
# Tests — routing
# ──────────────────────────────────────────────────────────────────────── #
class TestFlagRouting:
    def test_flag_on_with_client_routes_through_tool_use_loop(
        self, monkeypatch
    ):
        scripted = [
            _FakeMessage(
                content=[
                    _FakeBlock(
                        type="tool_use",
                        id="t1",
                        name="prometheus_investigate",
                        input={"service": "checkout", "time_range": "15m"},
                    )
                ],
                stop_reason="tool_use",
            ),
            _FakeMessage(
                content=[
                    _FakeBlock(
                        type="text",
                        text="Prometheus shows elevated 5xx on checkout.",
                    )
                ],
                stop_reason="end_turn",
            ),
        ]
        client = _FakeAnthropicClient(scripted)
        monkeypatch.setattr(
            agent_pkg, "build_anthropic_client", lambda inp: client
        )

        result = _agent().run(_input(_ai()))

        # The intent classifier still runs (deterministic stub returns
        # "latency_issues" for this message). What the tool-use branch
        # is responsible for is the selection_meta marker AND the
        # llm_analysis blob — those identify the run as tool-use-driven.
        assert (result.plan.selection_meta or {}).get("mode") == "tool_use_llm"
        assert (result.llm_analysis or {}).get("mode") == "tool_use_loop"
        assert any(s.tool.lower() == "prometheus" for s in result.tool_steps)
        assert result.observations, "expected at least one observation"
        assert "checkout" in result.final_response.lower()
        # Two LLM round-trips: initial tool_use + follow-up text.
        assert len(client.messages.calls) == 2

    def test_flag_off_uses_deterministic_path(self, monkeypatch):
        # If routing accidentally hit the tool-use loop, the fake would
        # raise (no scripted responses). So a successful run proves the
        # deterministic planner ran instead.
        def _boom(_):
            raise AssertionError("client factory must not be called")

        monkeypatch.setattr(agent_pkg, "build_anthropic_client", _boom)
        result = _agent().run(_input(_ai(flag=False)))

        assert result.intent != "tool_use"
        assert (result.plan.selection_meta or {}).get("mode") != "tool_use_llm"

    def test_missing_api_key_falls_back_silently(self, monkeypatch):
        # build_anthropic_client should return None when the api_key is
        # blank — the agent must NOT raise and must fall through to the
        # deterministic planner.
        monkeypatch.setattr(
            agent_pkg, "build_anthropic_client", lambda inp: None
        )
        result = _agent().run(_input(_ai(key="")))

        # Eligibility check (`should_use_tool_use_loop`) already returns
        # False for an empty api_key, so build_anthropic_client wouldn't
        # even be called. Either way, the deterministic path must produce
        # a valid AgentResult.
        assert result is not None
        assert result.plan is not None
        assert (result.plan.selection_meta or {}).get("mode") != "tool_use_llm"

    def test_flag_on_non_anthropic_provider_falls_back(self, monkeypatch):
        def _boom(_):
            raise AssertionError("client factory must not be called for non-anthropic")

        monkeypatch.setattr(agent_pkg, "build_anthropic_client", _boom)
        result = _agent().run(_input(_ai(provider="azure")))

        assert (result.plan.selection_meta or {}).get("mode") != "tool_use_llm"

    def test_client_factory_returns_none_then_fallback(self, monkeypatch):
        # Eligibility passes but factory returns None (e.g. anthropic
        # package not installed). The agent should fall through cleanly.
        monkeypatch.setattr(
            agent_pkg, "build_anthropic_client", lambda inp: None
        )
        result = _agent().run(_input(_ai()))

        assert result is not None
        assert (result.plan.selection_meta or {}).get("mode") != "tool_use_llm"


# ──────────────────────────────────────────────────────────────────────── #
# Tests — should_use_tool_use_loop eligibility
# ──────────────────────────────────────────────────────────────────────── #
class TestEligibility:
    def test_all_conditions_met_returns_true(self):
        assert tul_module.should_use_tool_use_loop(_input(_ai())) is True

    def test_llm_disabled_returns_false(self):
        assert tul_module.should_use_tool_use_loop(_input(_ai(enabled=False))) is False

    def test_flag_off_returns_false(self):
        assert tul_module.should_use_tool_use_loop(_input(_ai(flag=False))) is False

    def test_blank_api_key_returns_false(self):
        assert tul_module.should_use_tool_use_loop(_input(_ai(key=""))) is False

    def test_non_anthropic_provider_returns_false(self):
        assert tul_module.should_use_tool_use_loop(_input(_ai(provider="azure"))) is False

    def test_no_tools_returns_false(self):
        inp = AgentInput(
            message="hi",
            service="x",
            time_range="15m",
            tools=[],
            llm=_ai(),
        )
        assert tul_module.should_use_tool_use_loop(inp) is False

    def test_no_llm_returns_false(self):
        assert tul_module.should_use_tool_use_loop(_input(None)) is False


# ──────────────────────────────────────────────────────────────────────── #
# Tests — result synthesis
# ──────────────────────────────────────────────────────────────────────── #
class TestResultSynthesis:
    def test_synthesized_result_carries_iterations_and_replan_reason(
        self, monkeypatch
    ):
        scripted = [
            _FakeMessage(
                content=[
                    _FakeBlock(
                        type="tool_use",
                        id="t1",
                        name="prometheus_investigate",
                        input={"service": "checkout"},
                    )
                ],
                stop_reason="tool_use",
            ),
            _FakeMessage(
                content=[
                    _FakeBlock(
                        type="tool_use",
                        id="t2",
                        name="prometheus_investigate",
                        input={"service": "checkout", "query_focus": "5xx"},
                    )
                ],
                stop_reason="tool_use",
            ),
            _FakeMessage(
                content=[_FakeBlock(type="text", text="Done.")],
                stop_reason="end_turn",
            ),
        ]
        client = _FakeAnthropicClient(scripted)
        monkeypatch.setattr(
            agent_pkg, "build_anthropic_client", lambda inp: client
        )

        agent = AyosaAgent(
            dispatcher=ToolDispatcher({"prometheus": _FakePromAdapter}),
            max_iterations=3,
        )
        result = agent.run(_input(_ai()))

        assert result.iterations >= 2
        assert result.replan_reason is not None
        assert "tool-use loop" in result.replan_reason.lower()
        assert result.llm_used is True
        assert result.llm_analysis.get("mode") == "tool_use_loop"
