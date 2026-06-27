from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import requests


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _load_methodology() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "knowledge" / "google_sre_slo_methodology.json"
    try:
        return json.loads(path.read_text())
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
            or _get(rec, "description")
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
            or _get(row, "recommended_objective_text")
            or _get(row, "objective_display")
        ),
        "action": _get(row, "action"),
        "evidence_summary": _get(row, "evidence_summary"),
    }


def _extract_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(r"\{.*\}", text or "", re.S)
    if match:
        return json.loads(match.group(0))

    raise ValueError("AI response did not contain valid JSON.")


def _safe_str(value: Any, max_len: int = 500) -> str:
    text = str(value or "")
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _fallback_advisor(payload: dict[str, Any], status: str = "deterministic_fallback") -> dict[str, Any]:
    recommendations = payload.get("production_ready_recommendations", [])
    coverage = payload.get("slo_coverage_matrix", [])
    summary = payload.get("summary", {})

    top_actions = []
    for index, rec in enumerate(recommendations[:5], start=1):
        display = rec.get("objective_display") or f"{rec.get('objective')} over {rec.get('window')}"
        top_actions.append(
            {
                "priority": index,
                "service": rec.get("service"),
                "action": f"Create {rec.get('sli_type')} SLO",
                "recommended_slo": display,
                "why": _safe_str(rec.get("rationale") or "Evidence-backed deterministic recommendation.", 900),
                "owner": "Service owner and SRE team",
                "risk": "medium",
            }
        )

    questions = []
    for row in coverage[:15]:
        service = row.get("service")
        sli = row.get("sli_type")
        row_status = row.get("status")

        if row_status == "recommended":
            questions.append(
                {
                    "service": service,
                    "question": (
                        f"Does the service owner agree that the recommended {sli} SLO is "
                        "user-relevant, measurable, and suitable for production rollout?"
                    ),
                }
            )
        elif row_status == "missing_telemetry":
            questions.append(
                {
                    "service": service,
                    "question": f"What instrumentation is needed before a production-grade {sli} SLO can be adopted?",
                }
            )

    return {
        "status": status,
        "executive_interpretation": (
            f"SLO Studio analyzed {summary.get('service_count')} services over "
            f"{summary.get('lookback_days')} days and produced "
            f"{summary.get('production_ready_slo_count')} evidence-backed recommendations. "
            "The deterministic engine calculated the SLO objectives; the AI Advisor organizes evidence into rollout actions."
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
            "AI translated deterministic SLO findings into a leadership-ready rollout plan for service owners, SREs, and governance teams."
        ),
    }


def _call_anthropic(prompt: str) -> str:
    api_key = (
        os.getenv("SLO_AI_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("CLAUDE_API_KEY")
        or os.getenv("AI_API_KEY")
    )
    model = (
        os.getenv("SLO_AI_MODEL")
        or os.getenv("ANTHROPIC_MODEL")
        or os.getenv("AI_MODEL")
        or "claude-sonnet-4-6"
    )

    if not api_key:
        raise RuntimeError("No Anthropic API key found. Set ANTHROPIC_API_KEY or SLO_AI_API_KEY.")

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        json={
            "model": model,
            "max_tokens": 1800,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=45,
    )
    response.raise_for_status()

    data = response.json()
    return "\n".join(
        block.get("text", "")
        for block in data.get("content", [])
        if block.get("type") == "text"
    ).strip()


def _call_openai(prompt: str) -> str:
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
        raise RuntimeError("No OpenAI API key found. Set OPENAI_API_KEY or SLO_AI_API_KEY.")

    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        },
        json={
            "model": model,
            "temperature": 0.2,
            "max_tokens": 1800,
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
        timeout=45,
    )
    response.raise_for_status()

    data = response.json()
    return data["choices"][0]["message"]["content"].strip()


def _build_prompt(payload: dict[str, Any]) -> str:
    return f"""
You are creating an AI Advisor section for SLO Studio.

SLO Studio already calculated deterministic SLO recommendations. Your job is to explain and prioritize them for leadership and service owners.

Hard rules:
- Do not change any SLO objective.
- Do not change any latency threshold.
- Do not change confidence values.
- Do not invent telemetry.
- Do not claim AI calculated the SLOs.
- Use Google SRE methodology only as advisory guidance.
- Use only the evidence in the JSON.
- Output valid JSON only.

Return this JSON shape:
{{
  "status": "generated",
  "executive_interpretation": "string",
  "top_actions": [
    {{
      "priority": 1,
      "service": "string",
      "action": "string",
      "recommended_slo": "string",
      "why": "string",
      "owner": "string",
      "risk": "low|medium|high"
    }}
  ],
  "governance_notes": ["string"],
  "service_owner_questions": [
    {{
      "service": "string",
      "question": "string"
    }}
  ],
  "rollout_plan": ["string"],
  "leadership_summary": "string"
}}

Evidence JSON:
{json.dumps(payload, indent=2)}
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
    methodology = _load_methodology()

    payload = {
        "methodology": methodology,
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

    if not enabled:
        advisor = _fallback_advisor(payload, status="disabled")
        advisor["enabled"] = False
        advisor["provider"] = provider
        advisor["error"] = None
        return advisor

    prompt = _build_prompt(payload)

    try:
        if provider == "openai":
            raw = _call_openai(prompt)
        else:
            provider = "anthropic"
            raw = _call_anthropic(prompt)

        advisor = _extract_json(raw)
        advisor["enabled"] = True
        advisor["provider"] = provider
        advisor["error"] = None

        if not advisor.get("status"):
            advisor["status"] = "generated"

        return advisor

    except Exception as exc:
        advisor = _fallback_advisor(payload, status="fallback")
        advisor["enabled"] = True
        advisor["provider"] = provider
        advisor["error"] = _safe_str(exc, 220)
        return advisor
