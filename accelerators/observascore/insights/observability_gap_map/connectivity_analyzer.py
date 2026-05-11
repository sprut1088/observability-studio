from __future__ import annotations

import logging
import re
from typing import Any

from observascore.insights.observability_gap_map.models import (
    ConnectivitySummary,
    SignalConnectivityEvidence,
    SignalConnectivityResult,
)
from observascore.model import ObservabilityEstate, Signal, SignalType

logger = logging.getLogger(__name__)

TRACE_ID_KEYWORDS = {
    "trace_id", "traceid", "trace-id",
    "span_id", "spanid", "span-id",
    "correlation_id", "correlationid", "correlation-id",
    "request_id", "requestid", "request-id",
}
LOGS_KEYWORDS = {"log", "logs", "logger", "event", "message", "msg", "splunk", "loki"}
TRACES_KEYWORDS = {"trace", "traces", "spans", "span", "jaeger", "tempo", "tracing"}


def _normalize_name(name: str | None) -> str:
    value = str(name or "").lower().strip()
    value = value.replace("_", "-")
    value = re.sub(r"[^a-z0-9.-]+", "", value)
    value = value.replace("-service", "").replace(".service", "").replace("service", "")
    return value.replace("-", "").replace("_", "").replace(".", "")


def _service_matches(value: str | None, service: str) -> bool:
    norm_value = _normalize_name(value)
    norm_service = _normalize_name(service)

    if not norm_value or not norm_service:
        return False

    return (
        norm_value == norm_service
        or norm_service in norm_value
        or norm_value in norm_service
    )


def _extract_keywords(text: str | None, keywords: set[str]) -> list[str]:
    if not text:
        return []

    text_lower = str(text).lower()
    return [kw for kw in keywords if kw in text_lower]


def _signal_matches_service(signal: Signal, service: str) -> bool:
    values: list[str] = [
        getattr(signal, "identifier", "") or "",
        getattr(signal, "name", "") or "",
        getattr(signal, "source_tool", "") or "",
    ]

    labels = getattr(signal, "labels", {}) or {}
    values.extend(str(k) for k in labels.keys())
    values.extend(str(v) for v in labels.values())

    metadata = getattr(signal, "metadata", {}) or {}
    if isinstance(metadata, dict):
        values.extend(str(k) for k in metadata.keys())
        values.extend(str(v) for v in metadata.values())

    return any(_service_matches(value, service) for value in values)


def _dashboard_matches_service(dashboard: Any, service: str) -> bool:
    values: list[str] = [
        getattr(dashboard, "title", "") or "",
        getattr(dashboard, "uid", "") or "",
        getattr(dashboard, "folder", "") or "",
        getattr(dashboard, "source_tool", "") or "",
    ]

    values.extend(getattr(dashboard, "tags", []) or [])

    for panel in getattr(dashboard, "panels", []) or []:
        values.append(getattr(panel, "title", "") or "")
        values.append(getattr(panel, "panel_type", "") or "")
        values.extend(_panel_queries(panel))

    return any(_service_matches(value, service) for value in values)


def _alert_matches_service(alert: Any, service: str) -> bool:
    values: list[str] = [
        getattr(alert, "name", "") or "",
        getattr(alert, "query", "") or "",
        getattr(alert, "expression", "") or "",
        getattr(alert, "group", "") or "",
        getattr(alert, "source_tool", "") or "",
    ]

    labels = getattr(alert, "labels", {}) or {}
    if isinstance(labels, dict):
        values.extend(str(k) for k in labels.keys())
        values.extend(str(v) for v in labels.values())

    annotations = getattr(alert, "annotations", {}) or {}
    if isinstance(annotations, dict):
        values.extend(str(k) for k in annotations.keys())
        values.extend(str(v) for v in annotations.values())

    return any(_service_matches(value, service) for value in values)


def _panel_queries(panel: Any) -> list[str]:
    queries = getattr(panel, "queries", []) or []

    if isinstance(queries, str):
        return [queries]

    if isinstance(queries, list):
        return [str(query) for query in queries if query]

    return []


def _check_metrics_to_logs(estate: ObservabilityEstate, service: str) -> tuple[str, list[str]]:
    metrics = [
        signal for signal in estate.signals
        if signal.signal_type == SignalType.METRIC and _signal_matches_service(signal, service)
    ]

    logs = [
        signal for signal in estate.signals
        if signal.signal_type == SignalType.LOG and _signal_matches_service(signal, service)
    ]

    if not metrics or not logs:
        return "fail", []

    metric_labels: set[str] = set()
    for metric in metrics:
        metric_labels.update((getattr(metric, "labels", {}) or {}).keys())

    log_labels: set[str] = set()
    for log in logs:
        log_labels.update((getattr(log, "labels", {}) or {}).keys())

    common_labels = metric_labels & log_labels

    if common_labels:
        return "pass", sorted(common_labels)[:3]

    return "warn", []


def _check_logs_to_traces(estate: ObservabilityEstate, service: str) -> tuple[str, list[str]]:
    logs = [
        signal for signal in estate.signals
        if signal.signal_type == SignalType.LOG and _signal_matches_service(signal, service)
    ]

    traces = [
        signal for signal in estate.signals
        if signal.signal_type == SignalType.TRACE and _signal_matches_service(signal, service)
    ]

    if not logs or not traces:
        return "fail", []

    evidence: list[str] = []

    for log in logs:
        values = [
            getattr(log, "identifier", "") or "",
            getattr(log, "name", "") or "",
        ]

        labels = getattr(log, "labels", {}) or {}
        values.extend(str(k) for k in labels.keys())
        values.extend(str(v) for v in labels.values())

        for value in values:
            evidence.extend(_extract_keywords(value, TRACE_ID_KEYWORDS))

    evidence = list(dict.fromkeys(evidence))

    if evidence:
        return "pass", evidence[:3]

    return "warn", []


def _check_alerts_to_dashboards(estate: ObservabilityEstate, service: str) -> tuple[str, list[str]]:
    alerts = [alert for alert in estate.alert_rules if _alert_matches_service(alert, service)]
    dashboards = [dashboard for dashboard in estate.dashboards if _dashboard_matches_service(dashboard, service)]

    if not alerts or not dashboards:
        return "fail", []

    alert_names = [_normalize_name(getattr(alert, "name", "") or "") for alert in alerts]
    dashboard_names = [_normalize_name(getattr(dashboard, "title", "") or "") for dashboard in dashboards]

    for alert_name in alert_names:
        for dashboard_name in dashboard_names:
            if alert_name and dashboard_name and (alert_name in dashboard_name or dashboard_name in alert_name):
                return "pass", [getattr(alert, "name", "alert") for alert in alerts[:2]]

    return "warn", [
        f"{len(alerts)} alert(s)",
        f"{len(dashboards)} dashboard(s)",
    ]


def _check_dashboards_to_logs(estate: ObservabilityEstate, service: str) -> tuple[str, list[str]]:
    dashboards = [dashboard for dashboard in estate.dashboards if _dashboard_matches_service(dashboard, service)]

    logs = [
        signal for signal in estate.signals
        if signal.signal_type == SignalType.LOG and _signal_matches_service(signal, service)
    ]

    if not dashboards or not logs:
        return "fail", []

    evidence: list[str] = []

    for dashboard in dashboards:
        for panel in getattr(dashboard, "panels", []) or []:
            panel_title = getattr(panel, "title", "") or "panel"
            for query in _panel_queries(panel):
                if _extract_keywords(query, LOGS_KEYWORDS):
                    evidence.append(panel_title)
                    break

    evidence = list(dict.fromkeys(evidence))

    if evidence:
        return "pass", evidence[:2]

    return "warn", [
        f"{len(dashboards)} dashboard(s)",
        f"{len(logs)} log signal(s)",
    ]


def _check_dashboards_to_traces(estate: ObservabilityEstate, service: str) -> tuple[str, list[str]]:
    dashboards = [dashboard for dashboard in estate.dashboards if _dashboard_matches_service(dashboard, service)]

    traces = [
        signal for signal in estate.signals
        if signal.signal_type == SignalType.TRACE and _signal_matches_service(signal, service)
    ]

    if not dashboards or not traces:
        return "fail", []

    evidence: list[str] = []

    for dashboard in dashboards:
        for panel in getattr(dashboard, "panels", []) or []:
            panel_title = getattr(panel, "title", "") or "panel"
            for query in _panel_queries(panel):
                if _extract_keywords(query, TRACES_KEYWORDS) or _extract_keywords(query, TRACE_ID_KEYWORDS):
                    evidence.append(panel_title)
                    break

    evidence = list(dict.fromkeys(evidence))

    if evidence:
        return "pass", evidence[:2]

    return "warn", [
        f"{len(dashboards)} dashboard(s)",
        f"{len(traces)} trace signal(s)",
    ]


def _score_check(status: str) -> float:
    if status == "pass":
        return 100.0
    if status == "warn":
        return 60.0
    return 0.0


def _compute_connectivity_score(checks: dict[str, str]) -> float:
    scores = [_score_check(status) for status in checks.values()]
    return sum(scores) / len(scores) if scores else 0.0


def _mttr_risk_from_score(score: float) -> str:
    if score >= 80:
        return "low"
    if score >= 50:
        return "medium"
    return "high"


def _build_explanation(service: str, checks: dict[str, str]) -> str:
    score = _compute_connectivity_score(checks)
    failed_checks = [key.replace("_", " → ") for key, value in checks.items() if value == "fail"]
    warned_checks = [key.replace("_", " → ") for key, value in checks.items() if value == "warn"]

    if score >= 80:
        return f"{service} has strong debugging paths across signals."

    if score >= 50:
        if failed_checks:
            return f"{service} has partial debugging paths; missing: {', '.join(failed_checks[:2])}."
        if warned_checks:
            return f"{service} has signal coverage, but weak linkage in: {', '.join(warned_checks[:2])}."
        return f"{service} has moderate connectivity."

    if failed_checks:
        return f"{service} has broken debugging paths. Critical gaps: {', '.join(failed_checks[:3])}."

    return f"{service} has weak debugging-path connectivity."


def _build_result_for_service(estate: ObservabilityEstate, service: str) -> SignalConnectivityResult:
    check_results = {
        "metrics_to_logs": _check_metrics_to_logs(estate, service),
        "logs_to_traces": _check_logs_to_traces(estate, service),
        "alerts_to_dashboards": _check_alerts_to_dashboards(estate, service),
        "dashboards_to_logs": _check_dashboards_to_logs(estate, service),
        "dashboards_to_traces": _check_dashboards_to_traces(estate, service),
    }

    checks = {name: result[0] for name, result in check_results.items()}

    evidence_items: list[SignalConnectivityEvidence] = []

    source_target = {
        "metrics_to_logs": ("metrics", "logs"),
        "logs_to_traces": ("logs", "traces"),
        "alerts_to_dashboards": ("alerts", "dashboards"),
        "dashboards_to_logs": ("dashboards", "logs"),
        "dashboards_to_traces": ("dashboards", "traces"),
    }

    for check_name, (_, evidence) in check_results.items():
        if not evidence:
            continue

        source, target = source_target[check_name]
        evidence_items.append(SignalConnectivityEvidence(source, target, evidence))

    gaps = [f"No {name.replace('_', ' ')} linkage" for name, status in checks.items() if status == "fail"]

    overall_score = _compute_connectivity_score(checks)

    return SignalConnectivityResult(
        service_name=service,
        metrics_to_logs=checks["metrics_to_logs"],
        logs_to_traces=checks["logs_to_traces"],
        alerts_to_dashboards=checks["alerts_to_dashboards"],
        dashboards_to_logs=checks["dashboards_to_logs"],
        dashboards_to_traces=checks["dashboards_to_traces"],
        overall_connectivity_score=overall_score,
        mttr_risk=_mttr_risk_from_score(overall_score),
        evidence=evidence_items,
        gaps=gaps,
        explanation=_build_explanation(service, checks),
    )


def analyze_signal_connectivity(
    estate: ObservabilityEstate,
    canonical_services: list[str],
) -> tuple[list[SignalConnectivityResult], ConnectivitySummary]:
    logger.info("Starting signal connectivity analysis for %d services", len(canonical_services))

    services = [service for service in canonical_services if str(service).strip()]
    results = [_build_result_for_service(estate, service) for service in services]

    strong_paths = sum(1 for result in results if result.overall_connectivity_score >= 80)
    partial_paths = sum(1 for result in results if 50 <= result.overall_connectivity_score < 80)
    broken_paths = sum(1 for result in results if result.overall_connectivity_score < 50)
    overall_score = (
        sum(result.overall_connectivity_score for result in results) / len(results)
        if results
        else 0.0
    )

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