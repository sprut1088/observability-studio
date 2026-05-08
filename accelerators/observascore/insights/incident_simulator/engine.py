from __future__ import annotations

import logging
import re
from collections import defaultdict

from observascore.insights.incident_simulator.models import (
    CheckCategory,
    CheckStatus,
    IncidentCheck,
    IncidentEvidence,
    IncidentRequestContext,
    IncidentSimulationResult,
)
from observascore.model import ObservabilityEstate

logger = logging.getLogger(__name__)

INCIDENT_KEYWORDS = {
    "high_latency": [
        "latency",
        "duration",
        "response_time",
        "response time",
        "elapsed",
        "p95",
        "p99",
        "percentile",
        "slow",
    ],
    "error_spike": [
        "error",
        "errors",
        "error_rate",
        "failure",
        "failed",
        "exception",
        "5xx",
        "4xx",
        "status",
        "unsuccessful",
    ],
    "traffic_drop": ["traffic", "request", "requests", "rate", "throughput", "volume", "count", "rps", "rpm", "drop", "low"],
    "traffic_surge": ["traffic", "request", "requests", "rate", "throughput", "volume", "count", "rps", "rpm", "spike", "high"],
    "service_down": ["up", "down", "availability", "health", "heartbeat", "status", "unavailable", "outage"],
    "dependency_failure": ["dependency", "upstream", "downstream", "external", "timeout", "connection", "unavailable", "5xx", "latency"],
}


def _normalize_service_name(name: str) -> str:
    value = (name or "").strip().lower().replace("_", "-")
    value = re.sub(r"[^a-z0-9-]", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    if "." in value:
        value = value.split(".")[-1]
    for suffix in ("-service", "service"):
        if value.endswith(suffix) and len(value) > len(suffix) + 1:
            value = value[: -len(suffix)].rstrip("-")
            break
    return value


def _service_variants(normalized: str) -> set[str]:
    compact = normalized.replace("-", "")
    return {
        normalized,
        compact,
        f"{normalized}-service",
        f"{compact}service",
    }


def _keywords_for_incident(incident_type: str) -> list[str]:
    return INCIDENT_KEYWORDS.get(incident_type, [])


def _find_keywords_in_text(text: str, keywords: list[str]) -> list[str]:
    lowered = (text or "").lower()
    return [kw for kw in keywords if kw in lowered]


def _service_name_in_text(service_variants: set[str], text: str) -> bool:
    lowered = (text or "").lower()
    normalized = _normalize_service_name(lowered)
    for variant in service_variants:
        if variant and variant in normalized:
            return True
    return False


def _check_detection(
    estate: ObservabilityEstate,
    context: IncidentRequestContext,
    keywords: list[str],
) -> IncidentCheck:
    service_variants = _service_variants(_normalize_service_name(context.service_name))
    evidence: list[IncidentEvidence] = []
    matched_alerts = []

    for alert in estate.alert_rules:
        alert_name = (alert.name or "").lower()
        alert_query = (alert.query or "").lower()
        alert_labels = {k.lower(): v.lower() for k, v in (alert.labels or {}).items()}

        matched_keywords = _find_keywords_in_text(f"{alert_name} {alert_query}", keywords)
        if not matched_keywords:
            continue

        service_match = _service_name_in_text(service_variants, alert_name) or any(
            _service_name_in_text(service_variants, str(v)) for v in alert_labels.values()
        )

        if service_match:
            matched_alerts.append(alert.name)
            confidence = 0.9 if service_match else 0.6
            evidence.append(
                IncidentEvidence(
                    source_tool=alert.source_tool or "unknown",
                    object_type="alert",
                    object_name=alert.name,
                    query_snippet=alert.query[:100] if alert.query else "",
                    matched_keywords=matched_keywords,
                    confidence=confidence,
                )
            )

    has_alerts = len(matched_alerts) > 0
    status: CheckStatus = "pass" if has_alerts else "fail"
    score = 100 if has_alerts else 0
    explanation = f"Found {len(matched_alerts)} alert(s) for {context.service_name}" if has_alerts else "No alerts found for this incident type."
    recommendation = (
        ""
        if has_alerts
        else f"Add an alert for {context.incident_type} with keywords: {', '.join(keywords[:3])}"
    )

    return IncidentCheck(
        category="detection",
        name="Alert Coverage",
        status=status,
        score=score,
        explanation=explanation,
        evidence=evidence,
        recommendation=recommendation,
    )


def _check_visibility(
    estate: ObservabilityEstate,
    context: IncidentRequestContext,
    keywords: list[str],
) -> IncidentCheck:
    service_variants = _service_variants(_normalize_service_name(context.service_name))
    evidence: list[IncidentEvidence] = []

    for dashboard in estate.dashboards:
        dashboard_name = (dashboard.title or "").lower()
        dashboard_tags = {k.lower(): v.lower() for k, v in (dashboard.tags or {}).items()}

        service_match = _service_name_in_text(service_variants, dashboard_name) or any(
            _service_name_in_text(service_variants, str(v)) for v in dashboard_tags.values()
        )

        if not service_match:
            continue

        for panel in dashboard.panels:
            panel_title = (panel.title or "").lower()
            panel_queries = panel.queries or []

            matched_keywords = _find_keywords_in_text(f"{panel_title} {' '.join(panel_queries)}", keywords)
            if matched_keywords:
                evidence.append(
                    IncidentEvidence(
                        source_tool=dashboard.source_tool,
                        object_type="panel",
                        object_name=f"{dashboard.title}/{panel_title}",
                        query_snippet=panel_queries[0][:100] if panel_queries else "",
                        matched_keywords=matched_keywords,
                        confidence=0.85,
                    )
                )

    has_visibility = len(evidence) > 0
    status: CheckStatus = "pass" if has_visibility else "fail"
    score = 100 if has_visibility else 0
    explanation = f"Found {len(evidence)} relevant panel(s)" if has_visibility else "No visibility panels for this incident type."
    recommendation = (
        ""
        if has_visibility
        else f"Add dashboard panel showing {context.incident_type} signals (e.g., latency, errors, traffic)"
    )

    return IncidentCheck(
        category="visibility",
        name="Dashboard Coverage",
        status=status,
        score=score,
        explanation=explanation,
        evidence=evidence,
        recommendation=recommendation,
    )


def _check_diagnosis(
    estate: ObservabilityEstate,
    context: IncidentRequestContext,
) -> IncidentCheck:
    service_variants = _service_variants(_normalize_service_name(context.service_name))
    evidence: list[IncidentEvidence] = []
    has_logs = False
    has_traces = False
    has_metrics = False

    for signal in estate.signals:
        signal_name = (signal.name or "").lower()
        signal_labels = {k.lower(): v.lower() for k, v in (signal.labels or {}).items()}
        service_match = _service_name_in_text(service_variants, signal_name) or any(
            _service_name_in_text(service_variants, str(v)) for v in signal_labels.values()
        )

        if not service_match:
            continue

        if signal.signal_type == "log":
            has_logs = True
            evidence.append(
                IncidentEvidence(
                    source_tool=signal.source_tool or "unknown",
                    object_type="log",
                    object_name=signal.name,
                    confidence=0.8,
                )
            )
        elif signal.signal_type == "trace":
            has_traces = True
            evidence.append(
                IncidentEvidence(
                    source_tool=signal.source_tool or "unknown",
                    object_type="trace",
                    object_name=signal.name,
                    confidence=0.8,
                )
            )
        elif signal.signal_type == "metric":
            has_metrics = True

    signals_found = sum([has_logs, has_traces, has_metrics])
    score = 0
    if has_logs:
        score += 40
    if has_traces:
        score += 35
    if has_metrics:
        score += 25

    status: CheckStatus = "pass" if signals_found >= 2 else ("warn" if signals_found == 1 else "fail")
    explanation = f"Found {signals_found}/3 signal types (logs={has_logs}, traces={has_traces}, metrics={has_metrics})"
    gaps = []
    if not has_logs:
        gaps.append("logs")
    if not has_traces:
        gaps.append("traces")
    if not has_metrics:
        gaps.append("metrics")
    recommendation = f"Add missing signal types: {', '.join(gaps)}" if gaps else ""

    return IncidentCheck(
        category="diagnosis",
        name="Signal Diversity",
        status=status,
        score=score,
        explanation=explanation,
        evidence=evidence,
        recommendation=recommendation,
    )


def _check_response(
    estate: ObservabilityEstate,
    context: IncidentRequestContext,
) -> IncidentCheck:
    service_variants = _service_variants(_normalize_service_name(context.service_name))
    evidence: list[IncidentEvidence] = []

    runbook_found = False
    owner_found = False

    for alert in estate.alert_rules:
        alert_name = (alert.name or "").lower()
        alert_labels = {k.lower(): v.lower() for k, v in (alert.labels or {}).items()}
        service_match = _service_name_in_text(service_variants, alert_name) or any(
            _service_name_in_text(service_variants, str(v)) for v in alert_labels.values()
        )

        if not service_match:
            continue

        annotations = alert.annotations or {}
        if annotations.get("runbook") or annotations.get("runbook_url"):
            runbook_found = True
            evidence.append(
                IncidentEvidence(
                    source_tool=alert.source_tool or "unknown",
                    object_type="alert",
                    object_name=alert.name,
                    matched_keywords=["runbook"],
                    confidence=0.95,
                )
            )

        if "owner" in alert_labels or "team" in alert_labels or "notify" in alert_labels:
            owner_found = True

    score = 0
    if runbook_found:
        score += 50
    if owner_found:
        score += 50

    status: CheckStatus = "pass" if score >= 80 else ("warn" if score >= 40 else "fail")
    explanation = f"Runbook configured: {runbook_found}, Owner/team configured: {owner_found}"
    recommendation = ""
    if not runbook_found:
        recommendation = "Add runbook annotation to alerts for faster response."
    if not owner_found:
        recommendation += " Add owner/team labels to alerts for routing." if recommendation else "Add owner/team labels to alerts for routing."

    return IncidentCheck(
        category="response",
        name="Response Readiness",
        status=status,
        score=score,
        explanation=explanation,
        evidence=evidence,
        recommendation=recommendation,
    )


def simulate_incident(
    estate: ObservabilityEstate,
    context: IncidentRequestContext,
) -> IncidentSimulationResult:
    logger.info(
        "Simulating incident: application=%s service=%s type=%s",
        context.application_name,
        context.service_name,
        context.incident_type,
    )

    keywords = _keywords_for_incident(context.incident_type)

    check_detection = _check_detection(estate, context, keywords)
    check_visibility = _check_visibility(estate, context, keywords)
    check_diagnosis = _check_diagnosis(estate, context)
    check_response = _check_response(estate, context)

    checks = [check_detection, check_visibility, check_diagnosis, check_response]

    detection_score = check_detection.score
    visibility_score = check_visibility.score
    diagnosis_score = check_diagnosis.score
    response_score = check_response.score

    overall_score = (
        (detection_score * 0.30) +
        (visibility_score * 0.25) +
        (diagnosis_score * 0.25) +
        (response_score * 0.20)
    )
    overall_score = round(overall_score, 2)

    if overall_score >= 85:
        readiness_status = "Ready"
    elif overall_score >= 70:
        readiness_status = "Mostly Ready"
    elif overall_score >= 45:
        readiness_status = "At Risk"
    elif overall_score >= 20:
        readiness_status = "High MTTR Risk"
    else:
        readiness_status = "Not Ready"

    gaps = []
    recommendations = []
    evidence_map: dict[str, list[IncidentEvidence]] = defaultdict(list)

    for check in checks:
        if check.status in ("fail", "warn"):
            gaps.append(f"{check.name}: {check.explanation}")
        if check.recommendation:
            recommendations.append(check.recommendation)
        for ev in check.evidence:
            evidence_map[check.category].append(ev)

    extraction_errors = list(getattr(estate.summary, "extraction_errors", []))

    return IncidentSimulationResult(
        application_name=context.application_name,
        environment=context.environment,
        service_name=context.service_name,
        incident_type=context.incident_type,
        overall_readiness_score=overall_score,
        readiness_status=readiness_status,
        detection_score=float(detection_score),
        visibility_score=float(visibility_score),
        diagnosis_score=float(diagnosis_score),
        response_score=float(response_score),
        checks=checks,
        evidence=dict(evidence_map),
        gaps=gaps,
        recommendations=recommendations,
        extraction_errors=extraction_errors,
    )
