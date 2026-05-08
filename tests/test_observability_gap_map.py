from __future__ import annotations

from datetime import datetime, timezone

from observascore.insights.observability_gap_map import analyze_observability_gap_map
from observascore.model import (
    AlertClassification,
    AlertRule,
    Dashboard,
    DashboardPanel,
    ExtractionSummary,
    ObservabilityEstate,
    Signal,
    SignalType,
)


def _estate(signals: list[Signal], dashboards: list[Dashboard], alerts: list[AlertRule]) -> ObservabilityEstate:
    return ObservabilityEstate(
        client_name="Gap Test",
        environment="test",
        timestamp=datetime.now(timezone.utc).isoformat(),
        signals=signals,
        dashboards=dashboards,
        alert_rules=alerts,
        summary=ExtractionSummary(),
    )


def test_service_with_full_coverage_is_excellent() -> None:
    signals = [
        Signal(source_tool="prometheus", identifier="http_requests_total", signal_type=SignalType.METRIC, labels={"service": "checkout"}),
        Signal(source_tool="loki", identifier="logs", signal_type=SignalType.LOG, labels={"service": "checkout"}),
        Signal(source_tool="jaeger", identifier="trace", signal_type=SignalType.TRACE, labels={"service": "checkout"}),
    ]
    dashboards = [
        Dashboard(
            source_tool="grafana",
            uid="g1",
            title="Checkout Overview",
            panels=[
                DashboardPanel(title="Request Rate", panel_type="timeseries", queries=["sum(rate(http_requests_total{service='checkout'}[5m]))"]),
                DashboardPanel(title="Error Rate", panel_type="timeseries", queries=["sum(rate(http_requests_total{service='checkout',status=~'5..'}[5m]))"]),
                DashboardPanel(title="Latency p95", panel_type="timeseries", queries=["histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{service='checkout'}[5m]))"]),
            ],
        )
    ]
    alerts = [
        AlertRule(
            source_tool="prometheus",
            name="CheckoutHighLatency",
            expression="service='checkout' and latency > 1",
            classification=AlertClassification.UNKNOWN,
            labels={"service": "checkout"},
        )
    ]

    result = analyze_observability_gap_map(_estate(signals, dashboards, alerts))

    assert result.total_services >= 1
    checkout = next(profile for profile in result.service_profiles if profile.service == "checkout")
    assert checkout.coverage_score == 100
    assert checkout.readiness_status == "Excellent"


def test_service_with_logs_only_is_poor() -> None:
    signals = [
        Signal(source_tool="splunk", identifier="app_logs", signal_type=SignalType.LOG, labels={"service": "billing"}),
    ]

    result = analyze_observability_gap_map(_estate(signals, [], []))

    billing = next(profile for profile in result.service_profiles if profile.service == "billing")
    assert billing.coverage.logs_present is True
    assert billing.coverage_score == 20
    assert billing.readiness_status == "Poor"


def test_unknown_service_grouping() -> None:
    signals = [
        Signal(source_tool="loki", identifier="raw_log_line", signal_type=SignalType.LOG, labels={}),
    ]

    result = analyze_observability_gap_map(_estate(signals, [], []))

    assert any(profile.service == "unknown" for profile in result.service_profiles)


def test_recommendations_generated_for_missing_signals() -> None:
    signals = [
        Signal(source_tool="prometheus", identifier="reqs", signal_type=SignalType.METRIC, labels={"service": "payments"}),
    ]

    result = analyze_observability_gap_map(_estate(signals, [], []))

    recs = [r for r in result.top_recommendations if r.service == "payments"]
    missing = {r.missing_signal for r in recs}
    assert "logs" in missing
    assert "traces" in missing
    assert "dashboards" in missing
    assert "alerts" in missing
    assert "red" in missing


def test_multiple_tools_aggregate_correctly() -> None:
    signals = [
        Signal(source_tool="prometheus", identifier="m", signal_type=SignalType.METRIC, labels={"service": "catalog"}),
        Signal(source_tool="splunk", identifier="l", signal_type=SignalType.LOG, labels={"service": "catalog"}),
        Signal(source_tool="tempo", identifier="t", signal_type=SignalType.TRACE, labels={"service": "catalog"}),
    ]
    dashboards = [
        Dashboard(
            source_tool="grafana",
            uid="g2",
            title="Catalog Dashboard",
            panels=[DashboardPanel(title="Request Rate", panel_type="timeseries", queries=["service='catalog'"])],
        )
    ]
    alerts = [
        AlertRule(
            source_tool="prometheus",
            name="CatalogAlert",
            expression="service='catalog'",
            classification=AlertClassification.UNKNOWN,
            labels={"service": "catalog"},
        )
    ]

    result = analyze_observability_gap_map(_estate(signals, dashboards, alerts))

    tools = {summary.tool_name for summary in result.tool_coverage_summary}
    assert "prometheus" in tools
    assert "splunk" in tools
    assert "tempo" in tools
    assert "grafana" in tools
