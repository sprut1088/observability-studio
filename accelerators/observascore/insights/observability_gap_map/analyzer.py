from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict

from observascore.insights.observability_gap_map.models import (
    ApplicationContext,
    GapRecommendation,
    ObservabilityGapMapResult,
    ServiceGapProfile,
    SignalCoverage,
    ToolCoverageSummary,
)
from observascore.insights.observability_gap_map.connectivity_analyzer import analyze_signal_connectivity
from observascore.insights.red_panel_intelligence.analyzer import (
    DURATION_KEYWORDS,
    ERROR_KEYWORDS,
    RATE_KEYWORDS,
)
from observascore.model import AlertRule, Dashboard, DashboardPanel, ObservabilityEstate, Signal, SignalType

logger = logging.getLogger(__name__)

SERVICE_LABEL_KEYS = {
    "service",
    "service.name",
    "service_name",
    "app",
    "application",
    "svc",
    "job",
}

SERVICE_PATTERN = re.compile(
    r"(?:service(?:\.name|_name)?|svc|app(?:lication)?|job|source|sourcetype|index)\s*[=:]\s*['\"]?([A-Za-z0-9_./:-]+)",
    re.IGNORECASE,
)

SERVICE_NAME_PATTERN = re.compile(r"\b([a-z0-9][a-z0-9._-]{2,}(?:service|svc|api|backend|frontend))\b", re.IGNORECASE)
FILE_PATH_PATTERN = re.compile(r"^(?:[a-zA-Z]:\\|/).*")
GENERIC_SERVICE_VALUES = {
    "all",
    "none",
    "null",
    "unknown",
    "prod",
    "production",
    "staging",
    "stage",
    "dev",
    "test",
    "main",
    "platform",
    "system",
    "default",
    "summary",
    "history",
    "search",
    "source",
    "sourcetype",
    "index",
    "logs",
    "metrics",
    "traces",
    "alerts",
}
IGNORED_KEYWORDS = {
    "true",
    "false",
    "if",
    "or",
    "and",
    "mvindex",
    "split",
    "coalesce",
    "match",
}
SPLUNK_INTERNAL_INDEXES = {
    "_internal",
    "_audit",
    "_introspection",
    "_metrics",
    "_telemetry",
    "_thefishbucket",
}
SPLUNK_PLATFORM_OBJECTS = {
    "splunkd",
    "splunkd_access",
    "splunkd_ui_access",
    "splunk_web_access",
    "splunk_telemetry",
    "splunk_resource_usage",
}


def _normalize_token(value: str | None) -> str | None:
    if not value:
        return None
    token = value.strip().strip("\"'").lower()
    if not token or len(token) > 120:
        return None
    if token.startswith("http"):
        return None
    return token


def _canonical_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _is_sanitized_candidate(value: str, explicit_allow: set[str]) -> bool:
    token = _normalize_token(value)
    if not token:
        return False
    if token in explicit_allow:
        return True
    if token in SPLUNK_INTERNAL_INDEXES or token in SPLUNK_PLATFORM_OBJECTS:
        return False
    if token in IGNORED_KEYWORDS or token in GENERIC_SERVICE_VALUES:
        return False
    if token.startswith("_"):
        return False
    if "/opt/" in token:
        return False
    if token.endswith(".log"):
        return False
    if FILE_PATH_PATTERN.match(token) or "/" in token and "service" not in token:
        return False
    if token.isdigit():
        return False
    if len(token) < 3:
        return False
    return True


def _extract_tokens(text: str) -> set[str]:
    if not text:
        return set()
    matches: set[str] = set()
    for item in SERVICE_PATTERN.findall(text):
        token = _normalize_token(item)
        if token:
            matches.add(token)
    for item in SERVICE_NAME_PATTERN.findall(text):
        token = _normalize_token(item)
        if token:
            matches.add(token)
    return matches


def _extract_from_signal(signal: Signal) -> set[str]:
    matches: set[str] = set()
    labels = signal.labels or {}
    for key, value in labels.items():
        value_token = _normalize_token(str(value))
        if key.lower() in SERVICE_LABEL_KEYS and value_token:
            matches.add(value_token)
        matches.update(_extract_tokens(f"{key}={value}"))
    matches.update(_extract_tokens(signal.identifier or ""))
    return matches


def _extract_from_alert(alert: AlertRule) -> set[str]:
    matches: set[str] = set()
    for key, value in (alert.labels or {}).items():
        value_token = _normalize_token(str(value))
        if key.lower() in SERVICE_LABEL_KEYS and value_token:
            matches.add(value_token)
        matches.update(_extract_tokens(f"{key}={value}"))
    matches.update(_extract_tokens(alert.group or ""))
    matches.update(_extract_tokens(alert.name or ""))
    matches.update(_extract_tokens(alert.expression or ""))
    return matches


def _extract_from_panel(panel: DashboardPanel) -> set[str]:
    matches = _extract_tokens(panel.title or "")
    for query in panel.queries or []:
        matches.update(_extract_tokens(query))
    return matches


def _extract_from_dashboard(dashboard: Dashboard) -> set[str]:
    matches: set[str] = set()
    matches.update(_extract_tokens(dashboard.title or ""))
    matches.update(_extract_tokens(dashboard.folder or ""))
    for tag in dashboard.tags or []:
        matches.update(_extract_tokens(tag))
    for panel in dashboard.panels:
        matches.update(_extract_from_panel(panel))
    return matches


def _has_keyword(text: str, keywords: list[str]) -> bool:
    lowered = (text or "").lower()
    return any(keyword in lowered for keyword in keywords)


def _dashboard_red_flags(dashboard: Dashboard) -> tuple[bool, bool, bool]:
    rate_present = _has_keyword(dashboard.title or "", RATE_KEYWORDS)
    errors_present = _has_keyword(dashboard.title or "", ERROR_KEYWORDS)
    duration_present = _has_keyword(dashboard.title or "", DURATION_KEYWORDS)

    for panel in dashboard.panels:
        if _has_keyword(panel.title or "", RATE_KEYWORDS):
            rate_present = True
        if _has_keyword(panel.title or "", ERROR_KEYWORDS):
            errors_present = True
        if _has_keyword(panel.title or "", DURATION_KEYWORDS):
            duration_present = True
        for query in panel.queries or []:
            if _has_keyword(query, RATE_KEYWORDS):
                rate_present = True
            if _has_keyword(query, ERROR_KEYWORDS):
                errors_present = True
            if _has_keyword(query, DURATION_KEYWORDS):
                duration_present = True

    return rate_present, errors_present, duration_present


def _matches_canonical(candidate: str, canonical_service: str) -> bool:
    candidate_key = _canonical_key(candidate)
    service_key = _canonical_key(canonical_service)
    if candidate_key == service_key:
        return True
    if candidate_key.endswith(service_key) or service_key in candidate_key:
        return True
    if service_key.endswith(candidate_key):
        return True
    return False


def _score_coverage(coverage: SignalCoverage) -> int:
    score = 0
    if coverage.metrics_present:
        score += 20
    if coverage.logs_present:
        score += 20
    if coverage.traces_present:
        score += 20
    if coverage.dashboards_present:
        score += 15
    if coverage.alerts_present:
        score += 15
    if coverage.rate_present and coverage.errors_present and coverage.duration_present:
        score += 10
    return min(score, 100)


def _readiness_status(score: int) -> str:
    if score >= 85:
        return "Excellent"
    if score >= 70:
        return "Good"
    if score >= 45:
        return "Partial"
    if score >= 20:
        return "Poor"
    return "Blind Spot"


def _build_recommendations(service: str, coverage: SignalCoverage) -> list[GapRecommendation]:
    recommendations: list[GapRecommendation] = []
    if not coverage.metrics_present:
        recommendations.append(
            GapRecommendation(
                service=service,
                missing_signal="metrics",
                action="Add RED/golden-signal metrics via Prometheus/OpenTelemetry.",
                expected_value="Improves SLO tracking and early anomaly detection.",
                impact="critical",
            )
        )
    if not coverage.logs_present:
        recommendations.append(
            GapRecommendation(
                service=service,
                missing_signal="logs",
                action="Ship structured logs to Splunk/Loki/Elasticsearch.",
                expected_value="Enables fast incident triage and root-cause lookup.",
                impact="critical",
            )
        )
    if not coverage.traces_present:
        recommendations.append(
            GapRecommendation(
                service=service,
                missing_signal="traces",
                action="Instrument distributed tracing using OpenTelemetry.",
                expected_value="Reveals latency bottlenecks and dependency failures.",
                impact="high",
            )
        )
    if not coverage.dashboards_present:
        recommendations.append(
            GapRecommendation(
                service=service,
                missing_signal="dashboards",
                action="Create service-level operational dashboard.",
                expected_value="Improves day-2 operations visibility for responders.",
                impact="high",
            )
        )
    if not coverage.alerts_present:
        recommendations.append(
            GapRecommendation(
                service=service,
                missing_signal="alerts",
                action="Add actionable alerts with severity and runbook.",
                expected_value="Reduces MTTR with clear ownership and actions.",
                impact="high",
            )
        )
    if not (coverage.rate_present and coverage.errors_present and coverage.duration_present):
        recommendations.append(
            GapRecommendation(
                service=service,
                missing_signal="red",
                action="Add Rate, Errors, and Duration dashboard panels.",
                expected_value="Improves service health confidence and release readiness.",
                impact="dashboard",
            )
        )
    return recommendations


def _collect_tool_summaries(
    estate: ObservabilityEstate,
    scoped_services: list[str],
    aliases_by_service: dict[str, set[str]],
) -> list[ToolCoverageSummary]:
    tool_data: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    def mark(tool_name: str, category: str, service: str) -> None:
        tool_data[tool_name][category].add(service)

    for signal in estate.signals:
        observed = _extract_from_signal(signal)
        for service in scoped_services:
            if any(_matches_canonical(candidate, service) for candidate in observed | aliases_by_service[service]):
                if signal.signal_type == SignalType.METRIC:
                    mark(signal.source_tool, "metrics", service)
                elif signal.signal_type == SignalType.LOG:
                    mark(signal.source_tool, "logs", service)
                elif signal.signal_type == SignalType.TRACE:
                    mark(signal.source_tool, "traces", service)

    for alert in estate.alert_rules:
        observed = _extract_from_alert(alert)
        for service in scoped_services:
            if any(_matches_canonical(candidate, service) for candidate in observed | aliases_by_service[service]):
                mark(alert.source_tool, "alerts", service)

    for dashboard in estate.dashboards:
        observed = _extract_from_dashboard(dashboard)
        rate_present, errors_present, duration_present = _dashboard_red_flags(dashboard)
        for service in scoped_services:
            if any(_matches_canonical(candidate, service) for candidate in observed | aliases_by_service[service]):
                mark(dashboard.source_tool, "dashboards", service)
                if rate_present and errors_present and duration_present:
                    mark(dashboard.source_tool, "red", service)

    summary: list[ToolCoverageSummary] = []
    for tool_name, categories in sorted(tool_data.items()):
        all_services = set()
        for values in categories.values():
            all_services.update(values)
        summary.append(
            ToolCoverageSummary(
                tool_name=tool_name,
                metrics_services=len(categories.get("metrics", set())),
                logs_services=len(categories.get("logs", set())),
                traces_services=len(categories.get("traces", set())),
                dashboards_services=len(categories.get("dashboards", set())),
                alerts_services=len(categories.get("alerts", set())),
                red_complete_services=len(categories.get("red", set())),
                total_services=len(all_services),
            )
        )

    return summary


def _normalize_canonical_services(services: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in services:
        token = _normalize_token(item)
        if not token:
            continue
        if token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def analyze_observability_gap_map(
    estate: ObservabilityEstate,
    application_context: ApplicationContext | None = None,
) -> ObservabilityGapMapResult:
    logger.info("Starting Observability Gap Map analysis")

    app_name = "unspecified-application"
    app_env = "unknown"
    canonical_services: list[str] = []
    include_auto = False
    if application_context:
        app_name = application_context.name or app_name
        app_env = application_context.environment or app_env
        canonical_services = _normalize_canonical_services(application_context.services)
        include_auto = application_context.include_auto_discovered

    raw_counts = {
        "signals": len(estate.signals),
        "dashboards": len(estate.dashboards),
        "alerts": len(estate.alert_rules),
    }

    explicit_allow = set(canonical_services)
    observed_candidates: set[str] = set()
    ignored_candidates: set[str] = set()

    def collect(candidates: set[str]) -> None:
        for candidate in candidates:
            token = _normalize_token(candidate)
            if not token:
                continue
            observed_candidates.add(token)
            if not _is_sanitized_candidate(token, explicit_allow=explicit_allow):
                ignored_candidates.add(token)

    for signal in estate.signals:
        collect(_extract_from_signal(signal))
    for alert in estate.alert_rules:
        collect(_extract_from_alert(alert))
    for dashboard in estate.dashboards:
        collect(_extract_from_dashboard(dashboard))

    sanitized_candidates = sorted(
        candidate
        for candidate in observed_candidates
        if _is_sanitized_candidate(candidate, explicit_allow=explicit_allow)
    )

    discovery_mode = False
    report_scope = list(canonical_services)
    if not report_scope:
        discovery_mode = True
        report_scope = list(sanitized_candidates)

    if include_auto and not discovery_mode:
        for candidate in sanitized_candidates:
            if candidate not in report_scope:
                report_scope.append(candidate)

    aliases_by_service = {
        service: {service, service.replace("-", ""), service.replace("_", ""), service.replace(".", "")}
        for service in report_scope
    }

    coverage_by_service: dict[str, SignalCoverage] = defaultdict(SignalCoverage)
    tools_by_service: dict[str, set[str]] = defaultdict(set)

    for signal in estate.signals:
        observed = _extract_from_signal(signal)
        for service in report_scope:
            if any(_matches_canonical(candidate, service) for candidate in observed | aliases_by_service[service]):
                coverage = coverage_by_service[service]
                tools_by_service[service].add(signal.source_tool)
                if signal.signal_type == SignalType.METRIC:
                    coverage.metrics_present = True
                elif signal.signal_type == SignalType.LOG:
                    coverage.logs_present = True
                elif signal.signal_type == SignalType.TRACE:
                    coverage.traces_present = True

    for alert in estate.alert_rules:
        observed = _extract_from_alert(alert)
        for service in report_scope:
            if any(_matches_canonical(candidate, service) for candidate in observed | aliases_by_service[service]):
                coverage = coverage_by_service[service]
                coverage.alerts_present = True
                tools_by_service[service].add(alert.source_tool)

    for dashboard in estate.dashboards:
        observed = _extract_from_dashboard(dashboard)
        rate_present, errors_present, duration_present = _dashboard_red_flags(dashboard)
        for service in report_scope:
            if any(_matches_canonical(candidate, service) for candidate in observed | aliases_by_service[service]):
                coverage = coverage_by_service[service]
                coverage.dashboards_present = True
                coverage.rate_present = coverage.rate_present or rate_present
                coverage.errors_present = coverage.errors_present or errors_present
                coverage.duration_present = coverage.duration_present or duration_present
                tools_by_service[service].add(dashboard.source_tool)

    service_profiles: list[ServiceGapProfile] = []
    recommendations: list[GapRecommendation] = []
    missing_signal_counts: Counter[str] = Counter()

    for service in report_scope:
        coverage = coverage_by_service[service]
        score = _score_coverage(coverage)
        status = _readiness_status(score)

        missing_signals: list[str] = []
        if not coverage.metrics_present:
            missing_signals.append("metrics")
            missing_signal_counts["metrics"] += 1
        if not coverage.logs_present:
            missing_signals.append("logs")
            missing_signal_counts["logs"] += 1
        if not coverage.traces_present:
            missing_signals.append("traces")
            missing_signal_counts["traces"] += 1
        if not coverage.dashboards_present:
            missing_signals.append("dashboards")
            missing_signal_counts["dashboards"] += 1
        if not coverage.alerts_present:
            missing_signals.append("alerts")
            missing_signal_counts["alerts"] += 1
        if not (coverage.rate_present and coverage.errors_present and coverage.duration_present):
            missing_signals.append("red")
            missing_signal_counts["red"] += 1

        profile = ServiceGapProfile(
            service=service,
            coverage=coverage,
            coverage_score=score,
            readiness_status=status,
            missing_signals=missing_signals,
            tools=sorted(tools_by_service[service]),
        )
        service_profiles.append(profile)
        recommendations.extend(_build_recommendations(service=service, coverage=coverage))

    service_profiles.sort(key=lambda item: item.coverage_score)
    total_services = len(service_profiles)
    overall_coverage_score = 0.0
    if total_services:
        overall_coverage_score = round(sum(item.coverage_score for item in service_profiles) / total_services, 2)

    strongest_services = [
        item.service
        for item in sorted(service_profiles, key=lambda item: item.coverage_score, reverse=True)[:5]
    ]
    weakest_services = [item.service for item in service_profiles[:5]]

    sorted_recommendations = sorted(
        recommendations,
        key=lambda item: ({"critical": 0, "high": 1, "dashboard": 2}.get(item.impact, 9), item.service),
    )

    tool_coverage_summary = _collect_tool_summaries(estate, report_scope, aliases_by_service)

    # Analyze signal connectivity for debugging paths
    #connectivity_results, connectivity_summary = analyze_signal_connectivity(estate, canonical_services)

    services_for_connectivity = canonical_services or [
        profile.service for profile in service_profiles
    ]

    connectivity_results, connectivity_summary = analyze_signal_connectivity(
        estate,
        services_for_connectivity,
    )

    return ObservabilityGapMapResult(
        application_name=app_name,
        environment=app_env,
        canonical_services=canonical_services,
        auto_discovered_candidates=sanitized_candidates,
        ignored_candidates=sorted(ignored_candidates),
        total_services=total_services,
        overall_coverage_score=overall_coverage_score,
        service_profiles=service_profiles,
        tool_coverage_summary=tool_coverage_summary,
        top_recommendations=sorted_recommendations[:25],
        raw_counts=raw_counts,
        extraction_errors=list(getattr(estate.summary, "extraction_errors", [])),
        excellent_services=sum(1 for item in service_profiles if item.readiness_status == "Excellent"),
        good_services=sum(1 for item in service_profiles if item.readiness_status == "Good"),
        partial_services=sum(1 for item in service_profiles if item.readiness_status == "Partial"),
        poor_services=sum(1 for item in service_profiles if item.readiness_status == "Poor"),
        blind_spot_services=sum(1 for item in service_profiles if item.readiness_status == "Blind Spot"),
        strongest_services=strongest_services,
        weakest_services=weakest_services,
        gap_map_score=overall_coverage_score,
        service_blind_spots=sum(1 for item in service_profiles if item.readiness_status == "Blind Spot"),
        missing_signal_counts={
            "metrics": missing_signal_counts.get("metrics", 0),
            "logs": missing_signal_counts.get("logs", 0),
            "traces": missing_signal_counts.get("traces", 0),
            "dashboards": missing_signal_counts.get("dashboards", 0),
            "alerts": missing_signal_counts.get("alerts", 0),
            "red": missing_signal_counts.get("red", 0),
        },
        discovery_mode=discovery_mode,
        no_dashboards_found=(len(estate.dashboards) == 0),
        connectivity_results=connectivity_results,
        connectivity_summary=connectivity_summary.to_dict(),
    )
