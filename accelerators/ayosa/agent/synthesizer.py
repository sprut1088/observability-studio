"""Synthesizer — Respond phase.

Composes a deterministic answer from observations and reflections, then
*optionally* enriches it via the existing `AyosaAIAnalyst`. The LLM is
called **only** when:
  * The caller enabled it (`AgentInput.llm.enabled`)
  * Deterministic evidence exists (at least one `ok` observation)
"""

from __future__ import annotations

import logging
from typing import Any

from accelerators.ayosa.agent.schemas import (
    AgentInput,
    AgentResult,
    IncidentSnapshot,
    Observation,
    Plan,
    ReflectionNote,
    TimelineEntry,
    ToolStep,
)

logger = logging.getLogger(__name__)


class Synthesizer:
    """Builds the final `AgentResult` from the agent loop's intermediate state."""

    def synthesize(
        self,
        agent_input: AgentInput,
        plan: Plan,
        tool_steps: list[ToolStep],
        observations: list[Observation],
        reflections: list[ReflectionNote],
    ) -> AgentResult:
        ok_obs = [o for o in observations if o.status == "ok"]
        confidence = calculate_confidence(plan, ok_obs, reflections)
        configured_tool_names = [t.tool for t in agent_input.tools]
        answer = compose_deterministic_answer(
            plan, ok_obs, reflections, configured_tools=configured_tool_names
        )
        snapshot = build_snapshot(plan, ok_obs, confidence)

        result = AgentResult(
            intent=plan.intent,
            plan=plan,
            tool_steps=tool_steps,
            observations=observations,
            reflections=reflections,
            final_response=answer,
            confidence=confidence,
            charts=[],
            timeline=build_timeline(ok_obs),
            evidence=observations,
            snapshot=snapshot,
            llm_used=False,
            llm_analysis=None,
        )

        # ── Optional LLM enhancement (gated by evidence sufficiency) ──
        llm_cfg = agent_input.llm
        if llm_cfg and llm_cfg.enabled and ok_obs:
            try:
                enhanced = self._invoke_llm(llm_cfg, result)
            except Exception as exc:  # noqa: BLE001 — LLM must never break agent
                logger.warning("Synthesizer LLM enrichment raised: %s", exc)
                enhanced = None
            if enhanced is not None:
                result.llm_analysis = enhanced
                result.llm_used = True
                # If the LLM provided a richer narrative, prefer it.
                summary = (enhanced.get("executive_summary") or "").strip()
                if summary:
                    result.final_response = summary

        return result

    # ------------------------------------------------------------------ #
    # LLM invocation — isolated so tests can monkeypatch it
    # ------------------------------------------------------------------ #
    def _invoke_llm(self, llm_cfg: Any, result: AgentResult) -> dict[str, Any] | None:
        try:
            from accelerators.ayosa.llm.analyst import AyosaAIAnalyst
        except ImportError as exc:
            logger.info("AyosaAIAnalyst unavailable: %s", exc)
            return None

        try:
            analyst = AyosaAIAnalyst(
                {
                    "provider": llm_cfg.provider or "anthropic",
                    "api_key": llm_cfg.api_key,
                    "model": llm_cfg.model,
                    "azure_endpoint": llm_cfg.azure_endpoint,
                    "azure_deployment": llm_cfg.azure_deployment,
                    "openrouter_model": llm_cfg.openrouter_model,
                }
            )
            context = _build_llm_context(result)
            return analyst.analyze_focused(context)
        except Exception as exc:  # noqa: BLE001 — LLM errors must not break the agent
            logger.warning("Synthesizer LLM call failed: %s", exc)
            return None


# ──────────────────────────────────────────────────────────────────────── #
# Pure helpers — unit-testable, no I/O
# ──────────────────────────────────────────────────────────────────────── #
def calculate_confidence(
    plan: Plan,
    ok_observations: list[Observation],
    reflections: list[ReflectionNote],
) -> float:
    """Deterministic 0..1 confidence based on signal sufficiency.

    * 1.0 when no signals are required (chit-chat / current_time).
    * Otherwise: fraction of required signals with `sufficient` reflection,
      dampened by error/empty signal count.
    """
    required = plan.required_signals
    if not required:
        return 1.0 if ok_observations or not plan.selected_tools else 0.5

    by_status = {r.status: 0 for r in reflections}
    for r in reflections:
        by_status[r.status] = by_status.get(r.status, 0) + 1

    sufficient = by_status.get("sufficient", 0)
    partial = by_status.get("partial", 0)
    score = (sufficient + 0.5 * partial) / max(len(required), 1)
    return round(max(0.0, min(1.0, score)), 2)


def compose_deterministic_answer(
    plan: Plan,
    ok_observations: list[Observation],
    reflections: list[ReflectionNote],
    configured_tools: list[str] | None = None,
) -> str:
    """Build a short natural-language answer purely from facts."""
    if not plan.required_signals:
        return (
            f"Intent '{plan.intent.replace('_', ' ')}' — no observability "
            "tool queries were required."
        )

    if not ok_observations:
        # Prefer a registry-derived suggestion when we know what's missing.
        if configured_tools is not None:
            from accelerators.ayosa.agent.tool_registry import (
                format_missing_signal_message,
            )

            msg = format_missing_signal_message(
                intent=plan.intent,
                required_signals=plan.required_signals,
                configured_tools=configured_tools,
            )
            if msg:
                return msg

        missing = [r.signal for r in reflections if r.status in ("missing", "empty")]
        if missing:
            return (
                f"No usable evidence was collected for intent "
                f"'{plan.intent.replace('_', ' ')}'. Missing signals: "
                f"{', '.join(missing)}."
            )
        return (
            f"No findings returned for intent "
            f"'{plan.intent.replace('_', ' ')}'."
        )

    findings_preview = "; ".join(
        o.finding for o in ok_observations[:3] if o.finding
    )
    suffix = "" if len(ok_observations) <= 3 else f" (+{len(ok_observations) - 3} more)"
    return (
        f"Intent '{plan.intent.replace('_', ' ')}' — {len(ok_observations)} "
        f"finding(s) across {len({o.source for o in ok_observations})} tool(s). "
        f"{findings_preview}{suffix}"
    )


def build_timeline(observations: list[Observation]) -> list[TimelineEntry]:
    """Emit timeline entries for observations that look event-like."""
    out: list[TimelineEntry] = []
    for o in observations:
        raw = o.raw if isinstance(o.raw, dict) else {}
        ts = raw.get("timestamp") or raw.get("time") or raw.get("@timestamp")
        severity = raw.get("severity") or raw.get("level")
        out.append(
            TimelineEntry(
                timestamp=str(ts) if ts is not None else None,
                source=o.source,
                event=(o.finding or "")[:200],
                severity=str(severity) if severity is not None else None,
            )
        )
    return out


def build_snapshot(
    plan: Plan,
    ok_observations: list[Observation],
    confidence: float,
) -> IncidentSnapshot:
    coverage: dict[str, list[str]] = {}
    for o in ok_observations:
        coverage.setdefault(o.signal, [])
        if o.source not in coverage[o.signal]:
            coverage[o.signal].append(o.source)

    top_findings = [o.finding for o in ok_observations if o.finding][:5]
    return IncidentSnapshot(
        root_cause="",
        impact="",
        confidence=confidence,
        coverage=coverage,
        top_findings=top_findings,
        recommended_actions=[],
    )


def _build_llm_context(result: AgentResult) -> dict[str, Any]:
    """Compact, evidence-only dict handed to the analyst.

    Mirrors the shape used by `AyosaService._generate_llm_context` so the
    existing `AyosaAIAnalyst.analyze_focused()` can consume it unchanged.
    """
    return {
        "service": result.plan.service,
        "time_range": result.plan.time_range,
        "intent": result.intent,
        "confidence": result.confidence,
        "signal_coverage": result.snapshot.coverage if result.snapshot else {},
        "missing_signals": result.plan.missing_signals,
        "workspace_context": result.plan.workspace_context,
        "evidence": [
            {
                "source": o.source,
                "signal": o.signal,
                "finding": o.finding,
                "query": o.query,
                "status": o.status,
            }
            for o in result.observations
        ],
        "timeline": [t.model_dump() for t in result.timeline],
    }


__all__ = [
    "Synthesizer",
    "calculate_confidence",
    "compose_deterministic_answer",
    "build_timeline",
    "build_snapshot",
]
