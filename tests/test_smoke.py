"""Smoke tests for the rules engine and scoring."""
from datetime import datetime, timezone

from observascore.adapters.prometheus import classify_alert
from observascore.engine import ScoringEngine
from observascore.model import (
    AlertClassification,
    AlertRule,
    Dashboard,
    DashboardPanel,
    Datasource,
    ObservabilityEstate,
    Service,
    Signal,
    SignalType,
)
from observascore.rules import RulesEngine


def make_empty_estate() -> ObservabilityEstate:
    return ObservabilityEstate(
        client_name="Test",
        environment="test",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def test_classify_alert_burn_rate():
    assert classify_alert("PaymentsSLOBurnRate", "slo:burn_rate > 10") == AlertClassification.BURN_RATE


def test_classify_alert_symptom():
    assert classify_alert("HighLatency", "http_request_duration_seconds > 1") == AlertClassification.SYMPTOM


def test_classify_alert_cause():
    assert classify_alert("HighCPU", "node_cpu_usage > 0.9") == AlertClassification.CAUSE


def test_rules_engine_loads_core_pack():
    engine = RulesEngine()
    assert len(engine.rules) > 20
    assert "SIG-001" in engine.rules
    assert "ALERT-001" in engine.rules


def test_empty_estate_has_findings():
    """An empty estate should generate multiple findings across dimensions."""
    estate = make_empty_estate()
    engine = RulesEngine()
    findings = engine.evaluate(estate)
    scorer = ScoringEngine()
    result = scorer.score(findings)
    # Empty estate should fire the signal coverage rules at least
    assert len(findings) >= 5
    # Signal coverage should be at the bottom (no metrics/logs/traces)
    sig_cov = next(d for d in result.dimension_scores if d.dimension == "signal_coverage")
    assert sig_cov.level <= 2


def test_well_formed_estate_scores_higher():
    """A well-formed estate should score noticeably better than empty."""
    estate = make_empty_estate()

    # Add some signals covering golden signals
    estate.signals = [
        Signal(source_tool="prometheus", identifier="http_request_duration_seconds", signal_type=SignalType.METRIC, semantic_type="latency"),
        Signal(source_tool="prometheus", identifier="http_errors_total", signal_type=SignalType.METRIC, semantic_type="error"),
        Signal(source_tool="prometheus", identifier="http_requests_total", signal_type=SignalType.METRIC, semantic_type="traffic"),
        Signal(source_tool="prometheus", identifier="node_cpu_usage", signal_type=SignalType.METRIC, semantic_type="saturation"),
        Signal(source_tool="loki", identifier="service", signal_type=SignalType.LOG),
        Signal(source_tool="jaeger", identifier="payments-api", signal_type=SignalType.TRACE),
    ]

    estate.alert_rules = [
        AlertRule(
            source_tool="prometheus",
            name="PaymentsSLOBurnRateFast",
            expression="slo:burn_rate:1h > 14.4",
            severity="critical",
            classification=AlertClassification.BURN_RATE,
            for_duration="2m",
            annotations={"description": "Fast burn", "summary": "Burn rate too high"},
            runbook_url="https://runbooks/burn-rate",
            group="slo",
        ),
    ]

    estate.dashboards = [
        Dashboard(
            source_tool="grafana",
            uid="dash1",
            title="Payments Overview",
            folder="Payments",
            tags=["payments", "tier-1"],
            panels=[DashboardPanel(title="Latency", panel_type="timeseries", unit="s", has_thresholds=True)],
            variables=["env", "service"],
            has_templating=True,
        )
    ]

    estate.services = [Service(name="payments-api", source_tool="jaeger")]
    estate.datasources = [Datasource(source_tool="grafana", name="Prom", ds_type="prometheus")]

    engine = RulesEngine()
    empty_result = ScoringEngine().score(engine.evaluate(make_empty_estate()))
    good_result = ScoringEngine().score(engine.evaluate(estate))

    assert good_result.overall_score > empty_result.overall_score
