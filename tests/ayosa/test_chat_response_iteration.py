"""Step 14 — backend serialization of ``tool_steps[*].iteration``.

The frontend groups tool steps by iteration to surface the LLM's
multi-pass investigation trajectory; this asserts the bridge no longer
drops the field that ``AyosaAgent.run`` stamps on each step.
"""

from __future__ import annotations

from types import SimpleNamespace

from accelerators.ayosa.agent.schemas import (
    AgentResult,
    Plan,
    ToolStep,
)
from accelerators.ayosa.agent_bridge import _agent_result_to_chat_response


def _result(*steps: ToolStep, iterations: int = 1, replan_reason: str | None = None) -> AgentResult:
    return AgentResult(
        intent="error_investigation",
        plan=Plan(
            intent="error_investigation",
            service="payments",
            time_range="15m",
            required_signals=["logs"],
            selected_tools=["loki"],
        ),
        tool_steps=list(steps),
        iterations=iterations,
        replan_reason=replan_reason,
    )


def _request(service: str = "payments") -> SimpleNamespace:
    return SimpleNamespace(service=service, message="x")


def test_chat_response_carries_iteration_per_step():
    steps = [
        ToolStep(index=0, tool="prometheus", label="Probe", status="done", iteration=0),
        ToolStep(index=1, tool="loki",       label="Probe", status="done", iteration=0),
        ToolStep(index=2, tool="alertmanager", label="Probe", status="done", iteration=1),
    ]
    payload = _agent_result_to_chat_response(
        _result(*steps, iterations=2, replan_reason="LLM iterative re-plan: alertmanager"),
        _request(),
    )
    out_steps = payload["tool_steps"]
    assert [s["iteration"] for s in out_steps] == [0, 0, 1]
    assert payload["iterations"] == 2
    assert "iterative" in payload["replan_reason"]


def test_chat_response_iteration_defaults_to_zero_for_single_pass():
    payload = _agent_result_to_chat_response(
        _result(ToolStep(index=0, tool="prometheus", label="Probe", status="done")),
        _request(),
    )
    assert payload["tool_steps"][0]["iteration"] == 0
    assert payload["iterations"] == 1
    assert payload["replan_reason"] is None
