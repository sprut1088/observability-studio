"""Offline demo: run an assessment with synthetic data, no VM needed.

Usage:
    python examples/demo_offline.py

Produces a report in ./reports/ showing what ObservaScore output looks like
before you point it at a real stack.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from observascore.engine import ScoringEngine
from observascore.model import (
    AlertClassification,
    AlertRule,
    Dashboard,
    DashboardPanel,
    Datasource,
    ExtractionSummary,
    ObservabilityEstate,
    RecordingRule,
    ScrapeTarget,
    Service,
    Signal,
    SignalType,
)
from observascore.report import ReportGenerator
from observascore.rules import RulesEngine


def build_demo_estate() -> ObservabilityEstate:
    estate = ObservabilityEstate(
        client_name="Demo Bank (Offline Sample)",
        environment="lab",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    # --- Scrape targets: mostly healthy with a couple of failures ---
    estate.scrape_targets = [
        ScrapeTarget(source_tool="prometheus", job="node", instance=f"node-{i}:9100",
                     health="up" if i < 4 else "down",
                     last_scrape_error=None if i < 4 else "connection refused")
        for i in range(5)
    ] + [
        ScrapeTarget(source_tool="prometheus", job="payments-api",
                     instance="payments-api:8080", health="up"),
        ScrapeTarget(source_tool="prometheus", job="cadvisor",
                     instance="cadvisor:8080", health="up"),
    ]

    # --- Signals: some golden signals present, some missing ---
    estate.signals = [
        Signal("prometheus", "node_cpu_seconds_total", SignalType.METRIC, semantic_type="saturation"),
        Signal("prometheus", "node_memory_MemAvailable_bytes", SignalType.METRIC, semantic_type="saturation"),
        Signal("prometheus", "http_requests_total", SignalType.METRIC, semantic_type="traffic"),
        Signal("prometheus", "http_request_duration_seconds_bucket", SignalType.METRIC, semantic_type="latency"),
        # Intentionally no error metric to trigger a finding
        Signal("loki", "job", SignalType.LOG, cardinality_estimate=8),
        Signal("loki", "level", SignalType.LOG, cardinality_estimate=5),
        Signal("jaeger", "payments-api", SignalType.TRACE, cardinality_estimate=14),
        Signal("jaeger", "customer-onboarding", SignalType.TRACE, cardinality_estimate=8),
    ]

    # --- Alerts: classic FI anti-patterns ---
    estate.alert_rules = [
        AlertRule(
            source_tool="prometheus", name="HighCPUUsage",
            expression="node_cpu_seconds_total > 0.9",
            severity="warning",
            classification=AlertClassification.CAUSE,
            for_duration="0s",  # anti-pattern: no for clause
            labels={"severity": "warning"},
            annotations={},  # anti-pattern: no description, no runbook
            group="node",
        ),
        AlertRule(
            source_tool="prometheus", name="HighMemoryUsage",
            expression="node_memory_MemAvailable_bytes < 1e9",
            severity="warning",
            classification=AlertClassification.CAUSE,
            for_duration="5m",
            labels={"severity": "warning"},
            annotations={"description": "Low memory"},
            group="node",
        ),
        AlertRule(
            source_tool="prometheus", name="DiskWillFillIn4h",
            expression="predict_linear(node_filesystem_free_bytes[1h], 4*3600) < 0",
            severity=None,  # anti-pattern: no severity
            classification=AlertClassification.CAUSE,
            for_duration="10m",
            labels={},
            annotations={"summary": "Disk will fill"},
            group="disk",
        ),
        AlertRule(
            source_tool="prometheus", name="InstanceDown",
            expression="up == 0",
            severity="critical",
            classification=AlertClassification.CAUSE,
            for_duration="2m",
            labels={"severity": "critical"},
            annotations={"summary": "Instance down", "description": "An instance is down"},
            group="ops",
        ),
        # One good alert with runbook, symptom-based, burn rate
        AlertRule(
            source_tool="prometheus", name="PaymentsHighErrorBudgetBurn",
            expression="slo:payments_availability:burn_rate:1h > 14.4",
            severity="critical",
            classification=AlertClassification.BURN_RATE,
            for_duration="2m",
            labels={"severity": "critical", "service": "payments"},
            annotations={
                "summary": "Payments error budget burning fast",
                "description": "Fast burn on payments availability SLO",
            },
            runbook_url="https://runbooks.bank.local/payments/slo-burn",
            group="slo",
        ),
    ]

    # --- Recording rules: minimal (triggers AUTO-001 concern if zero; we add one) ---
    estate.recording_rules = [
        RecordingRule(
            source_tool="prometheus",
            name="job:http_requests:rate5m",
            expression="sum by (job) (rate(http_requests_total[5m]))",
            group="http",
        ),
    ]

    # --- Dashboards: mostly in General, one well-structured ---
    estate.dashboards = [
        Dashboard(
            source_tool="grafana", uid="d1", title="Node Overview",
            folder="General",
            tags=[],  # no tags — triggers GOV-002
            panels=[
                DashboardPanel(title="CPU", panel_type="timeseries", queries=["rate(node_cpu[5m])"], unit="percent"),
                DashboardPanel(title="Mem", panel_type="timeseries", queries=[""], unit=None),  # no unit
            ],
            variables=[], has_templating=False,
        ),
        Dashboard(
            source_tool="grafana", uid="d2", title="Payments API",
            folder="Payments",
            tags=["payments", "tier-1"],
            panels=[
                DashboardPanel(title="Latency", panel_type="timeseries", queries=[""], unit="s", has_thresholds=True),
                DashboardPanel(title="Errors", panel_type="timeseries", queries=[""], unit="short"),
                DashboardPanel(title="Throughput", panel_type="timeseries", queries=[""], unit="reqps"),
            ],
            variables=["env", "service"], has_templating=True,
        ),
        Dashboard(
            source_tool="grafana", uid="d3", title="Old_Dashboard_v3_DONOTUSE",
            folder="General", tags=[],
            panels=[DashboardPanel(title="p1", panel_type="graph", queries=[""])],
            variables=[], has_templating=False,
        ),
    ]

    estate.datasources = [
        Datasource(source_tool="grafana", name="Prometheus", ds_type="prometheus",
                   url="http://prometheus:9090", is_default=True),
        Datasource(source_tool="grafana", name="Loki", ds_type="loki",
                   url="http://loki:3100"),
        Datasource(source_tool="grafana", name="Jaeger", ds_type="jaeger",
                   url="http://jaeger:16686"),
    ]

    estate.services = [
        Service(name="payments-api", source_tool="jaeger", operations=["POST /pay", "GET /status"]),
        Service(name="customer-onboarding", source_tool="jaeger", operations=["POST /customer"]),
    ]

    estate.summary = ExtractionSummary(
        prometheus_targets=len(estate.scrape_targets),
        prometheus_targets_up=sum(1 for t in estate.scrape_targets if t.health == "up"),
        prometheus_alert_rules=len(estate.alert_rules),
        prometheus_recording_rules=len(estate.recording_rules),
        prometheus_metrics_sampled=sum(1 for s in estate.signals if s.signal_type == SignalType.METRIC),
        grafana_dashboards=len(estate.dashboards),
        grafana_folders=2,
        grafana_datasources=len(estate.datasources),
        loki_labels=sum(1 for s in estate.signals if s.signal_type == SignalType.LOG),
        jaeger_services=len(estate.services),
    )

    return estate


def main():
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("Building demo estate...")
    estate = build_demo_estate()

    print("Evaluating rules...")
    engine = RulesEngine()
    findings = engine.evaluate(estate)

    print("Scoring...")
    result = ScoringEngine().score(findings)
    print(f"\nOverall: Level {result.overall_level} ({result.overall_level_name}) "
          f"— {result.overall_score:.1f}/100")
    for d in result.dimension_scores:
        print(f"  {d.label:<30} L{d.level} ({d.score:.0f}/100) — {d.findings_count} findings")

    print("\nGenerating report...")
    out_dir = Path("./reports")
    gen = ReportGenerator()
    paths = gen.generate(estate, result, out_dir, {"report": {"title": "ObservaScore Demo Report"}})
    print(f"HTML: {paths['html']}")
    print(f"JSON: {paths['json']}")
    print("\nOpen the HTML in a browser to see the full report.")


if __name__ == "__main__":
    main()
