from __future__ import annotations

from models import ServiceProfile, SLORecommendation


CORE_SLIS = ["availability", "latency", "error_rate"]


def _matches_service(text: str, service: str) -> bool:
    text_l = text.lower()
    service_l = service.lower()
    return service_l in text_l


def _existing_matches(existing_slos: list[dict], service: str, sli_type: str) -> bool:
    for item in existing_slos:
        text = " ".join(
            str(item.get(key, ""))
            for key in ["service", "name", "slo", "sli_type", "rule_name", "alert_name"]
        ).lower()

        if not _matches_service(text, service):
            continue

        if sli_type == "availability" and (
            "availability" in text or "burn" in text or "errorbudget" in text or "error_budget" in text
        ):
            return True

        if sli_type == "latency" and ("latency" in text or "duration" in text or "p95" in text or "p99" in text):
            return True

        if sli_type == "error_rate" and ("error-rate" in text or "error_rate" in text or "error ratio" in text):
            return True

        if sli_type in text:
            return True

    return False


def _recommendation_for(
    recommendations: list[SLORecommendation],
    service: str,
    sli_type: str,
) -> SLORecommendation | None:
    matches = [
        rec for rec in recommendations
        if rec.service == service and rec.sli_type == sli_type
    ]

    if not matches:
        return None

    return sorted(matches, key=lambda rec: rec.confidence, reverse=True)[0]


def _evidence_types(profile: ServiceProfile) -> set[str]:
    return {ev.signal_type for ev in profile.evidence}


def _evidence_summary(profile: ServiceProfile) -> str:
    sources = sorted({ev.source for ev in profile.evidence if ev.source})
    if not sources:
        return "No supporting telemetry evidence collected."
    return "Evidence available from " + ", ".join(sources) + "."


def build_slo_coverage_matrix(
    profiles: list[ServiceProfile],
    existing_slos: list[dict],
    recommendations: list[SLORecommendation],
) -> list[dict]:
    rows: list[dict] = []

    for profile in profiles:
        evidence_types = _evidence_types(profile)

        for sli_type in CORE_SLIS:
            existing = _existing_matches(existing_slos, profile.name, sli_type)
            rec = _recommendation_for(recommendations, profile.name, sli_type)

            if existing:
                status = "existing"
                action = "Existing SLO coverage detected. Review objective, burn-rate windows, ownership, and alert routing."
            elif rec:
                status = "recommended"
                action = f"Create missing {sli_type} SLO at {rec.objective}% over {rec.window}."
            elif sli_type == "availability" and "availability_trend" not in evidence_types:
                status = "missing_telemetry"
                action = "Expose or standardize request count metrics with HTTP/gRPC status-code labels."
            elif sli_type == "error_rate" and "error_rate_trend" not in evidence_types:
                status = "missing_telemetry"
                action = "Expose error-rate telemetry from status-code based request counters."
            elif sli_type == "latency" and "latency_trend" not in evidence_types:
                status = "missing_telemetry"
                action = "Expose latency histogram buckets so p95/p99 SLOs can be calculated."
            else:
                status = "candidate"
                action = "Validate SLI ownership, business criticality, and production readiness."

            rows.append(
                {
                    "service": profile.name,
                    "sli_type": sli_type,
                    "status": status,
                    "recommended_objective": rec.objective if rec else None,
                    "recommended_window": rec.window if rec else None,
                    "confidence": rec.confidence if rec else None,
                    "evidence_summary": _evidence_summary(profile),
                    "action": action,
                }
            )

    status_order = {"recommended": 0, "missing_telemetry": 1, "candidate": 2, "existing": 3}
    return sorted(rows, key=lambda row: (status_order.get(row["status"], 9), row["service"], row["sli_type"]))
