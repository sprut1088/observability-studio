from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict

from observascore.insights.red_panel_intelligence.models import (
    RedDashboardAppendix,
    RedEvidence,
    RedPanelIntelligenceResult,
    RedServiceCoverage,
    RedSignalCoverage,
)
from observascore.model import Dashboard, DashboardPanel, ObservabilityEstate

logger = logging.getLogger(__name__)

RATE_KEYWORDS = [
    "rate",
    "throughput",
    "throughput",
    "traffic",
    "rps",
    "rpm",
    "count",
    "requests",
    "volume",
    "qps",
]

ERROR_KEYWORDS = [
    "error",
    "errors",
    "failed",
    "exception",
    "4xx",
    "5xx",
    "timeout",
    "retries",
]

DURATION_KEYWORDS = [
    "latency",
    "duration",
    "response time",
    "response_time",
    "p95",
    "p99",
    "percentile",
    "histogram_quantile",
]

IGNORE_DISCOVERY_TOKENS = {
    "http",
    "https",
    "api",
    "prod",
    "production",
    "stage",
    "staging",
    "dev",
    "test",
    "requests",
    "request",
    "latency",
    "duration",
    "error",
    "errors",
    "rate",
    "status",
    "service",
    "services",
    "cluster",
    "namespace",
    "pod",
    "instance",
    "host",
    "node",
    "team",
    "dashboard",
    "overview",
    "total",
    "count",
}

FILTERED_DASHBOARD_HINTS = [
    "infra",
    "infrastructure",
    "cluster",
    "node",
    "kubernetes",
    "k8s",
    "platform",
    "ops",
    "global",
    "overview",
]

SERVICE_CAPTURE_PATTERNS = [
    re.compile(r"service\s*=\s*['\"]?([a-zA-Z0-9_.:/-]+)['\"]?", re.IGNORECASE),
    re.compile(r"service_name\s*=\s*['\"]?([a-zA-Z0-9_.:/-]+)['\"]?", re.IGNORECASE),
    re.compile(r"service\s*:\s*['\"]?([a-zA-Z0-9_.:/-]+)['\"]?", re.IGNORECASE),
    re.compile(r"service:([a-zA-Z0-9_.:/-]+)", re.IGNORECASE),
]


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


def _find_keywords(text: str, keywords: list[str]) -> list[str]:
    lowered = (text or "").lower()
    return [kw for kw in keywords if kw in lowered]


def _score_status(rate_present: bool, errors_present: bool, duration_present: bool) -> tuple[int, str]:
    present_count = int(rate_present) + int(errors_present) + int(duration_present)
    if present_count == 3:
        return 100, "complete"
    if present_count == 2:
        return 67, "partial"
    if present_count == 1:
        return 33, "weak"
    return 0, "blind"


def _recommendations_for_service(rate_present: bool, errors_present: bool, duration_present: bool) -> list[str]:
    recommendations: list[str] = []
    if not rate_present:
        recommendations.append("Add Rate query (for example: sum(rate(http_requests_total[5m])))")
    if not errors_present:
        recommendations.append("Add Error query (for example: sum(rate(http_requests_total{status=~'5..'}[5m])))")
    if not duration_present:
        recommendations.append(
            "Add Duration query (for example: histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m])))"
        )
    return recommendations


def _signal_matches(text: str) -> dict[str, list[str]]:
    lowered = (text or "").lower()
    return {
        "rate": _find_keywords(lowered, RATE_KEYWORDS),
        "errors": _find_keywords(lowered, ERROR_KEYWORDS),
        "duration": _find_keywords(lowered, DURATION_KEYWORDS),
    }


def _extract_candidates_from_text(text: str) -> set[str]:
    lowered = (text or "").lower()
    candidates: set[str] = set()
    for pattern in SERVICE_CAPTURE_PATTERNS:
        for match in pattern.findall(lowered):
            normalized = _normalize_service_name(match)
            if normalized and normalized not in IGNORE_DISCOVERY_TOKENS:
                candidates.add(normalized)
    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", lowered):
        normalized = _normalize_service_name(token)
        if (
            normalized
            and len(normalized) >= 3
            and normalized not in IGNORE_DISCOVERY_TOKENS
            and not normalized.isdigit()
        ):
            if token.endswith("service") or "svc" in token or "api" in token:
                candidates.add(normalized)
    return candidates


def _dashboard_has_context(
    dashboard: Dashboard,
    application_name: str,
    canonical_normalized: set[str],
) -> bool:
    title = (dashboard.title or "").lower()
    app_norm = _normalize_service_name(application_name)
    if app_norm and app_norm in _normalize_service_name(title):
        return True
    for service in canonical_normalized:
        if service and service in _normalize_service_name(title):
            return True
    for panel in dashboard.panels:
        panel_text = (panel.title or "").lower()
        if app_norm and app_norm in _normalize_service_name(panel_text):
            return True
        for service in canonical_normalized:
            if service and service in _normalize_service_name(panel_text):
                return True
        for query in panel.queries:
            normalized_query = _normalize_service_name(query)
            if app_norm and app_norm in normalized_query:
                return True
            for service in canonical_normalized:
                if service and service in normalized_query:
                    return True
    return False


def _should_exclude_dashboard(dashboard: Dashboard, application_name: str, canonical_normalized: set[str]) -> bool:
    title = (dashboard.title or "").lower()
    is_filtered = any(hint in title for hint in FILTERED_DASHBOARD_HINTS)
    if not is_filtered:
        return False
    return not _dashboard_has_context(dashboard, application_name, canonical_normalized)


def _service_linked(service_variants: set[str], dashboard: Dashboard, panel: DashboardPanel, query: str) -> bool:
    corpus = " ".join([dashboard.title or "", panel.title or "", query or ""]).lower()
    norm = _normalize_service_name(corpus)
    for variant in service_variants:
        if variant and variant in norm:
            return True
    query_candidates = _extract_candidates_from_text(query)
    if query_candidates.intersection(service_variants):
        return True
    panel_candidates = _extract_candidates_from_text(panel.title or "")
    if panel_candidates.intersection(service_variants):
        return True
    return False


def _appendix_analysis(dashboard: Dashboard) -> RedDashboardAppendix:
    has_rate = False
    has_errors = False
    has_duration = False
    for panel in dashboard.panels:
        texts = [panel.title or "", *(panel.queries or [])]
        merged = " ".join(texts)
        matches = _signal_matches(merged)
        has_rate = has_rate or bool(matches["rate"])
        has_errors = has_errors or bool(matches["errors"])
        has_duration = has_duration or bool(matches["duration"])
    red_score, status = _score_status(has_rate, has_errors, has_duration)
    return RedDashboardAppendix(
        source_tool=dashboard.source_tool,
        dashboard_uid=dashboard.uid,
        dashboard_title=dashboard.title,
        panel_count=len(dashboard.panels),
        rate_present=has_rate,
        errors_present=has_errors,
        duration_present=has_duration,
        red_score=red_score,
        status=status,
    )


def analyze_red_panel_intelligence(
    estate: ObservabilityEstate,
    application_name: str,
    environment: str,
    canonical_services: list[str],
    auto_discover_services: bool,
) -> RedPanelIntelligenceResult:
    logger.info("Starting RED Panel Intelligence analysis for %d dashboards", len(estate.dashboards))

    canonical_map: dict[str, str] = {}
    for raw in canonical_services:
        normalized = _normalize_service_name(raw)
        if normalized:
            canonical_map[normalized] = raw.strip()

    discovered_counter: Counter[str] = Counter()
    for dashboard in estate.dashboards:
        for panel in dashboard.panels:
            discovered_counter.update(_extract_candidates_from_text(panel.title or ""))
            for query in panel.queries:
                discovered_counter.update(_extract_candidates_from_text(query or ""))

    discovered_services = [name for name, count in discovered_counter.most_common() if count >= 1 and name not in canonical_map]

    fallback_to_auto_discovery = False
    if not canonical_map:
        fallback_to_auto_discovery = True
        for discovered in discovered_services[:20]:
            canonical_map[discovered] = discovered

    if auto_discover_services:
        for discovered in discovered_services[:20]:
            canonical_map.setdefault(discovered, discovered)

    canonical_normalized = set(canonical_map.keys())

    service_bucket: dict[str, dict[str, list[RedEvidence] | set[str]]] = {}
    for normalized_service in canonical_normalized:
        service_bucket[normalized_service] = {
            "rate": [],
            "errors": [],
            "duration": [],
            "tools": set(),
            "dashboards": set(),
        }

    dashboard_appendix: list[RedDashboardAppendix] = []
    per_tool_scores: dict[str, list[int]] = defaultdict(list)
    recommendation_counter: Counter[str] = Counter()

    if not estate.dashboards:
        logger.warning("No dashboards found in ObservabilityEstate")
        return RedPanelIntelligenceResult(
            application_name=application_name,
            environment=environment,
            canonical_services=list(canonical_services),
            auto_discovered_services=discovered_services,
            auto_discover_services_enabled=auto_discover_services,
            fallback_to_auto_discovery=fallback_to_auto_discovery,
            services_assessed=len(canonical_map),
            fully_covered_services=0,
            partial_services=0,
            blind_services=len(canonical_map),
            overall_red_coverage_score=0.0,
            service_coverage=[],
            critical_blind_spots=list(canonical_map.values()),
            dashboard_appendix=[],
            dashboard_coverage_by_tool={},
            top_recommendations=[],
            extraction_errors=list(getattr(estate.summary, "extraction_errors", [])),
            no_dashboards_found=True,
            no_panels_found=False,
            guidance=["No dashboards discovered for selected tools."],
        )

    dashboards_without_panels = 0

    for dashboard in estate.dashboards:
        if _should_exclude_dashboard(dashboard, application_name, canonical_normalized):
            continue

        if not dashboard.panels:
            dashboards_without_panels += 1
            continue

        appendix = _appendix_analysis(dashboard)
        dashboard_appendix.append(appendix)
        per_tool_scores[dashboard.source_tool].append(appendix.red_score)

        for panel in dashboard.panels:
            for query in panel.queries:
                signal_from_panel_title = _signal_matches(panel.title or "")
                signal_from_query = _signal_matches(query or "")
                signal_hits = {
                    "rate": signal_from_panel_title["rate"] + signal_from_query["rate"],
                    "errors": signal_from_panel_title["errors"] + signal_from_query["errors"],
                    "duration": signal_from_panel_title["duration"] + signal_from_query["duration"],
                }
                if not any(signal_hits.values()):
                    continue

                for normalized_service in canonical_normalized:
                    variants = _service_variants(normalized_service)
                    if not _service_linked(variants, dashboard, panel, query):
                        continue

                    bucket = service_bucket[normalized_service]
                    bucket["tools"].add(dashboard.source_tool)
                    bucket["dashboards"].add(f"{dashboard.source_tool}:{dashboard.title}")

                    for category in ("rate", "errors", "duration"):
                        for keyword in signal_hits[category]:
                            source = "panel_query" if keyword in signal_from_query[category] else "panel_title"
                            evidence = RedEvidence(
                                category=category,
                                source_tool=dashboard.source_tool,
                                service=canonical_map[normalized_service],
                                dashboard_uid=dashboard.uid,
                                dashboard_title=dashboard.title,
                                panel_title=panel.title or "",
                                query=query or "",
                                source=source,
                                matched_keyword=keyword,
                            )
                            cast_list = bucket[category]
                            cast_list.append(evidence)

    service_coverage: list[RedServiceCoverage] = []
    for normalized_service, bucket in service_bucket.items():
        rate_evidence = bucket["rate"]
        errors_evidence = bucket["errors"]
        duration_evidence = bucket["duration"]
        rate_present = bool(rate_evidence)
        errors_present = bool(errors_evidence)
        duration_present = bool(duration_evidence)
        red_score, status = _score_status(rate_present, errors_present, duration_present)
        recs = _recommendations_for_service(rate_present, errors_present, duration_present)
        for rec in recs:
            recommendation_counter[rec] += 1
        service_coverage.append(
            RedServiceCoverage(
                service=canonical_map[normalized_service],
                normalized_service=normalized_service,
                auto_discovered=normalized_service in discovered_services,
                rate=RedSignalCoverage(found=rate_present, evidence=rate_evidence),
                errors=RedSignalCoverage(found=errors_present, evidence=errors_evidence),
                duration=RedSignalCoverage(found=duration_present, evidence=duration_evidence),
                red_score=red_score,
                status=status,
                tools=sorted(bucket["tools"]),
                dashboards=sorted(bucket["dashboards"]),
                recommendations=recs,
            )
        )

    service_coverage.sort(key=lambda item: (item.red_score, item.service.lower()))
    services_assessed = len(service_coverage)
    fully_covered = sum(1 for item in service_coverage if item.status == "complete")
    partial = sum(1 for item in service_coverage if item.status == "partial")
    blind = sum(1 for item in service_coverage if item.status == "blind")
    overall_red_score = (
        round(sum(item.red_score for item in service_coverage) / services_assessed, 2) if services_assessed else 0.0
    )

    dashboard_coverage_by_tool: dict[str, dict[str, float | int]] = {}
    for tool, scores in per_tool_scores.items():
        dashboard_coverage_by_tool[tool] = {
            "dashboard_count": len(scores),
            "avg_red_score": round(sum(scores) / len(scores), 2) if scores else 0.0,
            "complete_dashboards": sum(1 for score in scores if score == 100),
            "partial_dashboards": sum(1 for score in scores if score == 67),
            "weak_dashboards": sum(1 for score in scores if score == 33),
            "blind_dashboards": sum(1 for score in scores if score == 0),
        }

    top_recommendations = [
        f"{recommendation} ({count} service{'s' if count != 1 else ''})"
        for recommendation, count in recommendation_counter.most_common(10)
    ]

    critical_blind_spots = [item.service for item in service_coverage if item.status == "blind"]

    guidance: list[str] = []
    if fallback_to_auto_discovery:
        guidance.append("No canonical services supplied. Coverage derived from auto-discovered service names; review required.")
    if auto_discover_services:
        guidance.append("Auto-discovered services included in assessment scope.")
    if not auto_discover_services and discovered_services:
        guidance.append("Potential additional services detected but excluded because auto-discovery is disabled.")

    logger.info(
        "RED Panel Intelligence complete. services=%d complete=%d partial=%d blind=%d score=%.2f",
        services_assessed,
        fully_covered,
        partial,
        blind,
        overall_red_score,
    )

    return RedPanelIntelligenceResult(
        application_name=application_name,
        environment=environment,
        canonical_services=list(canonical_services),
        auto_discovered_services=discovered_services,
        auto_discover_services_enabled=auto_discover_services,
        fallback_to_auto_discovery=fallback_to_auto_discovery,
        services_assessed=services_assessed,
        fully_covered_services=fully_covered,
        partial_services=partial,
        blind_services=blind,
        overall_red_coverage_score=overall_red_score,
        service_coverage=service_coverage,
        critical_blind_spots=critical_blind_spots,
        dashboard_appendix=dashboard_appendix,
        dashboard_coverage_by_tool=dashboard_coverage_by_tool,
        top_recommendations=top_recommendations,
        extraction_errors=list(getattr(estate.summary, "extraction_errors", [])),
        no_dashboards_found=False,
        no_panels_found=bool(estate.dashboards) and dashboards_without_panels == len(estate.dashboards),
        guidance=guidance,
    )
