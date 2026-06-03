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
    tools_for_signal,
    select_tools_for_signals,
)
from accelerators.ayosa.agent.tool_selector_llm import select_tools_llm


class Planner:
    """Builds a `Plan` from an `AgentInput`."""

    def build(self, agent_input: AgentInput, intent_hint: str | None = None) -> Plan:
        """Construct the plan.

        When ``intent_hint`` is supplied (e.g. from the LLM intent
        router) it is forwarded to ``build_ayosa_plan`` as an override
        so that signal selection, query focus, and answer-shape hints
        derive from the LLM-routed intent rather than the keyword
        classifier.
        """
        plan_dict = build_ayosa_plan(
            message=agent_input.message,
            service=agent_input.service,
            time_range=agent_input.time_range,
            available_tools=[_tool_shim(t) for t in agent_input.tools],
            intent_override=intent_hint,
        )
        if intent_hint and not plan_dict.get("intent"):
            plan_dict["intent"] = intent_hint

        # ── Override tool selection using the structured tool registry ──
        required = list(plan_dict.get("required_signals") or [])
        configured = [t.tool for t in agent_input.tools]

        # Step 7: try the LLM tool-selector first. Falls back silently to
        # the deterministic registry walk on any failure path.
        selection_meta: dict[str, Any] = {"mode": "deterministic"}
        tool_args: dict[str, dict[str, Any]] | None = None
        llm_pick = _try_llm_select(
            agent_input=agent_input,
            intent=plan_dict.get("intent", ""),
            configured=configured,
            required_signals=required,
        )
        if llm_pick is not None:
            selected = llm_pick["tool_names"]
            covered = _covered_from_selection(selected, required)
            selection_meta = {
                "mode": "llm",
                "provider": llm_pick["provider"],
                "model": llm_pick["model"],
                "reasoning": llm_pick["reasoning"],
            }
            ta = llm_pick.get("tool_args") or {}
            # Only carry tool_args when at least one tool has a concrete
            # override (legacy ``tool_names`` shape returns empty dicts).
            has_overrides = any(
                any(v not in (None, "") for v in (args or {}).values())
                for args in ta.values()
            )
            tool_args = ta if has_overrides else None
        else:
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
        plan_dict["selection_meta"] = selection_meta
        plan_dict["tool_args"] = tool_args
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
        selection_meta=d.get("selection_meta"),
        tool_args=d.get("tool_args"),
        answer_type=d.get("answer_type", "investigation"),
        threshold_percent=d.get("threshold_percent"),
    )


# ──────────────────────────────────────────────────────────────────────── #
# Step 7 — LLM tool selector wiring
# ──────────────────────────────────────────────────────────────────────── #
def _try_llm_select(
    *,
    agent_input: AgentInput,
    intent: str,
    configured: list[str],
    required_signals: list[str],
) -> dict[str, Any] | None:
    """Call the LLM tool selector, returning a normalised payload or None.

    Returns ``None`` when the LLM is disabled, the call fails, or the
    selector emits an empty result for an intent that genuinely needs
    tools (in which case the deterministic path is safer).
    """
    if agent_input.llm is None or not configured:
        return None

    result = select_tools_llm(
        message=agent_input.message,
        intent=intent or "",
        configured_tools=configured,
        required_signals=required_signals,
        llm_config=agent_input.llm,
        service=agent_input.service,
        time_range=agent_input.time_range,
    )
    if result is None:
        return None

    # If the intent requires signals but the LLM picked nothing, fall back
    # to the deterministic selector. An empty pick is only acceptable when
    # there are no required signals (e.g. current_time).
    if required_signals and not result.tool_names:
        return None

    return {
        "tool_names": list(result.tool_names),
        "provider": result.provider,
        "model": result.model,
        "reasoning": result.reasoning,
        "tool_args": dict(result.tool_args or {}),
    }


def _covered_from_selection(
    selected: list[str], required_signals: list[str],
) -> list[str]:
    """Compute which required signals are covered by an explicit tool list.

    Uses the same registry definitions as ``select_tools_for_signals`` so
    coverage stays consistent regardless of who picked the tools.
    """
    selected_set = set(selected)
    covered: list[str] = []
    for signal in required_signals:
        for td in tools_for_signal(signal):
            if td.name in selected_set:
                if signal not in covered:
                    covered.append(signal)
                break
    return covered


__all__ = ["Planner"]
