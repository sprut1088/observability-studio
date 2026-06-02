"""Bridge between the public AYOSA chat contract (`AyosaChatRequest` /
`AyosaChatResponse`) and the internal `AyosaAgent` backbone.

This is the *only* glue layer. It does NOT modify the existing
`AyosaService` behaviour — when `agent_mode=False`, callers must keep
using `AyosaService.investigate(...)`.

Requirements honoured here:
  1. Additive only. `AyosaService` is untouched.
  6. LLM is additive: the agent's synthesizer already swallows LLM
     errors, and we wrap the LLM enrichment call so a failure leaves
     the deterministic answer in place.
  7. No generic RCA unless intent requires investigation. We only
     populate `probable_root_cause` / `impact` / `incident_snapshot`
     for investigation-shaped intents.
"""

from __future__ import annotations

import logging
from typing import Any

from accelerators.ayosa.agent import AyosaAgent
from accelerators.ayosa.agent.schemas import (
    AgentAIConfig,
    AgentInput,
    AgentResult,
    AgentToolConfig,
)
from accelerators.ayosa.agent.session_store import (
    SessionStore,
    get_default_store,
    summarise_evidence,
)
from accelerators.ayosa.workspace_index import (
    get_service_context,
    is_available as workspace_index_available,
    search_workspace,
    workspace_overview,
)
from accelerators.ayosa.persistence import persist_agent_result

logger = logging.getLogger(__name__)


# Intents for which a root-cause / incident snapshot is meaningful.
# Anything else (current_time, healthy_services_list, dashboard_lookup …)
# must NOT auto-populate RCA fields.
_INVESTIGATION_INTENTS: frozenset[str] = frozenset({
    "error_investigation",
    "latency_issues",
    "service_health",
    "active_alerts",
    "latest_error",
    "environment_health",
})


def run_agent_chat(
    request: Any,
    *,
    agent: AyosaAgent | None = None,
    store: SessionStore | None = None,
) -> dict[str, Any]:
    """Run the agent loop and return a dict shaped like `AyosaChatResponse`.

    `request` is an `AyosaChatRequest` (or duck-typed equivalent with the
    same attributes). `agent` and `store` are injectable for tests.
    """
    agent = agent or AyosaAgent()
    store = store or get_default_store()

    # ── Resolve session_id and honour reset_session ──
    session_id = (getattr(request, "session_id", None) or "").strip()
    if not session_id:
        session_id = store.new_session_id()
    if getattr(request, "reset_session", False):
        store.reset(session_id)

    # ── Follow-up inference: inherit prior service/time_range when blank ──
    inferred = store.infer_followup_context(
        session_id,
        current_service=getattr(request, "service", None),
        current_time_range=getattr(request, "time_range", None),
        default_time_range="30m",
    )

    agent_input = _to_agent_input(request, session_id=session_id, inferred=inferred)

    # ── Pre-plan retrieval: workspace context (never fabricated) ──
    workspace_ctx = _retrieve_workspace_context(
        message=agent_input.message,
        service=agent_input.service,
    )

    try:
        result = agent.run(agent_input)
    except Exception as exc:  # noqa: BLE001 — never propagate; chat must respond
        logger.error("AyosaAgent run failed: %s", exc, exc_info=True)
        out = _error_response(request, str(exc))
        out["session_id"] = session_id
        out["workspace_context"] = workspace_ctx
        return out

    # Attach the retrieved workspace context to the plan so consumers
    # (LLM synthesiser, UI, /chat callers) can see what was available.
    result.plan.workspace_context = workspace_ctx

    chat_response = _agent_result_to_chat_response(result, request)
    chat_response["session_id"] = session_id
    chat_response["workspace_context"] = workspace_ctx

    # ── Persist this turn so the next request can use it ──
    try:
        store.record_turn(
            session_id=session_id,
            user_message=agent_input.message,
            assistant_answer=result.final_response,
            intent=result.intent,
            service=agent_input.service,
            time_range=result.plan.time_range,
            tools_used=result.plan.selected_tools,
            evidence_summary=summarise_evidence(result.observations),
            snapshot=(
                result.snapshot.model_dump() if result.snapshot else None
            ),
        )
    except Exception as exc:  # noqa: BLE001 — memory failures must not break chat
        logger.warning("Session memory persist failed: %s", exc)

    # ── Persist run to SQLite (best-effort; never breaks chat) ──
    try:
        run_id = persist_agent_result(
            chat_response, request_message=agent_input.message,
        )
        if run_id:
            chat_response["run_id"] = run_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("Run persistence failed: %s", exc)

    return chat_response


# ──────────────────────────────────────────────────────────────────────── #
# Input mapping
# ──────────────────────────────────────────────────────────────────────── #
def _to_agent_input(
    request: Any,
    *,
    session_id: str | None = None,
    inferred: dict[str, Any] | None = None,
) -> AgentInput:
    tools = [
        AgentToolConfig(
            tool=t.tool,
            base_url=t.base_url,
            auth_token=getattr(t, "auth_token", None),
        )
        for t in (request.tools or [])
    ]

    llm_cfg: AgentAIConfig | None = None
    ai = getattr(request, "ai", None)
    if ai is not None:
        llm_cfg = AgentAIConfig(
            enabled=bool(getattr(ai, "enabled", False)),
            provider=getattr(ai, "provider", None),
            api_key=getattr(ai, "api_key", None),
            model=getattr(ai, "model", None),
            azure_endpoint=getattr(ai, "azure_endpoint", None),
            azure_deployment=getattr(ai, "azure_deployment", None),
            openrouter_model=getattr(ai, "openrouter_model", None),
        )

    sid = session_id or getattr(request, "session_id", None) or "default"

    # Inferred values take effect ONLY when the request didn't supply them.
    service = inferred["service"] if inferred else getattr(request, "service", None)
    time_range = (
        inferred["time_range"]
        if inferred
        else (getattr(request, "time_range", "30m") or "30m")
    )

    return AgentInput(
        message=request.message,
        service=service,
        time_range=time_range or "30m",
        tools=tools,
        llm=llm_cfg,
        session_id=sid,
    )


# ──────────────────────────────────────────────────────────────────────── #
# Result mapping
# ──────────────────────────────────────────────────────────────────────── #
def _agent_result_to_chat_response(
    result: AgentResult, request: Any
) -> dict[str, Any]:
    intent = result.intent or ""
    is_investigation = intent in _INVESTIGATION_INTENTS

    coverage = result.snapshot.coverage if result.snapshot else {}

    evidence = [
        {
            "source": o.source,
            "signal": o.signal,
            "finding": o.finding,
            "query": o.query,
            "status": o.status,
            "raw": o.raw,
        }
        for o in result.observations
    ]

    timeline = [
        {
            "timestamp": t.timestamp,
            "source": t.source,
            "event": t.event,
            "severity": t.severity,
        }
        for t in result.timeline
    ]

    tool_steps = [
        {
            "index": s.index,
            "tool": s.tool,
            "label": s.label,
            "status": s.status,
            "error": s.error,
        }
        for s in result.tool_steps
    ]

    # Requirement #7 — only populate RCA fields for investigation intents,
    # AND only when we have at least one ok observation or LLM output.
    root_cause = ""
    impact = ""
    incident_snapshot: dict[str, Any] | None = None
    if is_investigation and (evidence or result.llm_used):
        snap = result.snapshot
        if result.llm_analysis:
            root_cause = (
                result.llm_analysis.get("probable_root_cause")
                or result.llm_analysis.get("root_cause")
                or ""
            )
            impact = result.llm_analysis.get("impact", "") or ""
        if snap is not None:
            incident_snapshot = {
                "root_cause": root_cause or snap.root_cause,
                "impact": impact or snap.impact,
                "confidence": snap.confidence,
                "coverage": snap.coverage,
                "top_findings": snap.top_findings,
                "recommended_actions": snap.recommended_actions,
                "timeline_summary": timeline[:5],
            }

    suggested_actions = _derive_suggested_actions(result)

    llm_analysis: dict[str, Any] | None = None
    if result.llm_analysis is not None:
        # Normalise so the existing UI fields line up; never fabricate keys.
        llm_analysis = {
            "executive_summary": result.llm_analysis.get("executive_summary", ""),
            "reasoning": result.llm_analysis.get("reasoning", ""),
            "missing_information": result.llm_analysis.get("missing_information", []),
            "recommended_next_steps": result.llm_analysis.get(
                "recommended_next_steps", []
            ),
            "provider": result.llm_analysis.get("provider"),
            "model": result.llm_analysis.get("model"),
            "error": result.llm_analysis.get("error"),
        }

    return {
        "mode": "agent",
        "answer": result.final_response,
        "service": getattr(request, "service", None),
        "time_range": result.plan.time_range,
        "confidence": result.confidence,
        "intent": intent,
        "plan": result.plan.model_dump(),
        "tool_steps": tool_steps,
        "observations": evidence,  # alias of evidence for consumers wanting both
        "evidence": evidence,
        "timeline": timeline,
        "signal_coverage": coverage,
        "missing_signals": result.plan.missing_signals,
        "probable_root_cause": root_cause,
        "impact": impact,
        "detected_patterns": [],
        "related_artifacts": [],
        "suggested_actions": suggested_actions,
        "ai_analysis": None,
        "charts": [],
        "llm_analysis": llm_analysis,
        "incident_snapshot": incident_snapshot,
    }


def _apply_service_table_shape(
    response: dict[str, Any],
    result: AgentResult,
) -> dict[str, Any]:
    """Reshape an agent response for the ``service_stability_ranking`` intent.

    Strips investigation-shaped fields and surfaces the structured
    ``service_stability`` table extracted from Prometheus evidence raws.
    """
    plan = result.plan
    threshold = getattr(plan, "threshold_percent", None) or 1.0
    time_range = plan.time_range or ""

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for obs in result.observations:
        raw = obs.raw
        if not isinstance(raw, dict):
            continue
        for row in raw.get("service_stability") or []:
            if not isinstance(row, dict):
                continue
            svc = (row.get("service") or "").strip()
            if not svc or svc in seen:
                continue
            seen.add(svc)
            rows.append(row)

    rows.sort(key=lambda r: float(r.get("error_rate_percent") or 0.0))

    if rows:
        answer = (
            f"Found {len(rows)} service(s) with error rate below "
            f"{float(threshold):g}% over the last {time_range}."
        )
    else:
        answer = (
            f"No services found with error rate below {float(threshold):g}% "
            f"over the last {time_range}."
        )

    response["answer"] = answer
    response["answer_type"] = "service_table"
    response["service_stability"] = rows
    response["probable_root_cause"] = ""
    response["impact"] = ""
    response["incident_snapshot"] = None
    response["timeline"] = []
    response["detected_patterns"] = []
    response["suggested_actions"] = []
    response["ai_analysis"] = None
    response["llm_analysis"] = None
    return response


def _derive_suggested_actions(result: AgentResult) -> list[str]:
    """Build short, registry-driven action hints.

    Never generic RCA prose — only concrete "do X" items derived from
    reflections, missing signals, and (when present) the LLM's
    recommended next steps.
    """
    actions: list[str] = []

    # 1. From reflections that flagged a problem.
    for note in result.reflections:
        if note.status in ("missing", "empty"):
            actions.append(
                f"Collect '{note.signal}' data — none was usable in this run."
            )

    # 2. From the LLM, if it produced any.
    if result.llm_analysis:
        for step in result.llm_analysis.get("recommended_next_steps") or []:
            if isinstance(step, str) and step.strip():
                actions.append(step.strip())

    # Dedupe while keeping order; cap to 5.
    seen: set[str] = set()
    deduped: list[str] = []
    for a in actions:
        if a not in seen:
            seen.add(a)
            deduped.append(a)
    return deduped[:5]


def _error_response(request: Any, msg: str) -> dict[str, Any]:
    return {
        "mode": "agent",
        "answer": f"Agent run failed: {msg}",
        "service": getattr(request, "service", None),
        "time_range": getattr(request, "time_range", "30m") or "30m",
        "confidence": 0.0,
        "intent": "",
        "plan": None,
        "tool_steps": [],
        "observations": [],
        "evidence": [],
        "timeline": [],
        "signal_coverage": {},
        "missing_signals": [],
        "probable_root_cause": "",
        "impact": "",
        "detected_patterns": [],
        "related_artifacts": [],
        "suggested_actions": [],
        "ai_analysis": None,
        "charts": [],
        "llm_analysis": None,
        "incident_snapshot": None,
    }


__all__ = ["run_agent_chat"]


# ──────────────────────────────────────────────────────────────────────── #
# Workspace retrieval — pure function, isolated for tests
# ──────────────────────────────────────────────────────────────────────── #
def _retrieve_workspace_context(
    *,
    message: str,
    service: str | None,
    limit: int = 5,
) -> dict[str, Any]:
    """Return a compact workspace snapshot for the plan + LLM context.

    Never invents data: if the on-disk index is missing or empty, the
    returned dict explicitly says so via `available=False` and
    `message="workspace index unavailable"`. Callers must check
    `available` before treating the payload as authoritative.
    """
    try:
        if not workspace_index_available():
            from accelerators.ayosa.workspace_index import EMPTY_INDEX_MESSAGE
            return {"available": False, "message": EMPTY_INDEX_MESSAGE}

        payload: dict[str, Any] = {
            "available": True,
            "overview": workspace_overview(),
        }
        if service:
            payload["service_context"] = get_service_context(service)
        if message:
            payload["matches"] = search_workspace(message, limit=limit)
        return payload
    except Exception as exc:  # noqa: BLE001 — workspace must never break chat
        logger.warning("Workspace retrieval failed: %s", exc)
        return {"available": False, "message": "workspace index unavailable"}