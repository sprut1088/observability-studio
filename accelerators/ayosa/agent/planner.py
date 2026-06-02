"""Planner — bridges the existing intent classifier with the structured
tool registry.

Intent → required-signals comes from `accelerators.ayosa.service.build_ayosa_plan`
(so the agent and the legacy /api/ayosa/chat path stay aligned on intent
semantics). Tool selection is then re-driven through
`accelerators.ayosa.agent.tool_registry`, which is the single source of
truth for what each tool can do.
"""

from __future__ import annotations

from typing import Any

from accelerators.ayosa.service import build_ayosa_plan
from accelerators.ayosa.agent.schemas import AgentInput, Plan
from accelerators.ayosa.agent.tool_registry import (
    select_tools_for_signals,
)


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
        if intent_hint and not plan_dict.get("intent"):
            plan_dict["intent"] = intent_hint

        # ── Override tool selection using the structured tool registry ──
        required = list(plan_dict.get("required_signals") or [])
        configured = [t.tool for t in agent_input.tools]
        selected, covered = select_tools_for_signals(required, configured)
        missing = [s for s in required if s not in covered]
        skipped = [
            t.tool for t in agent_input.tools
            if t.tool.strip().lower() not in selected
        ]

        plan_dict["selected_tools"] = selected
        plan_dict["covered_signals"] = covered
        plan_dict["missing_signals"] = missing
        plan_dict["skipped_tools"] = skipped
        plan_dict["explanation"] = _build_explanation(
            intent=plan_dict.get("intent", ""),
            service=plan_dict.get("service"),
            selected=selected,
            missing=missing,
        )

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


def _build_explanation(
    intent: str,
    service: str | None,
    selected: list[str],
    missing: list[str],
) -> str:
    parts = [f"Intent: {(intent or '').replace('_', ' ')}"]
    if service:
        parts.append(f"service={service}")
    parts.append(
        f"querying {', '.join(selected)}" if selected
        else "no observability queries needed"
    )
    if missing:
        parts.append(f"missing signals: {', '.join(missing)}")
    return ". ".join(parts) + "."


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
        answer_type=d.get("answer_type", "investigation"),
        threshold_percent=d.get("threshold_percent"),
    )


__all__ = ["Planner"]
