from __future__ import annotations

import logging
from collections import Counter, defaultdict

from observascore.insights.red_panel_intelligence.models import (
    RedDashboardAnalysis,
    RedPanelEvidence,
    RedPanelIntelligenceResult,
)
from observascore.model import Dashboard, DashboardPanel, ObservabilityEstate

logger = logging.getLogger(__name__)

RATE_KEYWORDS = [
    "rate",
    "request",
    "throughput",
    "traffic",
    "rps",
    "rpm",
    "count",
    "hits",
    "volume",
    "events",
    "calls",
    "transactions",
]

ERROR_KEYWORDS = [
    "error",
    "errors",
    "fail",
    "failed",
    "failure",
    "exception",
    "4xx",
    "5xx",
    "status",
    "http_status",
    "unsuccessful",
    "timeout",
]

DURATION_KEYWORDS = [
    "latency",
    "duration",
    "response_time",
    "response time",
    "elapsed",
    "p95",
    "p99",
    "percentile",
    "slow",
    "time_ms",
    "latency_ms",
]


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
    return 0, "non_operational"


def _recommendations_for_dashboard(rate_present: bool, errors_present: bool, duration_present: bool) -> list[str]:
    recommendations: list[str] = []
    if not rate_present:
        recommendations.append("Missing Rate: Add traffic/request volume panel")
    if not errors_present:
        recommendations.append("Missing Errors: Add error-rate or failure panel")
    if not duration_present:
        recommendations.append("Missing Duration: Add latency/p95/p99 panel")
    return recommendations


def _collect_text_evidence(
    category: str,
    dashboard: Dashboard,
    panel: DashboardPanel | None,
    query: str | None,
    source: str,
    keywords: list[str],
    text: str,
) -> list[RedPanelEvidence]:
    matches = _find_keywords(text, keywords)
    evidence: list[RedPanelEvidence] = []
    for keyword in matches:
        evidence.append(
            RedPanelEvidence(
                category=category,
                source_tool=dashboard.source_tool,
                dashboard_uid=dashboard.uid,
                dashboard_title=dashboard.title,
                panel_title=panel.title if panel else None,
                query=query,
                source=source,
                matched_keyword=keyword,
            )
        )
    return evidence


def analyze_red_panel_intelligence(estate: ObservabilityEstate) -> RedPanelIntelligenceResult:
    logger.info("Starting RED Panel Intelligence analysis for %d dashboards", len(estate.dashboards))

    dashboard_analyses: list[RedDashboardAnalysis] = []
    recommendation_counter: Counter[str] = Counter()
    per_tool_scores: dict[str, list[int]] = defaultdict(list)

    if not estate.dashboards:
        logger.warning("No dashboards found in ObservabilityEstate")
        return RedPanelIntelligenceResult(
            total_dashboards=0,
            complete_dashboards=0,
            partial_dashboards=0,
            weak_dashboards=0,
            non_operational_dashboards=0,
            overall_red_score=0.0,
            dashboard_analyses=[],
            dashboard_coverage_by_tool={},
            top_recommendations=[],
            extraction_errors=list(getattr(estate.summary, "extraction_errors", [])),
            no_dashboards_found=True,
            no_panels_found=False,
        )

    dashboards_without_panels = 0

    for dashboard in estate.dashboards:
        evidence: list[RedPanelEvidence] = []

        rate_present = False
        errors_present = False
        duration_present = False

        dashboard_title = dashboard.title or ""
        rate_title_ev = _collect_text_evidence(
            category="Rate",
            dashboard=dashboard,
            panel=None,
            query=None,
            source="dashboard_title",
            keywords=RATE_KEYWORDS,
            text=dashboard_title,
        )
        errors_title_ev = _collect_text_evidence(
            category="Errors",
            dashboard=dashboard,
            panel=None,
            query=None,
            source="dashboard_title",
            keywords=ERROR_KEYWORDS,
            text=dashboard_title,
        )
        duration_title_ev = _collect_text_evidence(
            category="Duration",
            dashboard=dashboard,
            panel=None,
            query=None,
            source="dashboard_title",
            keywords=DURATION_KEYWORDS,
            text=dashboard_title,
        )

        if rate_title_ev:
            rate_present = True
            evidence.extend(rate_title_ev)
        if errors_title_ev:
            errors_present = True
            evidence.extend(errors_title_ev)
        if duration_title_ev:
            duration_present = True
            evidence.extend(duration_title_ev)

        if not dashboard.panels:
            dashboards_without_panels += 1

        for panel in dashboard.panels:
            panel_title = panel.title or ""

            rate_panel_ev = _collect_text_evidence(
                category="Rate",
                dashboard=dashboard,
                panel=panel,
                query=None,
                source="panel_title",
                keywords=RATE_KEYWORDS,
                text=panel_title,
            )
            errors_panel_ev = _collect_text_evidence(
                category="Errors",
                dashboard=dashboard,
                panel=panel,
                query=None,
                source="panel_title",
                keywords=ERROR_KEYWORDS,
                text=panel_title,
            )
            duration_panel_ev = _collect_text_evidence(
                category="Duration",
                dashboard=dashboard,
                panel=panel,
                query=None,
                source="panel_title",
                keywords=DURATION_KEYWORDS,
                text=panel_title,
            )

            if rate_panel_ev:
                rate_present = True
                evidence.extend(rate_panel_ev)
            if errors_panel_ev:
                errors_present = True
                evidence.extend(errors_panel_ev)
            if duration_panel_ev:
                duration_present = True
                evidence.extend(duration_panel_ev)

            for query in panel.queries:
                query_text = query or ""

                rate_query_ev = _collect_text_evidence(
                    category="Rate",
                    dashboard=dashboard,
                    panel=panel,
                    query=query_text,
                    source="panel_query",
                    keywords=RATE_KEYWORDS,
                    text=query_text,
                )
                errors_query_ev = _collect_text_evidence(
                    category="Errors",
                    dashboard=dashboard,
                    panel=panel,
                    query=query_text,
                    source="panel_query",
                    keywords=ERROR_KEYWORDS,
                    text=query_text,
                )
                duration_query_ev = _collect_text_evidence(
                    category="Duration",
                    dashboard=dashboard,
                    panel=panel,
                    query=query_text,
                    source="panel_query",
                    keywords=DURATION_KEYWORDS,
                    text=query_text,
                )

                if rate_query_ev:
                    rate_present = True
                    evidence.extend(rate_query_ev)
                if errors_query_ev:
                    errors_present = True
                    evidence.extend(errors_query_ev)
                if duration_query_ev:
                    duration_present = True
                    evidence.extend(duration_query_ev)

        red_score, status = _score_status(rate_present, errors_present, duration_present)
        recs = _recommendations_for_dashboard(rate_present, errors_present, duration_present)

        for rec in recs:
            recommendation_counter[rec] += 1

        per_tool_scores[dashboard.source_tool].append(red_score)

        dashboard_analyses.append(
            RedDashboardAnalysis(
                source_tool=dashboard.source_tool,
                dashboard_uid=dashboard.uid,
                dashboard_title=dashboard.title,
                panel_count=len(dashboard.panels),
                rate_present=rate_present,
                errors_present=errors_present,
                duration_present=duration_present,
                red_score=red_score,
                status=status,
                recommendations=recs,
                evidence=evidence,
            )
        )

    total = len(dashboard_analyses)
    complete = sum(1 for a in dashboard_analyses if a.status == "complete")
    partial = sum(1 for a in dashboard_analyses if a.status == "partial")
    weak = sum(1 for a in dashboard_analyses if a.status == "weak")
    non_operational = sum(1 for a in dashboard_analyses if a.status == "non_operational")

    overall_red_score = round(sum(a.red_score for a in dashboard_analyses) / total, 2) if total else 0.0

    dashboard_coverage_by_tool: dict[str, dict[str, float | int]] = {}
    for tool, scores in per_tool_scores.items():
        dashboard_coverage_by_tool[tool] = {
            "dashboard_count": len(scores),
            "avg_red_score": round(sum(scores) / len(scores), 2) if scores else 0.0,
            "complete_dashboards": sum(1 for score in scores if score == 100),
            "partial_dashboards": sum(1 for score in scores if score == 67),
            "weak_dashboards": sum(1 for score in scores if score == 33),
            "non_operational_dashboards": sum(1 for score in scores if score == 0),
        }

    top_recommendations = [
        f"{recommendation} ({count} dashboard{'s' if count != 1 else ''})"
        for recommendation, count in recommendation_counter.most_common(10)
    ]

    logger.info(
        "RED Panel Intelligence complete. Total=%d complete=%d partial=%d weak=%d non_operational=%d score=%.2f",
        total,
        complete,
        partial,
        weak,
        non_operational,
        overall_red_score,
    )

    return RedPanelIntelligenceResult(
        total_dashboards=total,
        complete_dashboards=complete,
        partial_dashboards=partial,
        weak_dashboards=weak,
        non_operational_dashboards=non_operational,
        overall_red_score=overall_red_score,
        dashboard_analyses=dashboard_analyses,
        dashboard_coverage_by_tool=dashboard_coverage_by_tool,
        top_recommendations=top_recommendations,
        extraction_errors=list(getattr(estate.summary, "extraction_errors", [])),
        no_dashboards_found=False,
        no_panels_found=dashboards_without_panels == total,
    )
