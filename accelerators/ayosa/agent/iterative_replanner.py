"""Step 13 — LLM-driven iterative re-plan.

After iteration N completes, this module asks the LLM tool selector
whether additional tool calls are warranted given the observations
gathered so far. The LLM is allowed to either:

* pick a configured tool that has not yet been dispatched, or
* re-call an already-used tool with a refined ``query`` to drill deeper.

When the LLM is disabled, returns nothing useful, or the caller has
already exhausted the iteration cap, this function returns ``None`` so
that the deterministic ``build_replan`` (gap-filling) path still applies.

Pure, additive layer — the existing single-pass and deterministic
re-plan flows continue to work unchanged.
"""

from __future__ import annotations

from typing import Any, Optional

from accelerators.ayosa.agent.schemas import (
    AgentInput,
    Observation,
    Plan,
)
from accelerators.ayosa.agent.tool_selector_llm import select_tools_llm


_MAX_PRIOR_OBS_LINES = 20
_MAX_PRIOR_OBS_FINDING_LEN = 200


def build_llm_replan(
    *,
    original_plan: Plan,
    agent_input: AgentInput,
    observations: list[Observation],
    already_dispatched: set[str],
) -> Optional[Plan]:
    """Ask the LLM whether a follow-up tool dispatch round is needed.

    Returns a narrow ``Plan`` containing only the new tool calls, or
    ``None`` when no useful follow-up was produced. The returned plan
    reuses the original intent / service / time_range / required_signals
    and stamps ``selection_meta.mode = 'llm_iterative'``.
    """
    if agent_input.llm is None:
        return None
    if not getattr(agent_input.llm, "enabled", False):
        return None

    configured = [t.tool for t in agent_input.tools]
    if not configured:
        return None

    augmented_message = _build_followup_message(agent_input.message, observations)

    result = select_tools_llm(
        message=augmented_message,
        intent=original_plan.intent or "",
        configured_tools=configured,
        required_signals=list(original_plan.required_signals or []),
        llm_config=agent_input.llm,
        service=original_plan.service,
        time_range=original_plan.time_range,
    )
    if result is None or not result.tool_names:
        return None

    selected: list[str] = []
    args: dict[str, dict[str, Any]] = {}
    for name in result.tool_names:
        nl = name.strip().lower()
        if not nl:
            continue
        ta = (result.tool_args or {}).get(nl) or {}
        has_query = isinstance(ta.get("query"), str) and ta["query"].strip()
        is_new_tool = nl not in already_dispatched
        # A re-dispatch of an already-tried tool is only worthwhile when
        # the LLM emits a refined ``query``; otherwise we'd repeat the
        # same heuristic probe.
        if not is_new_tool and not has_query:
            continue
        if nl in selected:
            continue
        selected.append(nl)
        args[nl] = ta

    if not selected:
        return None

    return Plan(
        intent=original_plan.intent,
        service=original_plan.service,
        time_range=original_plan.time_range,
        required_signals=original_plan.required_signals,
        selected_tools=selected,
        query_focus=original_plan.query_focus,
        should_query_metrics=original_plan.should_query_metrics,
        should_query_logs=original_plan.should_query_logs,
        should_query_alerts=original_plan.should_query_alerts,
        should_query_traces=original_plan.should_query_traces,
        should_query_dashboards=original_plan.should_query_dashboards,
        covered_signals=[],
        missing_signals=[],
        skipped_tools=[],
        explanation=(
            f"LLM iterative re-plan: {', '.join(selected)}. "
            f"{result.reasoning}".strip()
        ),
        workspace_context=original_plan.workspace_context,
        prior_runs=original_plan.prior_runs,
        selection_meta={
            "mode": "llm_iterative",
            "provider": result.provider,
            "model": result.model,
            "reasoning": result.reasoning,
        },
        tool_args=args,
        answer_type=original_plan.answer_type,
        threshold_percent=original_plan.threshold_percent,
    )


# ──────────────────────────────────────────────────────────────────────── #
# Helpers
# ──────────────────────────────────────────────────────────────────────── #
def _build_followup_message(original: str, observations: list[Observation]) -> str:
    """Compose the augmented prompt shown to the LLM tool selector."""
    summary = _summarise_observations(observations)
    return (
        f"Original user question: {original.strip()}\n\n"
        "Prior tool observations from this investigation:\n"
        f"{summary}\n\n"
        "Decide whether ADDITIONAL tool calls are needed to fully answer "
        "the original question. If the prior observations already answer "
        "it, return an empty tool_calls list. Otherwise emit ONLY the "
        "additional tool calls needed. You may re-call an already-used "
        "tool ONLY when you emit a refined `query` that drills deeper "
        "than the previous attempt."
    )


def _summarise_observations(observations: list[Observation]) -> str:
    if not observations:
        return "(no prior findings yet)"
    lines: list[str] = []
    for obs in observations[:_MAX_PRIOR_OBS_LINES]:
        finding = (obs.finding or "")[:_MAX_PRIOR_OBS_FINDING_LEN]
        status_tag = "" if obs.status == "ok" else f" [{obs.status}]"
        query_tag = f" query={obs.query!r}" if obs.query else ""
        lines.append(
            f"- {obs.source}/{obs.signal}{status_tag}: {finding}{query_tag}"
        )
    if len(observations) > _MAX_PRIOR_OBS_LINES:
        lines.append(
            f"- ... and {len(observations) - _MAX_PRIOR_OBS_LINES} more findings"
        )
    return "\n".join(lines)


__all__ = ["build_llm_replan"]
