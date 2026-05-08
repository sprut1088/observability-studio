from __future__ import annotations

import pytest
from pathlib import Path
from observascore.model import ObservabilityEstate, Service, Alert, Dashboard, Signal, Trace, Log, Metric
from observascore.insights.incident_simulator import (
    IncidentRequestContext,
    simulate_incident,
    IncidentSimulationResult,
)


@pytest.fixture
def minimal_estate():
    """Minimal estate with core objects for testing."""
    estate = ObservabilityEstate()
    service = Service(
        name="payment-service",
        owner="payments-team",
        environment="prod",
        dashboards=["payment-dashboard"],
        alert_names=["PaymentLatencyHigh"],
    )
    estate.services["payment-service"] = service
    return estate


def test_high_latency_with_all_signals(minimal_estate):
    """High latency incident with detection + visibility + diagnosis signals."""
    estate = minimal_estate
    estate.alert_rules = [
        Alert(name="PaymentLatencyHigh", service="payment-service", labels={"severity": "critical"})
    ]
    estate.dashboards = [
        Dashboard(name="payment-dashboard", panels=["Payment Latency p99", "Request Volume"])
    ]
    estate.signals = [
        Trace(service="payment-service", name="payment-trace"),
        Log(service="payment-service", name="payment-logs"),
        Metric(service="payment-service", name="payment-latency-ms"),
    ]

    context = IncidentRequestContext(
        application_name="PaymentApp",
        environment="prod",
        service_name="payment-service",
        incident_type="high_latency",
        canonical_services=["payment-service"],
    )

    result = simulate_incident(estate, context)

    assert result.overall_readiness_score >= 70, "Should be 'Ready' or above with all signals"
    assert result.readiness_status in ["Ready", "Mostly Ready"]
    assert result.detection_score >= 60
    assert result.visibility_score >= 60
    assert result.diagnosis_score >= 60


def test_error_spike_missing_alerts(minimal_estate):
    """Error spike with no detection alerts should score low on detection."""
    estate = minimal_estate
    estate.dashboards = [
        Dashboard(name="payment-dashboard", panels=["Error Rate"])
    ]
    estate.signals = [
        Metric(service="payment-service", name="error-rate"),
    ]

    context = IncidentRequestContext(
        application_name="PaymentApp",
        environment="prod",
        service_name="payment-service",
        incident_type="error_spike",
        canonical_services=["payment-service"],
    )

    result = simulate_incident(estate, context)

    assert result.detection_score < 50, "Should fail detection without error alerts"


def test_service_down_with_dashboards_no_logs(minimal_estate):
    """Service down with visibility but no logs should score visibility >= detection."""
    estate = minimal_estate
    estate.dashboards = [
        Dashboard(name="payment-dashboard", panels=["Service Availability"])
    ]
    estate.signals = [
        Metric(service="payment-service", name="uptime"),
    ]

    context = IncidentRequestContext(
        application_name="PaymentApp",
        environment="prod",
        service_name="payment-service",
        incident_type="service_down",
        canonical_services=["payment-service"],
    )

    result = simulate_incident(estate, context)

    assert result.visibility_score > 0, "Should have visibility from dashboards"
    assert result.diagnosis_score < result.visibility_score, "Diagnosis should be lower without logs/traces"


def test_service_normalization_matching(minimal_estate):
    """Service normalization should match payment-service / payment_service / paymentservice."""
    estate = minimal_estate
    # Only have "payment-service" in estate
    estate.alert_rules = [
        Alert(name="PaymentLatencyHigh", service="payment-service")
    ]

    for service_variant in ["payment-service", "payment_service", "paymentservice"]:
        context = IncidentRequestContext(
            application_name="PaymentApp",
            environment="prod",
            service_name=service_variant,
            incident_type="high_latency",
            canonical_services=["payment-service"],
        )

        result = simulate_incident(estate, context)
        assert result.detection_score > 0, f"Should match {service_variant} variant"


def test_all_incident_types(minimal_estate):
    """All 6 incident types should be processable."""
    estate = minimal_estate
    estate.alert_rules = [
        Alert(name="LatencyHigh", service="payment-service"),
        Alert(name="ErrorRateHigh", service="payment-service"),
    ]
    estate.dashboards = [
        Dashboard(name="payment-dashboard", panels=["Latency", "Errors"])
    ]

    incident_types = [
        "high_latency", "error_spike", "traffic_drop",
        "traffic_surge", "service_down", "dependency_failure"
    ]

    for inc_type in incident_types:
        context = IncidentRequestContext(
            application_name="PaymentApp",
            environment="prod",
            service_name="payment-service",
            incident_type=inc_type,
            canonical_services=["payment-service"],
        )

        result = simulate_incident(estate, context)
        assert isinstance(result, IncidentSimulationResult)
        assert 0 <= result.overall_readiness_score <= 100


def test_readiness_status_bands():
    """Verify readiness status bands are correctly assigned."""
    estate = ObservabilityEstate()
    context = IncidentRequestContext(
        application_name="App",
        environment="prod",
        service_name="test-service",
        incident_type="high_latency",
        canonical_services=["test-service"],
    )

    # Empty estate should produce low scores
    result = simulate_incident(estate, context)

    if result.overall_readiness_score < 20:
        assert result.readiness_status == "Not Ready"
    elif result.overall_readiness_score < 45:
        assert result.readiness_status == "High MTTR Risk"
    elif result.overall_readiness_score < 70:
        assert result.readiness_status == "At Risk"
    elif result.overall_readiness_score < 85:
        assert result.readiness_status == "Mostly Ready"
    else:
        assert result.readiness_status == "Ready"


def test_result_serialization(minimal_estate):
    """Result should serialize to dict without errors."""
    estate = minimal_estate
    context = IncidentRequestContext(
        application_name="PaymentApp",
        environment="prod",
        service_name="payment-service",
        incident_type="high_latency",
        canonical_services=["payment-service"],
    )

    result = simulate_incident(estate, context)
    result_dict = result.to_dict()

    assert isinstance(result_dict, dict)
    assert "overall_readiness_score" in result_dict
    assert "checks" in result_dict
    assert "evidence" in result_dict
    assert len(result_dict["checks"]) == 4  # detection, visibility, diagnosis, response


def test_response_readiness_checks(minimal_estate):
    """Response readiness should check for runbooks and owner labels."""
    estate = minimal_estate
    context = IncidentRequestContext(
        application_name="PaymentApp",
        environment="prod",
        service_name="payment-service",
        incident_type="high_latency",
        canonical_services=["payment-service"],
    )

    result = simulate_incident(estate, context)

    response_check = next((c for c in result.to_dict()["checks"] if c["name"] == "Response Readiness"), None)
    assert response_check is not None, "Response check should exist"
    assert "owner" in response_check["explanation"].lower() or "runbook" in response_check["explanation"].lower()
