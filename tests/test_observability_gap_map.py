from __future__ import annotations

from datetime import datetime, timezone

from observascore.insights.observability_gap_map import analyze_observability_gap_map
from observascore.insights.observability_gap_map.models import ApplicationContext
from observascore.model import (
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


def _app(services: list[str], include_auto_discovered: bool = False) -> ApplicationContext:
    return ApplicationContext(
        name="Astronomy Shop",
        environment="prod",
        services=services,
        include_auto_discovered=include_auto_discovered,
    )


def test_canonical_services_are_main_rows() -> None:
    signals = [
        Signal(source_tool="prometheus", identifier="http_requests_total", signal_type=SignalType.METRIC, labels={"service": "checkout"}),
        Signal(source_tool="loki", identifier="logs", signal_type=SignalType.LOG, labels={"service": "checkout"}),
        Signal(source_tool="loki", identifier="/opt/splunk/var/log/splunk/audit.log", signal_type=SignalType.LOG, labels={"source": "_audit"}),
    ]
    result = analyze_observability_gap_map(_estate(signals, [], []), application_context=_app(["checkout", "payment"]))

    services = [profile.service for profile in result.service_profiles]
    assert services == ["payment", "checkout"] or services == ["checkout", "payment"]
    assert "_audit" in result.ignored_candidates


def test_noisy_candidates_filtered_from_auto_discovery() -> None:
    signals = [
        Signal(source_tool="splunk", identifier="_internal", signal_type=SignalType.LOG, labels={"index": "_internal"}),
        Signal(source_tool="splunk", identifier="splunkd_access", signal_type=SignalType.LOG, labels={"source": "splunkd_access"}),
        Signal(source_tool="splunk", identifier="/opt/splunk/var/log/splunk/metrics.log", signal_type=SignalType.LOG, labels={"source": "/opt/splunk/var/log/splunk/metrics.log"}),
    ]
    result = analyze_observability_gap_map(_estate(signals, [], []), application_context=_app(["checkout"]))

    assert "_internal" not in result.auto_discovered_candidates
    assert "splunkd_access" not in result.auto_discovered_candidates
    assert any(item in result.ignored_candidates for item in ["_internal", "splunkd_access"])


def test_explicit_noisy_canonical_service_is_retained() -> None:
    signals = [
        Signal(source_tool="splunk", identifier="_internal", signal_type=SignalType.LOG, labels={"service": "_internal"}),
    ]
    result = analyze_observability_gap_map(_estate(signals, [], []), application_context=_app(["_internal"]))

    services = [profile.service for profile in result.service_profiles]
    assert "_internal" in services


def test_payment_matching_variants() -> None:
    signals = [
        Signal(source_tool="prometheus", identifier="payment-service", signal_type=SignalType.METRIC, labels={"svc": "payment"}),
        Signal(source_tool="tempo", identifier="oteldemo.paymentservice", signal_type=SignalType.TRACE, labels={"service.name": "payment"}),
    ]
    dashboards = [
        Dashboard(
            source_tool="grafana",
            uid="g1",
            title="payment-service overview",
            panels=[DashboardPanel(title="payment errors", panel_type="timeseries", queries=["service.name='payment'"])],
        )
    ]

    result = analyze_observability_gap_map(_estate(signals, dashboards, []), application_context=_app(["payment"]))

    payment = next(profile for profile in result.service_profiles if profile.service == "payment")
    assert payment.coverage.metrics_present is True
    assert payment.coverage.traces_present is True
    assert payment.coverage.dashboards_present is True


def test_no_canonical_services_falls_back_to_sanitized_discovery() -> None:
    signals = [
        Signal(source_tool="prometheus", identifier="oteldemo.paymentservice", signal_type=SignalType.METRIC, labels={"service.name": "payment"}),
        Signal(source_tool="splunk", identifier="_internal", signal_type=SignalType.LOG, labels={"index": "_internal"}),
    ]

    result = analyze_observability_gap_map(_estate(signals, [], []), application_context=_app([]))

    assert result.discovery_mode is True
    assert result.canonical_services == []
    assert "payment" in result.auto_discovered_candidates
    assert "payment" in [profile.service for profile in result.service_profiles]
    assert "_internal" not in [profile.service for profile in result.service_profiles]


def test_recommendations_target_report_scope_only() -> None:
    signals = [
        Signal(source_tool="prometheus", identifier="http_requests_total", signal_type=SignalType.METRIC, labels={"service": "checkout"}),
        Signal(source_tool="splunk", identifier="_internal", signal_type=SignalType.LOG, labels={"index": "_internal"}),
    ]
    result = analyze_observability_gap_map(_estate(signals, [], []), application_context=_app(["checkout"]))

    rec_services = {rec.service for rec in result.top_recommendations}
    assert "checkout" in rec_services
    assert "_internal" not in rec_services
