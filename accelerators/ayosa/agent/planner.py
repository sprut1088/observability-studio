"""Planner — wraps the existing `build_ayosa_plan` into an OO-friendly
component that the agent can compose.

This is a thin, additive adapter; the underlying planning rules stay in
`accelerators.ayosa.service.build_ayosa_plan` to guarantee parity with
the existing /api/ayosa/chat behaviour.
"""

from __future__ import annotations

from typing import Any

from accelerators.ayosa.service import build_ayosa_plan
from accelerators.ayosa.agent.schemas import AgentInput, Plan


class Planner:
    """Builds a `Plan` from an `AgentInput`."""

    def build(self, agent_input: AgentInput, intent_hint: str | None = None) -> Plan:
        """Construct the plan. `intent_hint` is accepted for symmetry with
        the agent loop but recomputed inside `build_ayosa_plan` for safety.
        """
        plan_dict = build_ayosa_plan(
            message=agent_input.message,
            service=agent_input.service,
            time_range=agent_input.time_range,
            available_tools=[_tool_shim(t) for t in agent_input.tools],
        )
        # `build_ayosa_plan` overrides intent from the message anyway.
        if intent_hint and not plan_dict.get("intent"):
            plan_dict["intent"] = intent_hint
        return _dict_to_plan(plan_dict)


# ──────────────────────────────────────────────────────────────────────── #
# Helpers — kept module-private and unit-testable
# ──────────────────────────────────────────────────────────────────────── #
class _ToolShim:
    """Duck-typed shim for build_ayosa_plan which expects `.tool` attribute."""

    __slots__ = ("tool",)

    def __init__(self, name: str) -> None:
        self.tool = name


def _tool_shim(t: Any) -> _ToolShim:
    return _ToolShim(t.tool)


def _dict_to_plan(d: dict[str, Any]) -> Plan:
    return Plan(
        intent=d.get("intent", "general_observability_question"),
        service=d.get("service"),
        time_range=d.get("time_range", "30m"),
        required_signals=list(d.get("required_signals", [])),
        selected_tools=list(d.get("selected_tools", [])),
        query_focus=d.get("query_focus", ""),
        should_query_metrics=bool(d.get("should_query_metrics", False)),
        should_query_logs=bool(d.get("should_query_logs", False)),
        should_query_alerts=bool(d.get("should_query_alerts", False)),
        should_query_traces=bool(d.get("should_query_traces", False)),
        should_query_dashboards=bool(d.get("should_query_dashboards", False)),
        covered_signals=list(d.get("covered_signals", [])),
        missing_signals=list(d.get("missing_signals", [])),
        skipped_tools=list(d.get("skipped_tools", [])),
        explanation=d.get("explanation", ""),
    )


__all__ = ["Planner"]
