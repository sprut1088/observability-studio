from __future__ import annotations

from datetime import datetime, timezone

from observascore.insights.red_panel_intelligence import analyze_red_panel_intelligence
from observascore.model import Dashboard, DashboardPanel, ExtractionSummary, ObservabilityEstate


def _estate_with_dashboards(dashboards: list[Dashboard]) -> ObservabilityEstate:
    return ObservabilityEstate(
        client_name="Test Client",
        environment="test",
        timestamp=datetime.now(timezone.utc).isoformat(),
        dashboards=dashboards,
        summary=ExtractionSummary(),
    )


def test_dashboard_with_full_red_coverage_scores_100() -> None:
    dashboard = Dashboard(
        source_tool="grafana",
        uid="grafana-1",
        title="Checkout RED Overview",
        panels=[
            DashboardPanel(title="Request Rate", panel_type="timeseries", queries=["sum(rate(http_requests_total[5m]))"]),
            DashboardPanel(title="Error Rate", panel_type="timeseries", queries=["sum(rate(http_requests_total{status=~'5..'}[5m]))"]),
            DashboardPanel(
                title="Latency p95",
                panel_type="timeseries",
                queries=["histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))"],
            ),
        ],
    )

    result = analyze_red_panel_intelligence(_estate_with_dashboards([dashboard]))

    assert result.total_dashboards == 1
    assert result.dashboard_analyses[0].red_score == 100
    assert result.dashboard_analyses[0].status == "complete"


def test_dashboard_with_rate_only_scores_33() -> None:
    dashboard = Dashboard(
        source_tool="splunk",
        uid="splunk-1",
        title="Traffic Monitor",
        panels=[
            DashboardPanel(
                title="Request Volume",
                panel_type="singlevalue",
                queries=["index=prod | stats count by service"],
            )
        ],
    )

    result = analyze_red_panel_intelligence(_estate_with_dashboards([dashboard]))

    analysis = result.dashboard_analyses[0]
    assert analysis.rate_present is True
    assert analysis.errors_present is False
    assert analysis.duration_present is False
    assert analysis.red_score == 33
    assert analysis.status == "weak"


def test_dashboard_with_no_red_signals_scores_0() -> None:
    dashboard = Dashboard(
        source_tool="datadog",
        uid="datadog-1",
        title="Infrastructure Health",
        panels=[
            DashboardPanel(
                title="CPU Utilization",
                panel_type="timeseries",
                queries=["avg:system.cpu.user{*}"],
            )
        ],
    )

    result = analyze_red_panel_intelligence(_estate_with_dashboards([dashboard]))

    analysis = result.dashboard_analyses[0]
    assert analysis.red_score == 0
    assert analysis.status == "non_operational"


def test_multi_tool_dashboards_aggregate_correctly() -> None:
    dashboards = [
        Dashboard(
            source_tool="grafana",
            uid="grafana-red",
            title="Payments RED",
            panels=[
                DashboardPanel(title="Rate", panel_type="timeseries", queries=["sum(rate(http_requests_total[5m]))"]),
                DashboardPanel(title="Errors", panel_type="timeseries", queries=["sum(rate(http_requests_total{status=~'5..'}[5m]))"]),
                DashboardPanel(title="Duration", panel_type="timeseries", queries=["histogram_quantile(0.99, rate(http_request_duration_seconds_bucket[5m]))"]),
            ],
        ),
        Dashboard(
            source_tool="dynatrace",
            uid="dynatrace-rate",
            title="Request Throughput",
            panels=[DashboardPanel(title="Calls", panel_type="timeseries", queries=["builtin:service.requestCount.total"])]
        ),
    ]

    result = analyze_red_panel_intelligence(_estate_with_dashboards(dashboards))

    assert result.total_dashboards == 2
    assert result.complete_dashboards == 1
    assert result.weak_dashboards == 1
    assert result.partial_dashboards == 0
    assert result.non_operational_dashboards == 0
    assert result.overall_red_score == 66.5
    assert "grafana" in result.dashboard_coverage_by_tool
    assert "dynatrace" in result.dashboard_coverage_by_tool
