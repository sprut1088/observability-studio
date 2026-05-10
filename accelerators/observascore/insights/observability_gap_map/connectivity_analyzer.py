from __future__ import annotations

import logging
import re
from collections import defaultdict

from observascore.insights.observability_gap_map.models import (
    ConnectivitySummary,
    SignalConnectivityEvidence,
    SignalConnectivityResult,
)
from observascore.model import ObservabilityEstate, Signal, SignalType

logger = logging.getLogger(__name__)

TRACE_ID_KEYWORDS = {"trace_id", "traceid", "trace-id", "span_id", "spanid", "span-id", "correlation_id", "correlationid", "correlation-id", "request_id", "requestid", "request-id"}
METRICS_KEYWORDS = {"metric", "metric_name", "counter", "gauge", "histogram", "summary"}
LOGS_KEYWORDS = {"log", "logs", "logger", "event", "message", "msg"}
TRACES_KEYWORDS = {"trace", "spans", "span", "jaeger", "tempo", "tracing"}
DASHBOARD_KEYWORDS = {"dashboard", "board", "view", "panel", "grafana"}


def _normalize_name(name: str) -> str:
    """Normalize a name for comparison."""
    return re.sub(r"[_-]", "", name).lower()


def _service_matches(name: str, service: str) -> bool:
    """Check if a name matches or contains the service name."""
    norm_name = _normalize_name(name)
    norm_service = _normalize_name(service)
    return norm_service in norm_name or norm_name in norm_service or norm_service == norm_name


def _extract_keywords(text: str | None, keywords: set[str]) -> list[str]:
    """Extract keywords from text."""
    if not text:
        return []
    text_lower = text.lower()
    found = []
    for kw in keywords:
        if kw in text_lower:
            found.append(kw)
    return found


def _check_metrics_to_logs(estate: ObservabilityEstate, service: str) -> tuple[str, list[str]]:
    """Check if metrics can connect to logs for a service."""
    # Find metrics for service
    metrics = [s for s in estate.signals if s.type == SignalType.METRIC and _service_matches(s.service or "", service)]
    # Find logs for service
    logs = [s for s in estate.signals if s.type == SignalType.LOG and _service_matches(s.service or "", service)]

    if not metrics or not logs:
        return "fail", []

    # Check for common identifiers
    evidence = []
    metric_labels = set()
    for m in metrics:
        if m.labels:
            metric_labels.update(m.labels.keys())

    log_labels = set()
    for l in logs:
        if l.labels:
            log_labels.update(l.labels.keys())

    # Strong connection if both have labels and share common ones
    common_labels = metric_labels & log_labels
    if common_labels and len(common_labels) >= 2:
        evidence = list(common_labels)[:3]
        return "pass", evidence

    # Warn if both exist but weak linkage
    if metrics and logs:
        return "warn", []

    return "fail", []


def _check_logs_to_traces(estate: ObservabilityEstate, service: str) -> tuple[str, list[str]]:
    """Check if logs can connect to traces for a service."""
    logs = [s for s in estate.signals if s.type == SignalType.LOG and _service_matches(s.service or "", service)]
    traces = [s for s in estate.signals if s.type == SignalType.TRACE and _service_matches(s.service or "", service)]

    if not traces:
        return "fail", []

    if not logs:
        return "fail", []

    # Check for trace ID keywords in logs
    evidence = []
    for log in logs:
        keywords = _extract_keywords(log.name, TRACE_ID_KEYWORDS)
        if keywords:
            evidence.extend(keywords[:2])

    if evidence:
        return "pass", evidence

    # If logs exist but no trace IDs found, still warn
    if logs and traces:
        return "warn", []

    return "fail", []


def _check_alerts_to_dashboards(estate: ObservabilityEstate, service: str) -> tuple[str, list[str]]:
    """Check if alerts can connect to dashboards for a service."""
    alerts = [a for a in estate.alert_rules if _service_matches(a.service or "", service)]
    dashboards = [d for d in estate.dashboards if _service_matches(d.name or "", service)]

    if not alerts or not dashboards:
        return "fail", []

    # Check for naming correlation
    alert_names = [_normalize_name(a.name) for a in alerts]
    dashboard_names = [_normalize_name(d.name) for d in dashboards]

    # Strong if clear naming match
    for aname in alert_names:
        for dname in dashboard_names:
            if aname in dname or dname in aname:
                return "pass", [a.name for a in alerts[:2]]

    # Warn if both exist but weak correlation
    return "warn", []


def _check_dashboards_to_logs(estate: ObservabilityEstate, service: str) -> tuple[str, list[str]]:
    """Check if dashboards can link to logs for a service."""
    dashboards = [d for d in estate.dashboards if _service_matches(d.name or "", service)]
    logs = [s for s in estate.signals if s.type == SignalType.LOG and _service_matches(s.service or "", service)]

    if not logs:
        return "fail", []

    if not dashboards:
        return "fail", []

    # Check for log-like queries in dashboard panels
    evidence = []
    for dashboard in dashboards:
        for panel in dashboard.panels or []:
            query = (panel.query or "") if hasattr(panel, "query") else ""
            if _extract_keywords(query, LOGS_KEYWORDS):
                evidence.append(panel.name if hasattr(panel, "name") else "panel")
                break

    if evidence:
        return "pass", evidence[:2]

    # Warn if both exist but generic queries
    if dashboards and logs:
        return "warn", []

    return "fail", []


def _check_dashboards_to_traces(estate: ObservabilityEstate, service: str) -> tuple[str, list[str]]:
    """Check if dashboards can link to traces for a service."""
    dashboards = [d for d in estate.dashboards if _service_matches(d.name or "", service)]
    traces = [s for s in estate.signals if s.type == SignalType.TRACE and _service_matches(s.service or "", service)]

    if not traces:
        return "fail", []

    if not dashboards:
        return "fail", []

    # Check for trace-related queries in dashboard panels
    evidence = []
    for dashboard in dashboards:
        for panel in dashboard.panels or []:
            query = (panel.query or "") if hasattr(panel, "query") else ""
            if _extract_keywords(query, TRACES_KEYWORDS) or _extract_keywords(query, TRACE_ID_KEYWORDS):
                evidence.append(panel.name if hasattr(panel, "name") else "panel")
                break

    if evidence:
        return "pass", evidence[:2]

    # Warn if both exist but no trace linkage
    if dashboards and traces:
        return "warn", []

    return "fail", []


def _score_check(status: str) -> float:
    """Convert check status to score."""
    if status == "pass":
        return 100.0
    if status == "warn":
        return 60.0
    return 0.0


def _compute_connectivity_score(checks: dict[str, str]) -> float:
    """Compute overall connectivity score from individual checks."""
    scores = [_score_check(checks[key]) for key in checks]
    return sum(scores) / len(scores) if scores else 0.0


def _mttr_risk_from_score(score: float) -> str:
    """Determine MTTR risk level from connectivity score."""
    if score >= 80:
        return "low"
    if score >= 50:
        return "medium"
    return "high"


def _build_explanation(service: str, checks: dict[str, str], gaps: list[str]) -> str:
    """Build human-readable explanation of connectivity."""
    score = _compute_connectivity_score(checks)
    failed_checks = [k.replace("_", " → ") for k, v in checks.items() if v == "fail"]
    warned_checks = [k.replace("_", " → ") for k, v in checks.items() if v == "warn"]

    if score >= 80:
        return f"{service} has strong debugging paths across signals."
    if score >= 50:
        if failed_checks:
            return f"{service} has partial paths; missing: {', '.join(failed_checks[:2])}."
        if warned_checks:
            return f"{service} has paths but weak linkage in: {', '.join(warned_checks[:2])}."
        return f"{service} has moderate connectivity."
    return f"{service} has broken debugging paths. Critical gaps: {', '.join(failed_checks[:3])}."


def analyze_signal_connectivity(
    estate: ObservabilityEstate,
    canonical_services: list[str],
) -> tuple[list[SignalConnectivityResult], ConnectivitySummary]:
    """
    Analyze signal connectivity for each service.
    Returns list of SignalConnectivityResult and ConnectivitySummary.
    """
    logger.info("Starting signal connectivity analysis for %d services", len(canonical_services))

    results = []
    scores = []

    for service in canonical_services:
        # Run all 5 connectivity checks
        checks = {
            "metrics_to_logs": _check_metrics_to_logs(estate, service)[0],
            "logs_to_traces": _check_logs_to_traces(estate, service)[0],
            "alerts_to_dashboards": _check_alerts_to_dashboards(estate, service)[0],
            "dashboards_to_logs": _check_dashboards_to_logs(estate, service)[0],
            "dashboards_to_traces": _check_dashboards_to_traces(estate, service)[0],
        }

        # Gather evidence
        evidence_items = []
        _, m2l_evidence = _check_metrics_to_logs(estate, service)
        if m2l_evidence:
            evidence_items.append(SignalConnectivityEvidence("metrics", "logs", m2l_evidence))

        _, l2t_evidence = _check_logs_to_traces(estate, service)
        if l2t_evidence:
            evidence_items.append(SignalConnectivityEvidence("logs", "traces", l2t_evidence))

        _, a2d_evidence = _check_alerts_to_dashboards(estate, service)
        if a2d_evidence:
            evidence_items.append(SignalConnectivityEvidence("alerts", "dashboards", a2d_evidence))

        _, d2l_evidence = _check_dashboards_to_logs(estate, service)
        if d2l_evidence:
            evidence_items.append(SignalConnectivityEvidence("dashboards", "logs", d2l_evidence))

        _, d2t_evidence = _check_dashboards_to_traces(estate, service)
        if d2t_evidence:
            evidence_items.append(SignalConnectivityEvidence("dashboards", "traces", d2t_evidence))

        # Identify gaps
        gaps = [f"No {k.replace('_', ' ')} linkage" for k, v in checks.items() if v == "fail"]

        # Compute score and risk
        overall_score = _compute_connectivity_score(checks)
        mttr_risk = _mttr_risk_from_score(overall_score)
        explanation = _build_explanation(service, checks, gaps)

        result = SignalConnectivityResult(
            service_name=service,
            metrics_to_logs=checks["metrics_to_logs"],
            logs_to_traces=checks["logs_to_traces"],
            alerts_to_dashboards=checks["alerts_to_dashboards"],
            dashboards_to_logs=checks["dashboards_to_logs"],
            dashboards_to_traces=checks["dashboards_to_traces"],
            overall_connectivity_score=overall_score,
            mttr_risk=mttr_risk,
            evidence=evidence_items,
            gaps=gaps,
            explanation=explanation,
        )
        results.append(result)
        scores.append(overall_score)

    # Aggregate summary
    strong_paths = sum(1 for r in results if r.overall_connectivity_score >= 80)
    partial_paths = sum(1 for r in results if 50 <= r.overall_connectivity_score < 80)
    broken_paths = sum(1 for r in results if r.overall_connectivity_score < 50)
    overall_score = sum(scores) / len(scores) if scores else 0.0

    summary = ConnectivitySummary(
        services_with_strong_paths=strong_paths,
        services_with_partial_paths=partial_paths,
        services_with_broken_paths=broken_paths,
        overall_connectivity_score=overall_score,
    )

    logger.info(
        "Connectivity analysis complete: strong=%d partial=%d broken=%d overall=%.1f",
        strong_paths,
        partial_paths,
        broken_paths,
        overall_score,
    )

    return results, summary
