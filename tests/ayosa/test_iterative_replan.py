"""Step 13 tests — LLM-driven iterative tool calling.

Covers ``build_llm_replan`` in isolation and the end-to-end
``AyosaAgent.run`` loop with ``max_iterations >= 3`` so the LLM-driven
iterative re-plan can fire.

All tests mock ``select_tools_llm`` so no real LLM call is made.
"""

from __future__ import annotations

from typing import Any

import pytest

from accelerators.ayosa.agent import AyosaAgent
from accelerators.ayosa.agent.iterative_replanner import build_llm_replan
from accelerators.ayosa.agent.schemas import (
    AgentAIConfig,
    AgentInput,
    AgentToolConfig,
    Observation,
    Plan,
)
from accelerators.ayosa.agent.tool_dispatcher import ToolDispatcher
from accelerators.ayosa.agent.tool_selector_llm import SelectorResult


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _plan(**over: Any) -> Plan:
    base = dict(
        intent="error_investigation",
        service="payments",
        time_range="15m",
        required_signals=["logs", "metrics"],
        selected_tools=["loki", "prometheus"],
    )
    base.update(over)
    return Plan(**base)


def _agent_input(*tools: str, llm_enabled: bool = True, message: str = "Why is payments slow?") -> AgentInput:
    return AgentInput(
        message=message,
        service="payments",
        time_range="15m",
        tools=[AgentToolConfig(tool=t, base_url=f"http://{t}") for t in tools],
        llm=AgentAIConfig(
            enabled=llm_enabled,
            provider="anthropic",
            api_key="sk-test",
            model="claude-sonnet-4-6",
        ),
    )


def _selector(tool_args: dict[str, dict[str, Any]], reasoning: str = "refined") -> SelectorResult:
    return SelectorResult(
        tool_names=list(tool_args.keys()),
        reasoning=reasoning,
        provider="anthropic",
        model="claude-sonnet-4-6",
        tool_args={k: dict(v) for k, v in tool_args.items()},
    )


# --------------------------------------------------------------------------- #
# Pure helper: build_llm_replan
# --------------------------------------------------------------------------- #
class TestBuildLLMReplan:
    def test_returns_none_when_llm_disabled(self, monkeypatch):
        ai = _agent_input("prometheus", llm_enabled=False)
        out = build_llm_replan(
            original_plan=_plan(),
            agent_input=ai,
            observations=[],
            already_dispatched={"prometheus"},
        )
        assert out is None

    def test_returns_none_when_llm_config_missing(self):
        ai = AgentInput(
            message="x",
            tools=[AgentToolConfig(tool="prometheus", base_url="http://p")],
        )
        out = build_llm_replan(
            original_plan=_plan(),
            agent_input=ai,
            observations=[],
            already_dispatched=set(),
        )
        assert out is None

    def test_returns_none_when_no_tools_configured(self, monkeypatch):
        ai = AgentInput(
            message="x",
            llm=AgentAIConfig(enabled=True, provider="anthropic", api_key="k"),
        )
        out = build_llm_replan(
            original_plan=_plan(),
            agent_input=ai,
            observations=[],
            already_dispatched=set(),
        )
        assert out is None

    def test_returns_none_when_selector_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            "accelerators.ayosa.agent.iterative_replanner.select_tools_llm",
            lambda **_kw: None,
        )
        ai = _agent_input("prometheus")
        out = build_llm_replan(
            original_plan=_plan(),
            agent_input=ai,
            observations=[],
            already_dispatched=set(),
        )
        assert out is None

    def test_picks_new_tool_not_yet_dispatched(self, monkeypatch):
        captured: dict[str, Any] = {}

        def fake(**kw):
            captured.update(kw)
            return _selector({"alertmanager": {
                "service": "payments", "time_range": None,
                "query": None, "reason": "check active alerts",
            }})

        monkeypatch.setattr(
            "accelerators.ayosa.agent.iterative_replanner.select_tools_llm",
            fake,
        )
        ai = _agent_input("prometheus", "alertmanager")
        out = build_llm_replan(
            original_plan=_plan(),
            agent_input=ai,
            observations=[Observation(
                source="prometheus", signal="metrics", finding="latency p99 normal",
            )],
            already_dispatched={"prometheus"},
        )
        assert out is not None
        assert out.selected_tools == ["alertmanager"]
        assert out.tool_args == {
            "alertmanager": {
                "service": "payments", "time_range": None,
                "query": None, "reason": "check active alerts",
            }
        }
        assert (out.selection_meta or {}).get("mode") == "llm_iterative"
        # The augmented message includes the prior finding summary.
        assert "Prior tool observations" in captured["message"]
        assert "latency p99 normal" in captured["message"]

    def test_picks_already_dispatched_tool_only_when_refined_query(self, monkeypatch):
        # No refined query → drop the entry.
        monkeypatch.setattr(
            "accelerators.ayosa.agent.iterative_replanner.select_tools_llm",
            lambda **_kw: _selector({"prometheus": {
                "service": None, "time_range": None, "query": None,
                "reason": "re-run",
            }}),
        )
        ai = _agent_input("prometheus")
        out = build_llm_replan(
            original_plan=_plan(),
            agent_input=ai,
            observations=[],
            already_dispatched={"prometheus"},
        )
        assert out is None

    def test_picks_already_dispatched_tool_with_refined_query(self, monkeypatch):
        monkeypatch.setattr(
            "accelerators.ayosa.agent.iterative_replanner.select_tools_llm",
            lambda **_kw: _selector({"prometheus": {
                "service": "payments", "time_range": "5m",
                "query": 'histogram_quantile(0.99, rate(http_request_duration_seconds_bucket{service="payments"}[5m]))',
                "reason": "drill into p99",
            }}),
        )
        ai = _agent_input("prometheus")
        out = build_llm_replan(
            original_plan=_plan(),
            agent_input=ai,
            observations=[Observation(
                source="prometheus", signal="metrics",
                finding="generic probe inconclusive",
            )],
            already_dispatched={"prometheus"},
        )
        assert out is not None
        assert out.selected_tools == ["prometheus"]
        assert "histogram_quantile" in out.tool_args["prometheus"]["query"]


# --------------------------------------------------------------------------- #
# AyosaAgent.run end-to-end
# --------------------------------------------------------------------------- #
class _PromAdapterFactory:
    """Returns a generic-finding adapter on the first call and a refined-
    finding adapter on subsequent calls so we can prove the LLM-driven
    second iteration ran with a refined query.
    """
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def __call__(self, base_url: str, auth_token: str | None = None):
        outer = self

        class _Adapter:
            def investigate(self, **kwargs):
                outer.calls.append(kwargs)
                plan = kwargs.get("plan") or {}
                active = (plan.get("active_tool_args") or {}) if isinstance(plan, dict) else {}
                q = active.get("query")
                if q:
                    return [{
                        "source": "prometheus", "signal": "metrics",
                        "finding": f"refined query found p99=950ms (q={q})",
                        "status": "ok", "query": q,
                    }]
                return [{
                    "source": "prometheus", "signal": "metrics",
                    "finding": "generic probe inconclusive", "status": "ok",
                }]
        return _Adapter()


class _LokiAdapter:
    def __init__(self, base_url: str, auth_token: str | None = None) -> None:
        pass

    def investigate(self, **kwargs):
        return [{
            "source": "loki", "signal": "logs",
            "finding": "no errors in last 15m", "status": "ok",
        }]


def _make_agent(adapters: dict, max_iterations: int = 3) -> AyosaAgent:
    return AyosaAgent(
        dispatcher=ToolDispatcher(adapters=adapters),
        max_iterations=max_iterations,
    )


class TestAgentMultiIteration:
    def test_llm_iterative_replan_runs_second_pass(self, monkeypatch):
        # First selector call (planner) → pick prometheus + loki.
        # Second selector call (iterative replan) → refine prometheus query.
        replan_payload = _selector({"prometheus": {
            "service": "payments", "time_range": "5m",
            "query": "histogram_quantile(0.99, rate(http_request_duration_seconds_bucket[5m]))",
            "reason": "drill p99",
        }}, reasoning="refine p99 latency")
        planner_payload = _selector({
            "prometheus": {"service": "payments", "time_range": "15m",
                           "query": None, "reason": "probe"},
            "loki": {"service": "payments", "time_range": "15m",
                     "query": None, "reason": "probe"},
        }, reasoning="initial pick")

        call_count = {"n": 0}

        def fake_select(**kw):
            call_count["n"] += 1
            return planner_payload if call_count["n"] == 1 else replan_payload

        # Patch both callsites — planner and iterative replanner.
        monkeypatch.setattr(
            "accelerators.ayosa.agent.planner.select_tools_llm", fake_select,
        )
        monkeypatch.setattr(
            "accelerators.ayosa.agent.iterative_replanner.select_tools_llm",
            fake_select,
        )

        prom_factory = _PromAdapterFactory()
        agent = _make_agent(
            adapters={"prometheus": prom_factory, "loki": _LokiAdapter},
            max_iterations=2,
        )
        ai = _agent_input("prometheus", "loki")
        # Disable LLM intent router to keep the test focused; planner +
        # iterative replan still use the LLM mock above.
        result = agent.run(ai)

        # ≥ 2 iterations executed.
        assert result.iterations >= 2
        # Prometheus invoked twice: once without query, once with refined query.
        assert len(prom_factory.calls) == 2
        active_args_seen = []
        for call in prom_factory.calls:
            plan = call.get("plan") or {}
            active_args_seen.append((plan.get("active_tool_args") or {}).get("query"))
        assert active_args_seen[0] in (None, "")
        assert "histogram_quantile" in (active_args_seen[1] or "")
        # Step from iteration 1 exists and is tagged correctly.
        iter1_steps = [s for s in result.tool_steps if s.iteration == 1]
        assert any(s.tool.lower() == "prometheus" for s in iter1_steps)
        assert result.replan_reason and "LLM iterative re-plan" in result.replan_reason

    def test_falls_back_to_deterministic_when_llm_replan_returns_none(self, monkeypatch):
        # Planner picks only prometheus; the deterministic replan should
        # then add loki to cover the 'logs' gap.
        planner_payload = _selector({
            "prometheus": {"service": "payments", "time_range": "15m",
                           "query": None, "reason": "probe"},
        }, reasoning="initial pick")

        def fake_select(**kw):
            return planner_payload

        monkeypatch.setattr(
            "accelerators.ayosa.agent.planner.select_tools_llm", fake_select,
        )
        # Iterative replan returns None → forces fallback.
        monkeypatch.setattr(
            "accelerators.ayosa.agent.iterative_replanner.select_tools_llm",
            lambda **_kw: None,
        )

        prom_factory = _PromAdapterFactory()
        agent = _make_agent(
            adapters={"prometheus": prom_factory, "loki": _LokiAdapter},
            max_iterations=2,
        )
        ai = _agent_input("prometheus", "loki", message="latest error in payments")
        result = agent.run(ai)

        # Loki ran in iteration 1 via the deterministic gap-filler.
        loki_iter1 = [
            s for s in result.tool_steps
            if s.tool.lower() == "loki" and s.iteration == 1
        ]
        assert loki_iter1
        assert result.replan_reason and "gaps" in result.replan_reason.lower()
