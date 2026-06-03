"""Re-plan helper for the AYOSA agent loop.

After the first dispatch + reflect pass, this module decides whether a
second pass is worth running and, if so, builds a *narrow* follow-up
plan that contains ONLY the additional tools to invoke. Tools that
already produced findings (or errored out) are not re-dispatched.

Design principles:
  - **Pure functions** — easy to unit-test, no side effects.
  - **No LLM** — re-planning is deterministic. The LLM is used only at
    intent-routing time (Step 1) and during synthesis.
  - **Bounded** — at most one re-plan iteration per call. The agent
    enforces a global iteration cap.
  - **Additive only** — the follow-up plan inherits required_signals
    and intent from the original plan; only `selected_tools` is
    narrowed to the new candidates.
"""

from __future__ import annotations

from typing import Iterable, Optional

from accelerators.ayosa.agent.schemas import (
    AgentInput,
    Plan,
    ReflectionNote,
)
from accelerators.ayosa.service import _SIGNAL_TOOLS


# Reflection statuses that warrant trying additional tools.
_GAP_STATUSES: frozenset[str] = frozenset({"missing", "empty"})


def gap_signals(reflections: Iterable[ReflectionNote]) -> list[str]:
    """Return the list of required signals that the first pass didn't cover."""
    return [r.signal for r in reflections if r.status in _GAP_STATUSES]


def find_additional_tools(
    agent_input: AgentInput,
    gaps: list[str],
    already_dispatched: set[str],
) -> tuple[list[str], list[str]]:
    """Find configured tools that can cover gap signals and weren't tried.

    Returns ``(tool_names, covered_signals)``. Each gap signal is matched
    to AT MOST one new tool to keep the second pass cheap.
    """
    if not gaps:
        return [], []

    configured = {t.tool.lower().strip() for t in agent_input.tools}
    additional: list[str] = []
    covered: list[str] = []
    seen_tools: set[str] = set()

    for sig in gaps:
        for tool_name in _SIGNAL_TOOLS.get(sig, []):
            key = tool_name.lower().strip()
            if (
                key in configured
                and key not in already_dispatched
                and key not in seen_tools
            ):
                additional.append(key)
                covered.append(sig)
                seen_tools.add(key)
                break  # one per signal — keep re-plan minimal

    return additional, covered


def build_replan(
    original_plan: Plan,
    agent_input: AgentInput,
    reflections: list[ReflectionNote],
    already_dispatched: set[str],
) -> Optional[Plan]:
    """Build a narrow follow-up plan, or return ``None`` if no replan is useful.

    The returned plan reuses the original intent / service / time_range /
    required_signals and sets ``selected_tools`` to the additional tools
    only. The dispatcher then invokes only those tools in iteration 2.
    """
    gaps = gap_signals(reflections)
    if not gaps:
        return None

    additional, covered = find_additional_tools(
        agent_input, gaps, already_dispatched
    )
    if not additional:
        return None

    return Plan(
        intent=original_plan.intent,
        service=original_plan.service,
        time_range=original_plan.time_range,
        required_signals=original_plan.required_signals,
        selected_tools=additional,
        query_focus=original_plan.query_focus,
        should_query_metrics=original_plan.should_query_metrics,
        should_query_logs=original_plan.should_query_logs,
        should_query_alerts=original_plan.should_query_alerts,
        should_query_traces=original_plan.should_query_traces,
        should_query_dashboards=original_plan.should_query_dashboards,
        covered_signals=covered,
        missing_signals=[s for s in gaps if s not in covered],
        skipped_tools=[],
        explanation=(
            f"Re-plan: covering gaps {covered} via {additional}."
        ),
        workspace_context=original_plan.workspace_context,
        prior_runs=original_plan.prior_runs,
        selection_meta=original_plan.selection_meta,
        tool_args=original_plan.tool_args,
        answer_type=original_plan.answer_type,
        threshold_percent=original_plan.threshold_percent,
    )


def derived_input_for_replan(
    agent_input: AgentInput, additional_tools: list[str]
) -> AgentInput:
    """Return a copy of ``agent_input`` whose tool list is narrowed to the
    re-plan candidates. This keeps the dispatcher from emitting noisy
    ``skipped`` entries for tools already exercised in iteration 0.
    """
    keep = {name.lower().strip() for name in additional_tools}
    filtered = [
        t for t in agent_input.tools if t.tool.lower().strip() in keep
    ]
    return agent_input.model_copy(update={"tools": filtered})


def replan_reason(reflections: list[ReflectionNote], covered: list[str]) -> str:
    """Human-readable explanation surfaced on AgentResult / SSE events."""
    gaps = gap_signals(reflections)
    if not gaps:
        return ""
    covered_str = ", ".join(covered) if covered else "none"
    return (
        f"Initial pass left signals with gaps: {', '.join(gaps)}. "
        f"Re-plan attempts to cover: {covered_str}."
    )


__all__ = [
    "gap_signals",
    "find_additional_tools",
    "build_replan",
    "derived_input_for_replan",
    "replan_reason",
]
