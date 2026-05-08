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


def test_canonical_services_drive_coverage() -> None:
    dashboards = [
        Dashboard(
            source_tool="grafana",
            uid="g-1",
            title="Checkout Service RED",
            panels=[
                DashboardPanel(title="Request Rate", panel_type="timeseries", queries=["sum(rate(http_requests_total{service='checkout'}[5m]))"]),
                DashboardPanel(title="Error Rate", panel_type="timeseries", queries=["sum(rate(http_requests_total{service='checkout',status=~'5..'}[5m]))"]),
                DashboardPanel(title="Latency p95", panel_type="timeseries", queries=["histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{service='checkout'}[5m]))"]),
            ],
        ),
        Dashboard(
            source_tool="datadog",
            uid="dd-1",
            title="Payment API",
            panels=[
                DashboardPanel(title="Request throughput", panel_type="query_value", queries=["sum:trace.http.requests{service:payment-api}.as_rate()"]),
            ],
        ),
    ]

    result = analyze_red_panel_intelligence(
        _estate_with_dashboards(dashboards),
        application_name="commerce",
        environment="prod",
        canonical_services=["checkout", "payment-api"],
        auto_discover_services=False,
    )

    assert result.services_assessed == 2
    assert result.fully_covered_services == 1
    assert result.partial_services == 0
    assert result.blind_services == 0
    assert result.overall_red_coverage_score == 66.5
    assert result.service_coverage[0].service in {"checkout", "payment-api"}


def test_auto_discovery_fallback_when_no_canonical_services() -> None:
    dashboard = Dashboard(
        source_tool="grafana",
        uid="g-2",
        title="Payments RED",
        panels=[
            DashboardPanel(
                title="Requests",
                panel_type="timeseries",
                queries=["sum(rate(http_requests_total{service='payment'}[5m]))"],
            )
        ],
    )

    result = analyze_red_panel_intelligence(
        _estate_with_dashboards([dashboard]),
        application_name="payments",
        environment="prod",
        canonical_services=[],
        auto_discover_services=False,
    )

    assert result.fallback_to_auto_discovery is True
    assert result.services_assessed >= 1
    assert any("review required" in note.lower() for note in result.guidance)


def test_filtered_infra_dashboard_not_counted_without_service_context() -> None:
    dashboards = [
        Dashboard(
            source_tool="grafana",
            uid="infra-1",
            title="Infrastructure Overview",
            panels=[
                DashboardPanel(
                    title="Cluster request rate",
                    panel_type="timeseries",
                    queries=["sum(rate(http_requests_total[5m]))"],
                )
            ],
        )
    ]

    result = analyze_red_panel_intelligence(
        _estate_with_dashboards(dashboards),
        application_name="commerce",
        environment="prod",
        canonical_services=["checkout"],
        auto_discover_services=False,
    )

    assert result.dashboard_appendix == []
    assert result.service_coverage[0].red_score == 0


def test_service_evidence_contains_query_matches() -> None:
    dashboards = [
        Dashboard(
            source_tool="splunk",
            uid="sp-1",
            title="Checkout Splunk Studio",
            panels=[
                DashboardPanel(
                    title="Errors",
                    panel_type="singlevalue",
                    queries=["index=prod service=checkout status=500 | stats count"],
                )
            ],
        )
    ]

    result = analyze_red_panel_intelligence(
        _estate_with_dashboards(dashboards),
        application_name="checkout-suite",
        environment="prod",
        canonical_services=["checkout"],
        auto_discover_services=False,
    )

    svc = result.service_coverage[0]
    assert svc.errors.found is True
    assert len(svc.errors.evidence) > 0
    assert svc.errors.evidence[0].source_tool == "splunk"


def test_no_false_positive_for_unrelated_service() -> None:
    dashboards = [
        Dashboard(
            source_tool="grafana",
            uid="g-3",
            title="Search Service RED",
            panels=[
                DashboardPanel(
                    title="Request Rate",
                    panel_type="timeseries",
                    queries=["sum(rate(http_requests_total{service='search'}[5m]))"],
                )
            ],
        )
    ]

    result = analyze_red_panel_intelligence(
        _estate_with_dashboards(dashboards),
        application_name="commerce",
        environment="prod",
        canonical_services=["checkout"],
        auto_discover_services=False,
    )

    assert result.service_coverage[0].service == "checkout"
    assert result.service_coverage[0].red_score == 0
