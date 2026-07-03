from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests


DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
ANTHROPIC_MODEL_ALIASES = {
    "claude-3-5-sonnet-latest": DEFAULT_ANTHROPIC_MODEL,
    "claude-3-5-sonnet": DEFAULT_ANTHROPIC_MODEL,
    "claude-3.5-sonnet": DEFAULT_ANTHROPIC_MODEL,
    "claude-3-5-sonnet-20240620": DEFAULT_ANTHROPIC_MODEL,
    "claude-3-5-sonnet-20241022": DEFAULT_ANTHROPIC_MODEL,
    "claude-3-7-sonnet-20250219": DEFAULT_ANTHROPIC_MODEL,
    "claude-sonnet-4": DEFAULT_ANTHROPIC_MODEL,
    "claude-sonnet-4-20250514": DEFAULT_ANTHROPIC_MODEL,
}


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _load_methodology() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "knowledge" / "google_sre_slo_methodology.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "name": "Fallback SRE methodology",
            "principles": [
                "SLOs should be explicit, measurable, and based on SLIs.",
                "Latency SLOs need a threshold.",
                "Availability and error-rate SLOs need a valid-event denominator.",
                "Error budgets should guide reliability and release decisions.",
            ],
            "advisor_rules": [
                "Do not change deterministic SLO recommendations.",
                "Use only provided evidence.",
            ],
            "slo_review_checklist": [],
        }


def _compact_evidence(evidence: list[Any], limit: int = 4) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ev in (evidence or [])[:limit]:
        rows.append(
            {
                "source": _get(ev, "source"),
                "signal_type": _get(ev, "signal_type"),
                "title": _get(ev, "title"),
                "value": _get(ev, "value"),
                "interpretation": _get(ev, "interpretation"),
                "query": _get(ev, "query"),
            }
        )
    return rows


def _recommendation_row(rec: Any) -> dict[str, Any]:
    return {
        "service": _get(rec, "service"),
        "sli_type": _get(rec, "sli_type"),
        "name": _get(rec, "name"),
        "objective": _get(rec, "objective"),
        "objective_display": (
            _get(rec, "objective_display")
            or _get(rec, "slo_display")
            or _get(rec, "display_objective")
        ),
        "threshold": _get(rec, "threshold"),
        "window": _get(rec, "window"),
        "confidence": _get(rec, "confidence"),
        "rationale": _get(rec, "rationale"),
        "assumptions": _get(rec, "assumptions", []) or [],
        "evidence": _compact_evidence(_get(rec, "evidence", []) or []),
    }


def _coverage_row(row: Any) -> dict[str, Any]:
    return {
        "service": _get(row, "service"),
        "sli_type": _get(row, "sli_type"),
        "status": _get(row, "status"),
        "recommended_objective": _get(row, "recommended_objective"),
        "recommended_objective_display": (
            _get(row, "recommended_objective_display")
            or _get(row, "objective_display")
        ),
        "action": _get(row, "action"),
    }


def _objective_text(rec: dict[str, Any]) -> str:
    return (
        rec.get("objective_display")
        or rec.get("recommended_objective_display")
        or rec.get("recommended_objective")
        or (
            f"{rec.get('objective')} over {rec.get('window')}"
            if rec.get("objective") and rec.get("window")
            else str(rec.get("objective") or "Review deterministic SLO recommendation")
        )
    )


def _fallback_advisor(payload: dict[str, Any], status: str = "fallback") -> dict[str, Any]:
    recommendations = payload.get("production_ready_recommendations", []) or []
    coverage = payload.get("slo_coverage_matrix", []) or []
    summary = payload.get("summary", {}) or {}

    top_actions: list[dict[str, Any]] = []
    for index, rec in enumerate(recommendations[:5], start=1):
        display = _objective_text(rec)
        top_actions.append(
            {
                "priority": index,
                "service": rec.get("service"),
                "action": f"Create {rec.get('sli_type')} SLO",
                "recommended_slo": display,
                "why": rec.get("rationale") or "Evidence-backed deterministic recommendation.",
                "owner": "Service owner and SRE team",
                "risk": "medium",
            }
        )

    questions: list[dict[str, str]] = []
    for row in coverage[:20]:
        service = row.get("service")
        sli = row.get("sli_type")
        status_value = row.get("status")
        if status_value == "recommended":
            questions.append(
                {
                    "service": service,
                    "question": (
                        f"Does the service owner agree that the recommended {sli} SLO is "
                        "user-relevant, measurable, and suitable for production rollout?"
                    ),
                }
            )
        elif status_value == "missing_telemetry":
            questions.append(
                {
                    "service": service,
                    "question": (
                        f"What instrumentation is needed before a production-grade {sli} SLO can be adopted?"
                    ),
                }
            )

    return {
        "status": status,
        "executive_interpretation": (
            f"SLO Studio analyzed {summary.get('service_count', 0)} services over "
            f"{summary.get('lookback_days', 'the selected lookback')} days and produced "
            f"{summary.get('production_ready_slo_count', len(recommendations))} evidence-backed recommendations. "
            "The deterministic engine calculated the SLO objectives; the advisor organizes evidence into rollout actions."
        ),
        "top_actions": top_actions,
        "governance_notes": [
            "Service owners should review every SLO objective before production rollout.",
            "Burn-rate alerts, dashboard coverage, alert routing, and runbooks should be validated before paging is enabled.",
            "Error budget policy should be agreed before SLOs are used for release governance.",
            "AI interpretation must not override deterministic SLO math, PromQL, confidence, or threshold values.",
        ],
        "service_owner_questions": questions[:8],
        "rollout_plan": [
            "Week 1: Review recommended SLOs with service owners and confirm ownership, user impact, and alert routing.",
            "Week 2: Deploy SLO rules and dashboards in non-production or silent mode.",
            "Week 3: Configure burn-rate alerts, runbooks, and escalation paths.",
            "Week 4: Enable production governance and review error-budget consumption with service owners.",
        ],
        "leadership_summary": (
            "AI translated deterministic SLO findings into a leadership-ready rollout plan for service owners, "
            "SREs, and governance teams."
        ),
    }


def _sanitize_plain_text(text: str, max_chars: int = 2200) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
    cleaned = cleaned.replace("```", "").strip()
    cleaned = "\n".join(line.rstrip() for line in cleaned.splitlines()).strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rsplit(" ", 1)[0].rstrip() + "..."
    return cleaned


def _normalize_anthropic_model(model: str | None) -> str:
    raw = (model or "").strip() or DEFAULT_ANTHROPIC_MODEL
    return ANTHROPIC_MODEL_ALIASES.get(raw, raw)


def _call_anthropic(prompt: str) -> tuple[str, str]:
    api_key = (
        os.getenv("SLO_AI_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("CLAUDE_API_KEY")
        or os.getenv("AI_API_KEY")
    )
    model = _normalize_anthropic_model(
        os.getenv("SLO_AI_MODEL")
        or os.getenv("ANTHROPIC_MODEL")
        or os.getenv("AI_MODEL")
        or DEFAULT_ANTHROPIC_MODEL
    )

    if not api_key:
        raise RuntimeError("No Anthropic API key found.")

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        json={
            "model": model,
            "max_tokens": 900,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    response.raise_for_status()

    data = response.json()
    text = "\n".join(
        block.get("text", "")
        for block in data.get("content", [])
        if block.get("type") == "text"
    ).strip()
    return text, model


def _call_openai(prompt: str) -> tuple[str, str]:
    api_key = (
        os.getenv("SLO_AI_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("AI_API_KEY")
    )
    model = (
        os.getenv("SLO_AI_MODEL")
        or os.getenv("OPENAI_MODEL")
        or os.getenv("AI_MODEL")
        or "gpt-4o-mini"
    )

    if not api_key:
        raise RuntimeError("No OpenAI API key found.")

    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        },
        json={
            "model": model,
            "temperature": 0.2,
            "max_tokens": 900,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an SRE advisor for regulated banking environments. "
                        "Use only provided evidence. Do not modify SLO calculations."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        },
        timeout=60,
    )
    response.raise_for_status()

    data = response.json()
    return (data["choices"][0]["message"]["content"] or "").strip(), model


def _build_narrative_prompt(payload: dict[str, Any]) -> str:
    compact_payload = {
        "methodology_principles": (payload.get("methodology") or {}).get("principles", [])[:6],
        "summary": payload.get("summary", {}),
        "tools": payload.get("tool_inventory", []),
        "production_ready_recommendations": payload.get("production_ready_recommendations", [])[:7],
        "candidate_recommendations": payload.get("candidate_recommendations", [])[:4],
        "coverage_samples": payload.get("slo_coverage_matrix", [])[:12],
        "existing_slos": payload.get("existing_slos", [])[:6],
        "collection_errors": payload.get("collection_errors", [])[:5],
    }

    return f"""
You are writing the AI Advisor narrative for SLO Studio in a regulated banking environment.

SLO Studio has already calculated every SLO objective, latency threshold, confidence score, and PromQL expression deterministically. Your task is only to explain the deterministic evidence for leadership and service owners.

Rules:
- Return plain text only. Do not return JSON.
- Do not use markdown tables.
- Do not change any objective, threshold, confidence, or PromQL.
- Do not invent telemetry or service ownership.
- Use only the evidence below.
- Keep the answer to 2 concise paragraphs, maximum 220 words total.
- First paragraph: executive interpretation and business/operational risk.
- Second paragraph: rollout guidance for service owners and governance.

Evidence:
{json.dumps(compact_payload, indent=2)}
""".strip()


def generate_ai_advisor(
    summary: dict[str, Any],
    tool_inventory: list[dict[str, Any]],
    production_ready_recommendations: list[Any],
    candidate_recommendations: list[Any],
    slo_coverage_matrix: list[Any],
    existing_slos: list[dict[str, Any]],
    collection_errors: list[dict[str, Any]],
) -> dict[str, Any]:
    enabled = str(os.getenv("SLO_AI_ENABLED", "true")).lower() in {"1", "true", "yes", "y"}
    provider = (os.getenv("SLO_AI_PROVIDER") or os.getenv("AI_PROVIDER") or "anthropic").lower()
    strict_json = str(os.getenv("SLO_AI_STRICT_JSON", "false")).lower() in {"1", "true", "yes", "y"}

    payload = {
        "methodology": _load_methodology(),
        "summary": summary,
        "tool_inventory": tool_inventory,
        "production_ready_recommendations": [
            _recommendation_row(rec) for rec in production_ready_recommendations[:10]
        ],
        "candidate_recommendations": [
            _recommendation_row(rec) for rec in candidate_recommendations[:8]
        ],
        "slo_coverage_matrix": [
            _coverage_row(row) for row in slo_coverage_matrix[:30]
        ],
        "existing_slos": existing_slos[:10],
        "collection_errors": collection_errors[:8],
    }

    advisor = _fallback_advisor(payload, status="deterministic")
    advisor["enabled"] = enabled
    advisor["provider"] = provider
    advisor["error"] = None
    advisor["mode"] = "deterministic_advisor"

    if not enabled:
        advisor["status"] = "disabled"
        return advisor

    # The old strict JSON mode is intentionally disabled by default because
    # LLM JSON formatting failures caused SLO Studio to display fallback notes
    # even when the API call succeeded. Keep it only as an explicit escape hatch.
    if strict_json:
        advisor["status"] = "deterministic"
        advisor["error"] = "SLO_AI_STRICT_JSON is enabled, but strict JSON mode has been retired for demo safety."
        return advisor

    prompt = _build_narrative_prompt(payload)

    try:
        if provider == "openai":
            raw, model = _call_openai(prompt)
        else:
            provider = "anthropic"
            raw, model = _call_anthropic(prompt)

        narrative = _sanitize_plain_text(raw)
        if not narrative:
            raise RuntimeError("AI provider returned an empty narrative.")

        advisor["status"] = "generated"
        advisor["provider"] = provider
        advisor["model"] = model
        advisor["mode"] = "narrative_no_json"
        advisor["executive_interpretation"] = narrative
        advisor["leadership_summary"] = (
            "AI advisor narrative generated successfully. Deterministic SLO math remains the source of truth for "
            "objectives, thresholds, confidence, and PromQL."
        )
        advisor["error"] = None
        return advisor

    except Exception as exc:
        advisor = _fallback_advisor(payload, status="fallback")
        advisor["enabled"] = True
        advisor["provider"] = provider
        advisor["error"] = str(exc)
        advisor["mode"] = "deterministic_fallback"
        return advisor
