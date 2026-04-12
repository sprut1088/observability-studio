"""Offline demo: run an assessment with synthetic data, no live stack needed.

Usage:
    python examples/demo_offline.py

    # With AI analysis (requires ANTHROPIC_API_KEY env var or --api-key flag):
    python examples/demo_offline.py --ai
    python examples/demo_offline.py --ai --api-key sk-ant-...

Produces a report in ./reports/ showing what ObservaScore output looks like
before you point it at a real stack. The synthetic estate intentionally has
a mix of good practices and common anti-patterns to showcase all rule categories.
"""
from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from observascore.engine import ScoringEngine
from observascore.model import (
    AlertClassification,
    AlertReceiver,
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
        configured_tools=["prometheus", "grafana", "loki", "jaeger", "alertmanager"],
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
        ScrapeTarget(source_tool="prometheus", job="customer-onboarding",
                     instance="customer-onboarding:8080", health="up"),
    ]

    # --- Signals: golden signals present except error metrics ---
    estate.signals = [
        Signal("prometheus", "node_cpu_seconds_total", SignalType.METRIC, semantic_type="saturation"),
        Signal("prometheus", "node_memory_MemAvailable_bytes", SignalType.METRIC, semantic_type="saturation"),
        Signal("prometheus", "http_requests_total", SignalType.METRIC, semantic_type="traffic"),
        Signal("prometheus", "http_request_duration_seconds_bucket", SignalType.METRIC, semantic_type="latency"),
        Signal("prometheus", "jvm_heap_used_bytes", SignalType.METRIC, semantic_type="saturation"),
        Signal("prometheus", "db_connections_active", SignalType.METRIC, semantic_type="saturation"),
        # Intentionally no error metric — triggers GOLD-002
        Signal("loki", "job", SignalType.LOG, cardinality_estimate=8),
        Signal("loki", "level", SignalType.LOG, cardinality_estimate=5),
        Signal("loki", "namespace", SignalType.LOG, cardinality_estimate=3),
        Signal("jaeger", "payments-api", SignalType.TRACE, cardinality_estimate=14),
        Signal("jaeger", "customer-onboarding", SignalType.TRACE, cardinality_estimate=8),
    ]

    # --- Alerts: classic FI anti-patterns mixed with one good example ---
    estate.alert_rules = [
        AlertRule(
            source_tool="prometheus", name="HighCPUUsage",
            expression="node_cpu_seconds_total > 0.9",
            severity="warning",
            classification=AlertClassification.CAUSE,
            for_duration="0s",         # anti-pattern: no for clause
            labels={"severity": "warning"},
            annotations={},            # anti-pattern: no description, no runbook
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
            severity=None,             # anti-pattern: no severity
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
        # One well-structured SLO burn-rate alert
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

    # --- Recording rules ---
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
            tags=[],                   # anti-pattern: no tags
            panels=[
                DashboardPanel(title="CPU", panel_type="timeseries",
                               queries=["rate(node_cpu[5m])"], unit="percent"),
                DashboardPanel(title="Memory", panel_type="timeseries",
                               queries=["node_memory_MemAvailable_bytes"], unit=None),  # no unit
            ],
            variables=[], has_templating=False,
        ),
        Dashboard(
            source_tool="grafana", uid="d2", title="Payments API",
            folder="Payments",
            tags=["payments", "tier-1"],
            panels=[
                DashboardPanel(title="Latency p99", panel_type="timeseries",
                               queries=["histogram_quantile(0.99,...)"], unit="s", has_thresholds=True),
                DashboardPanel(title="Error Rate", panel_type="timeseries",
                               queries=["..."], unit="percent"),
                DashboardPanel(title="Throughput", panel_type="timeseries",
                               queries=["..."], unit="reqps"),
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
        Service(name="payments-api", source_tool="jaeger",
                operations=["POST /pay", "GET /status", "GET /history"]),
        Service(name="customer-onboarding", source_tool="jaeger",
                operations=["POST /customer", "PUT /kyc"]),
    ]

    # AlertManager with only Slack (no PagerDuty — triggers MODERN-004)
    estate.alert_receivers = [
        AlertReceiver(name="slack-alerts", receiver_types=["slack"]),
        AlertReceiver(name="email-critical", receiver_types=["email"]),
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
        alertmanager_receivers=len(estate.alert_receivers),
        alertmanager_integrations=["slack", "email"],
    )

    return estate


def main(run_ai: bool = False, api_key: str | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

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
        print(f"  {d.label:<34} L{d.level} ({d.score:.0f}/100) — {d.findings_count} findings")

    # Optional AI analysis
    if run_ai:
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            print("\nWARNING: --ai requested but no API key found.")
            print("Set ANTHROPIC_API_KEY env var or pass --api-key.")
        else:
            print("\nRunning AI analysis...")
            try:
                from observascore.ai import ObservabilityAIAnalyst
                analyst = ObservabilityAIAnalyst({"api_key": resolved_key, "model": "claude-sonnet-4-6"})
                estate.ai_analysis = analyst.analyze(estate, findings, result)
                if estate.ai_analysis.error:
                    print(f"AI analysis error: {estate.ai_analysis.error}")
                else:
                    print(f"Trend score: {estate.ai_analysis.trend_score:.0f}/100")
                    print(f"Technical gaps: {len(estate.ai_analysis.technical_gaps)}")
                    print(f"Functional gaps: {len(estate.ai_analysis.functional_gaps)}")
            except Exception as e:
                print(f"AI analysis failed: {e}")

    print("\nGenerating report...")
    out_dir = Path("./reports")
    gen = ReportGenerator()
    paths = gen.generate(estate, result, out_dir, {"report": {"title": "ObservaScore Demo Report"}})
    print(f"HTML: {paths['html']}")
    print(f"JSON: {paths['json']}")
    print("\nOpen the HTML report in your browser.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ObservaScore offline demo")
    parser.add_argument("--ai", action="store_true", help="Run AI analysis")
    parser.add_argument("--api-key", help="Anthropic API key (or set ANTHROPIC_API_KEY)")
    args = parser.parse_args()
    main(run_ai=args.ai, api_key=args.api_key)
