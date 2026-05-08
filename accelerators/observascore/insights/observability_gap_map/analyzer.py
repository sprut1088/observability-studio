from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict

from observascore.insights.observability_gap_map.models import (
    GapRecommendation,
    ObservabilityGapMapResult,
    ServiceGapProfile,
    SignalCoverage,
    ToolCoverageSummary,
)
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
    "job",
    "source",
    "sourcetype",
    "index",
}

SERVICE_PATTERN = re.compile(
    r"(?:service(?:\.name|_name)?|app(?:lication)?|job|source|sourcetype|index)\s*[=:]\s*['\"]?([A-Za-z0-9_./:-]+)",
    re.IGNORECASE,
)

SERVICE_NAME_PATTERN = re.compile(r"\b([a-z0-9][a-z0-9._-]{2,}(?:service|svc|api|backend|frontend))\b", re.IGNORECASE)

IGNORED_SERVICE_VALUES = {
    "all",
    "none",
    "null",
    "unknown",
    "default",
    "prod",
    "production",
    "staging",
    "stage",
    "dev",
    "test",
    "main",
    "search",
    "logs",
    "metrics",
    "traces",
    "alerts",
}


def _normalize_service_name(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip().strip("\"'").lower()
    if not candidate or candidate in IGNORED_SERVICE_VALUES:
        return None
    if candidate.startswith("http"):
        return None
    if len(candidate) > 80:
        return None
    return candidate


def _extract_from_text(text: str) -> set[str]:
    if not text:
        return set()

    matches = set()
    for match in SERVICE_PATTERN.findall(text):
        normalized = _normalize_service_name(match)
        if normalized:
            matches.add(normalized)

    for match in SERVICE_NAME_PATTERN.findall(text):
        normalized = _normalize_service_name(match)
        if normalized:
            matches.add(normalized)

    return matches


def _extract_from_signal(signal: Signal) -> set[str]:
    matches: set[str] = set()

    labels = signal.labels or {}
    for key, value in labels.items():
        if key.lower() in SERVICE_LABEL_KEYS:
            normalized = _normalize_service_name(str(value))
            if normalized:
                matches.add(normalized)
        matches.update(_extract_from_text(f"{key}={value}"))

    matches.update(_extract_from_text(signal.identifier))
    return matches


def _extract_from_alert(alert: AlertRule) -> set[str]:
    matches: set[str] = set()

    for key, value in (alert.labels or {}).items():
        if key.lower() in {"service", "service_name", "app", "application"}:
            normalized = _normalize_service_name(str(value))
            if normalized:
                matches.add(normalized)
        matches.update(_extract_from_text(f"{key}={value}"))

    matches.update(_extract_from_text(alert.group or ""))
    matches.update(_extract_from_text(alert.name or ""))
    matches.update(_extract_from_text(alert.expression or ""))
    return matches


def _extract_from_dashboard(dashboard: Dashboard) -> set[str]:
    matches: set[str] = set()

    matches.update(_extract_from_text(dashboard.title or ""))
    matches.update(_extract_from_text(dashboard.folder or ""))

    for tag in dashboard.tags or []:
        matches.update(_extract_from_text(tag))

    for panel in dashboard.panels:
        matches.update(_extract_from_panel(panel))

    return matches


def _extract_from_panel(panel: DashboardPanel) -> set[str]:
    matches: set[str] = set()
    matches.update(_extract_from_text(panel.title or ""))
    for query in panel.queries or []:
        matches.update(_extract_from_text(query))
    return matches


def _has_keyword(text: str, keywords: list[str]) -> bool:
    lowered = (text or "").lower()
    return any(keyword in lowered for keyword in keywords)


def _dashboard_red_flags(dashboard: Dashboard) -> tuple[bool, bool, bool]:
    rate_present = _has_keyword(dashboard.title or "", RATE_KEYWORDS)
    errors_present = _has_keyword(dashboard.title or "", ERROR_KEYWORDS)
    duration_present = _has_keyword(dashboard.title or "", DURATION_KEYWORDS)

    for panel in dashboard.panels:
        panel_text = panel.title or ""
        if _has_keyword(panel_text, RATE_KEYWORDS):
            rate_present = True
        if _has_keyword(panel_text, ERROR_KEYWORDS):
            errors_present = True
        if _has_keyword(panel_text, DURATION_KEYWORDS):
            duration_present = True

        for query in panel.queries or []:
            if _has_keyword(query, RATE_KEYWORDS):
                rate_present = True
            if _has_keyword(query, ERROR_KEYWORDS):
                errors_present = True
            if _has_keyword(query, DURATION_KEYWORDS):
                duration_present = True

    return rate_present, errors_present, duration_present


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


def analyze_observability_gap_map(estate: ObservabilityEstate) -> ObservabilityGapMapResult:
    logger.info("Starting Observability Gap Map analysis")

    coverage_by_service: dict[str, SignalCoverage] = defaultdict(SignalCoverage)
    tools_by_service: dict[str, set[str]] = defaultdict(set)
    tool_category_services: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    raw_counts = {
        "signals": len(estate.signals),
        "dashboards": len(estate.dashboards),
        "alerts": len(estate.alert_rules),
    }

    inferred_services: set[str] = set()

    for signal in estate.signals:
        services = _extract_from_signal(signal)
        if not services:
            services = {"unknown"}
        inferred_services.update(services)

        for service in services:
            coverage = coverage_by_service[service]
            tools_by_service[service].add(signal.source_tool)
            if signal.signal_type == SignalType.METRIC:
                coverage.metrics_present = True
                tool_category_services[signal.source_tool]["metrics"].add(service)
            elif signal.signal_type == SignalType.LOG:
                coverage.logs_present = True
                tool_category_services[signal.source_tool]["logs"].add(service)
            elif signal.signal_type == SignalType.TRACE:
                coverage.traces_present = True
                tool_category_services[signal.source_tool]["traces"].add(service)

    for alert in estate.alert_rules:
        services = _extract_from_alert(alert)
        if not services:
            services = {"platform"}
        inferred_services.update(services)

        for service in services:
            coverage = coverage_by_service[service]
            coverage.alerts_present = True
            tools_by_service[service].add(alert.source_tool)
            tool_category_services[alert.source_tool]["alerts"].add(service)

    for dashboard in estate.dashboards:
        services = _extract_from_dashboard(dashboard)
        if not services:
            services = {"platform"}
        inferred_services.update(services)

        rate_present, errors_present, duration_present = _dashboard_red_flags(dashboard)

        for service in services:
            coverage = coverage_by_service[service]
            coverage.dashboards_present = True
            coverage.rate_present = coverage.rate_present or rate_present
            coverage.errors_present = coverage.errors_present or errors_present
            coverage.duration_present = coverage.duration_present or duration_present
            tools_by_service[service].add(dashboard.source_tool)
            tool_category_services[dashboard.source_tool]["dashboards"].add(service)
            if rate_present and errors_present and duration_present:
                tool_category_services[dashboard.source_tool]["red"].add(service)

    if not inferred_services:
        logger.warning("No services inferred from normalized estate objects")
        return ObservabilityGapMapResult(
            total_services=0,
            excellent_services=0,
            good_services=0,
            partial_services=0,
            poor_services=0,
            blind_spot_services=0,
            overall_coverage_score=0.0,
            weakest_services=[],
            strongest_services=[],
            service_profiles=[],
            tool_coverage_summary=[],
            top_recommendations=[
                GapRecommendation(
                    service="platform",
                    missing_signal="service_labels",
                    action="No services could be confidently inferred from the selected tools.",
                    expected_value="Add consistent service labels (service/app/job/source) to improve mapping quality.",
                    impact="critical",
                )
            ],
            extraction_errors=list(getattr(estate.summary, "extraction_errors", [])),
            gap_map_score=0.0,
            service_blind_spots=0,
            missing_signal_counts={
                "metrics": 0,
                "logs": 0,
                "traces": 0,
                "dashboards": 0,
                "alerts": 0,
                "red": 0,
            },
            no_services_inferred=True,
            no_dashboards_found=(len(estate.dashboards) == 0),
            raw_counts=raw_counts,
        )

    service_profiles: list[ServiceGapProfile] = []
    recommendations: list[GapRecommendation] = []
    missing_signal_counts: Counter[str] = Counter()

    for service in sorted(inferred_services):
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
    excellent_services = sum(1 for item in service_profiles if item.readiness_status == "Excellent")
    good_services = sum(1 for item in service_profiles if item.readiness_status == "Good")
    partial_services = sum(1 for item in service_profiles if item.readiness_status == "Partial")
    poor_services = sum(1 for item in service_profiles if item.readiness_status == "Poor")
    blind_spot_services = sum(1 for item in service_profiles if item.readiness_status == "Blind Spot")

    overall_coverage_score = round(sum(item.coverage_score for item in service_profiles) / total_services, 2)

    strongest_services = [item.service for item in sorted(service_profiles, key=lambda item: item.coverage_score, reverse=True)[:5]]
    weakest_services = [item.service for item in service_profiles[:5]]

    sorted_recommendations = sorted(
        recommendations,
        key=lambda item: ({"critical": 0, "high": 1, "dashboard": 2}.get(item.impact, 9), item.service),
    )

    tool_coverage_summary: list[ToolCoverageSummary] = []
    for tool_name, categories in sorted(tool_category_services.items()):
        all_services = set()
        for services in categories.values():
            all_services.update(services)

        tool_coverage_summary.append(
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

    logger.info(
        "Gap map complete. Services=%d, overall_score=%.2f, blind_spots=%d",
        total_services,
        overall_coverage_score,
        blind_spot_services,
    )

    return ObservabilityGapMapResult(
        total_services=total_services,
        excellent_services=excellent_services,
        good_services=good_services,
        partial_services=partial_services,
        poor_services=poor_services,
        blind_spot_services=blind_spot_services,
        overall_coverage_score=overall_coverage_score,
        weakest_services=weakest_services,
        strongest_services=strongest_services,
        service_profiles=service_profiles,
        tool_coverage_summary=tool_coverage_summary,
        top_recommendations=sorted_recommendations[:25],
        extraction_errors=list(getattr(estate.summary, "extraction_errors", [])),
        gap_map_score=overall_coverage_score,
        service_blind_spots=blind_spot_services,
        missing_signal_counts={
            "metrics": missing_signal_counts.get("metrics", 0),
            "logs": missing_signal_counts.get("logs", 0),
            "traces": missing_signal_counts.get("traces", 0),
            "dashboards": missing_signal_counts.get("dashboards", 0),
            "alerts": missing_signal_counts.get("alerts", 0),
            "red": missing_signal_counts.get("red", 0),
        },
        no_services_inferred=False,
        no_dashboards_found=(len(estate.dashboards) == 0),
        raw_counts=raw_counts,
    )
