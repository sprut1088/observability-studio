"""ObservaScore CLI."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.logging import RichHandler

from observascore.adapters import (
    AlertManagerAdapter,
    AppDynamicsAdapter,
    DatadogAdapter,
    DynatraceAdapter,
    ElasticsearchAdapter,
    GrafanaAdapter,
    JaegerAdapter,
    LokiAdapter,
    OtelCollectorAdapter,
    PrometheusAdapter,
    TempoAdapter,
)
from observascore.engine import ScoringEngine
from observascore.model import ExtractionSummary, ObservabilityEstate
from observascore.export import ExcelExporter
from observascore.report import ReportGenerator
from observascore.rules import RulesEngine

console = Console()


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


def _run_adapter(name: str, adapter_cls, config: dict, estate: ObservabilityEstate,
                 summary: ExtractionSummary) -> None:
    """Generic adapter runner — health-checks then extracts, populates estate."""
    console.print(f"[cyan]→ {name.capitalize()}[/cyan]")
    adapter = adapter_cls(config)
    if not adapter.health_check():
        summary.extraction_errors.append(f"{name}: health check failed")
        console.print(f"[red]  {name} unreachable[/red]")
        return
    try:
        data = adapter.extract()
        _merge_adapter_data(name, data, estate, summary)
    except Exception as e:
        summary.extraction_errors.append(f"{name}: {e}")
        console.print(f"[red]  {name} extraction failed: {e}[/red]")


def _merge_adapter_data(name: str, data: dict, estate: ObservabilityEstate,
                        summary: ExtractionSummary) -> None:
    """Merge extracted data into the estate and update summary counters."""
    errors = data.get("errors", [])
    summary.extraction_errors.extend(f"{name}: {e}" for e in errors)

    if name == "prometheus":
        estate.alert_rules.extend(data.get("alert_rules", []))
        estate.recording_rules.extend(data.get("recording_rules", []))
        estate.scrape_targets.extend(data.get("scrape_targets", []))
        estate.signals.extend(data.get("signals", []))
        summary.prometheus_targets = len(data.get("scrape_targets", []))
        summary.prometheus_targets_up = sum(
            1 for t in data.get("scrape_targets", []) if t.health == "up"
        )
        summary.prometheus_alert_rules = len(data.get("alert_rules", []))
        summary.prometheus_recording_rules = len(data.get("recording_rules", []))
        summary.prometheus_metrics_sampled = len(data.get("signals", []))

    elif name == "grafana":
        estate.dashboards.extend(data.get("dashboards", []))
        estate.datasources.extend(data.get("datasources", []))
        estate.alert_rules.extend(data.get("alert_rules", []))
        summary.grafana_dashboards = len(data.get("dashboards", []))
        summary.grafana_folders = len(data.get("folders", []))
        summary.grafana_datasources = len(data.get("datasources", []))
        summary.grafana_alert_rules = len(data.get("alert_rules", []))

    elif name == "loki":
        estate.signals.extend(data.get("signals", []))
        summary.loki_labels = len(data.get("labels", []))

    elif name == "jaeger":
        estate.services.extend(data.get("services", []))
        estate.signals.extend(data.get("signals", []))
        summary.jaeger_services = len(data.get("services", []))
        summary.jaeger_operations = sum(len(s.operations) for s in data.get("services", []))

    elif name == "alertmanager":
        estate.alert_receivers.extend(data.get("receivers", []))
        summary.alertmanager_receivers = len(data.get("receivers", []))
        summary.alertmanager_silences = len(data.get("silences", []))
        summary.alertmanager_integrations = data.get("integrations", [])

    elif name == "tempo":
        estate.services.extend(data.get("services", []))
        estate.signals.extend(data.get("signals", []))
        summary.tempo_services = len(data.get("services", []))

    elif name == "elasticsearch":
        estate.signals.extend(data.get("signals", []))
        summary.elasticsearch_indices = len(data.get("indices", []))
        summary.elasticsearch_data_streams = len(data.get("data_streams", []))

    elif name == "otel_collector":
        summary.otel_receivers = data.get("receivers", [])
        summary.otel_exporters = data.get("exporters", [])
        summary.otel_pipelines = data.get("pipeline_count", 0)

    elif name == "appdynamics":
        estate.services.extend(data.get("services", []))
        estate.alert_rules.extend(data.get("alert_rules", []))
        estate.dashboards.extend(data.get("dashboards", []))
        estate.signals.extend(data.get("signals", []))
        summary.appdynamics_applications = len(data.get("applications", []))
        summary.appdynamics_tiers = len(data.get("services", []))
        summary.appdynamics_health_rules = len(data.get("alert_rules", []))
        summary.appdynamics_business_transactions = data.get("business_transactions_total", 0)
        summary.appdynamics_has_eum = data.get("has_eum", False)
        summary.appdynamics_has_sim = data.get("has_sim", False)
        summary.appdynamics_has_db_monitoring = data.get("has_db_monitoring", False)
        summary.appdynamics_apps_with_baselines = data.get("apps_with_baselines", 0)

    elif name == "datadog":
        estate.alert_rules.extend(data.get("alert_rules", []))
        estate.dashboards.extend(data.get("dashboards", []))
        estate.services.extend(data.get("services", []))
        estate.recording_rules.extend(data.get("recording_rules", []))
        estate.signals.extend(data.get("signals", []))
        summary.datadog_monitors = len(data.get("alert_rules", []))
        summary.datadog_monitors_with_notifications = data.get("monitors_with_notifications", 0)
        summary.datadog_dashboards = len(data.get("dashboards", []))
        summary.datadog_hosts = data.get("hosts_count", 0)
        summary.datadog_slos = len(data.get("recording_rules", []))
        summary.datadog_synthetics = data.get("synthetics_count", 0)
        summary.datadog_has_apm = data.get("has_apm", False)
        summary.datadog_has_log_management = data.get("has_log_management", False)
        summary.datadog_has_security_monitoring = data.get("has_security_monitoring", False)
        summary.datadog_has_service_catalog = data.get("has_service_catalog", False)

    elif name == "dynatrace":
        estate.services.extend(data.get("services", []))
        estate.alert_rules.extend(data.get("alert_rules", []))
        estate.dashboards.extend(data.get("dashboards", []))
        estate.recording_rules.extend(data.get("recording_rules", []))
        estate.signals.extend(data.get("signals", []))
        entity_counts = data.get("entity_counts", {})
        summary.dynatrace_services = entity_counts.get("SERVICE", 0)
        summary.dynatrace_hosts = entity_counts.get("HOST", 0)
        summary.dynatrace_applications = entity_counts.get("APPLICATION", 0)
        summary.dynatrace_problems_open = data.get("problems_open", 0)
        summary.dynatrace_slos = len(data.get("recording_rules", []))
        summary.dynatrace_synthetics = data.get("synthetics_count", 0)
        summary.dynatrace_alerting_profiles = data.get("alerting_profiles", 0)
        summary.dynatrace_notification_integrations = data.get("notification_integrations", 0)
        summary.dynatrace_has_log_management = data.get("has_log_management", False)
        summary.dynatrace_has_rum = data.get("has_rum", False)


@click.group()
@click.version_option("0.2.0", prog_name="observascore")
def cli():
    """ObservaScore — Observability & SRE Maturity Assessment with AI Analysis."""


@cli.command()
@click.option("--config", "-c", type=click.Path(exists=True), required=True,
              help="Path to config YAML file")
@click.option("--output", "-o", type=click.Path(), default="./reports",
              help="Output directory for reports")
@click.option("--ai/--no-ai", default=True,
              help="Enable/disable AI-powered gap analysis (requires api key in config)")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def assess(config: str, output: str, ai: bool, verbose: bool) -> None:
    """Run a full maturity assessment and produce an HTML + JSON report."""
    setup_logging(verbose)
    console.rule("[bold blue]ObservaScore v0.2.0 — Assessment Starting")

    with open(config) as f:
        cfg = yaml.safe_load(f)

    client_name = cfg.get("client", {}).get("name", "Unknown")
    environment = cfg.get("client", {}).get("environment", "unknown")

    estate = ObservabilityEstate(
        client_name=client_name,
        environment=environment,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    summary = ExtractionSummary()
    sources = cfg.get("sources", {})

    # Map of (source_key, adapter_class) in extraction order
    adapter_map = [
        # Open-source stack
        ("prometheus", PrometheusAdapter),
        ("grafana", GrafanaAdapter),
        ("loki", LokiAdapter),
        ("jaeger", JaegerAdapter),
        ("alertmanager", AlertManagerAdapter),
        ("tempo", TempoAdapter),
        ("elasticsearch", ElasticsearchAdapter),
        ("otel_collector", OtelCollectorAdapter),
        # Commercial APM / observability platforms
        ("appdynamics", AppDynamicsAdapter),
        ("datadog", DatadogAdapter),
        ("dynatrace", DynatraceAdapter),
    ]

    console.rule("[bold blue]Extracting from sources")
    for src_key, adapter_cls in adapter_map:
        src_cfg = sources.get(src_key, {})
        if not src_cfg.get("enabled"):
            console.print(f"[dim]  {src_key:<16} disabled[/dim]")
            continue
        estate.configured_tools.append(src_key)
        _run_adapter(src_key, adapter_cls, src_cfg, estate, summary)

    estate.summary = summary

    console.rule("[bold blue]Evaluating rules")
    engine = RulesEngine()
    findings = engine.evaluate(estate)

    console.rule("[bold blue]Scoring")
    scorer = ScoringEngine()
    result = scorer.score(findings)

    console.print(
        f"[bold]Overall maturity: Level {result.overall_level} "
        f"— {result.overall_level_name} "
        f"({result.overall_score:.1f}/100)[/bold]"
    )
    for d in result.dimension_scores:
        console.print(
            f"  {d.label:<34} L{d.level} ({d.score:.0f}/100) — {d.findings_count} findings"
        )

    # --- AI Analysis ---
    ai_cfg = cfg.get("ai", {})
    if ai and ai_cfg.get("enabled", True) and ai_cfg.get("api_key"):
        console.rule("[bold blue]AI Analysis (Claude)")
        try:
            from observascore.ai import ObservabilityAIAnalyst
            analyst = ObservabilityAIAnalyst(ai_cfg)
            estate.ai_analysis = analyst.analyze(estate, findings, result)
            if estate.ai_analysis.error:
                console.print(f"[yellow]  AI analysis warning: {estate.ai_analysis.error}[/yellow]")
            else:
                console.print(
                    f"[green]  AI analysis complete — "
                    f"trend score: {estate.ai_analysis.trend_score:.0f}/100[/green]"
                )
                console.print(
                    f"  Technical gaps: {len(estate.ai_analysis.technical_gaps)} | "
                    f"Functional gaps: {len(estate.ai_analysis.functional_gaps)} | "
                    f"Trend alignments: {len(estate.ai_analysis.trend_alignments)}"
                )
        except Exception as e:
            console.print(f"[yellow]  AI analysis skipped: {e}[/yellow]")
    elif ai and not ai_cfg.get("api_key"):
        console.print("[dim]  AI analysis skipped — set ai.api_key in config to enable[/dim]")

    console.rule("[bold blue]Generating report")
    generator = ReportGenerator()
    paths = generator.generate(estate, result, Path(output), cfg)
    console.print(f"[green]✔ HTML report: {paths['html']}[/green]")
    console.print(f"[green]✔ JSON report: {paths['json']}[/green]")
    console.print()
    console.print("[bold]Open the HTML report in your browser to view the full assessment.[/bold]")


@cli.command()
@click.option("--config", "-c", type=click.Path(exists=True), required=True)
def check(config: str) -> None:
    """Check connectivity to all configured sources without running an assessment."""
    setup_logging(False)
    with open(config) as f:
        cfg = yaml.safe_load(f)
    sources = cfg.get("sources", {})

    adapters = [
        ("prometheus", PrometheusAdapter),
        ("grafana", GrafanaAdapter),
        ("loki", LokiAdapter),
        ("jaeger", JaegerAdapter),
        ("alertmanager", AlertManagerAdapter),
        ("tempo", TempoAdapter),
        ("elasticsearch", ElasticsearchAdapter),
        ("otel_collector", OtelCollectorAdapter),
        ("appdynamics", AppDynamicsAdapter),
        ("datadog", DatadogAdapter),
        ("dynatrace", DynatraceAdapter),
    ]
    console.rule("[bold blue]Connectivity Check")
    for name, cls in adapters:
        scfg = sources.get(name, {})
        if not scfg.get("enabled"):
            console.print(f"[dim]  {name:<18} disabled[/dim]")
            continue
        url = scfg.get("url", "?")
        try:
            adapter = cls(scfg)
            ok = adapter.health_check()
        except Exception as e:
            console.print(f"[red]  {name:<18} ✘ error: {e}[/red]")
            continue
        if ok:
            console.print(f"[green]  {name:<18} ✔ reachable[/green]  {url}")
        else:
            console.print(f"[red]  {name:<18} ✘ unreachable[/red]  {url}")


@cli.command(name="list-rules")
def list_rules() -> None:
    """List all rules loaded from the rule packs."""
    setup_logging(False)
    engine = RulesEngine()
    console.rule(f"[bold blue]Loaded Rules ({len(engine.rules)})")
    for rule_id, rd in sorted(engine.rules.items()):
        sev_color = {
            "critical": "red",
            "high": "orange3",
            "medium": "yellow",
            "low": "blue",
            "info": "dim",
        }.get(rd.severity, "white")
        console.print(
            f"  [{sev_color}]{rule_id:<12}[/{sev_color}] "
            f"[{rd.dimension:<24}] {rd.title}"
        )


@cli.command(name="export")
@click.option("--config", "-c", type=click.Path(exists=True), required=True,
              help="Path to config YAML file")
@click.option("--output", "-o", type=click.Path(), default="./exports",
              help="Output directory for the Excel export (default: ./exports)")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def export_cmd(config: str, output: str, verbose: bool) -> None:
    """Extract all observability data and export to a multi-sheet Excel workbook.

    Connects to every enabled source in the config, pulls the full estate
    (metrics, services, dashboards, alerts, tracing, etc.) and writes a
    structured .xlsx file — one worksheet per observability concern.
    The file is suitable for sharing with a client as a current-state snapshot.
    """
    setup_logging(verbose)
    console.rule("[bold blue]ObservaScore v0.2.0 — Estate Export")

    with open(config) as f:
        cfg = yaml.safe_load(f)

    client_name = cfg.get("client", {}).get("name", "Unknown")
    environment = cfg.get("client", {}).get("environment", "unknown")

    estate  = ObservabilityEstate(
        client_name=client_name,
        environment=environment,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    summary = ExtractionSummary()
    sources = cfg.get("sources", {})

    adapter_map = [
        ("prometheus",    PrometheusAdapter),
        ("grafana",       GrafanaAdapter),
        ("loki",          LokiAdapter),
        ("jaeger",        JaegerAdapter),
        ("alertmanager",  AlertManagerAdapter),
        ("tempo",         TempoAdapter),
        ("elasticsearch", ElasticsearchAdapter),
        ("otel_collector", OtelCollectorAdapter),
        ("appdynamics",   AppDynamicsAdapter),
        ("datadog",       DatadogAdapter),
        ("dynatrace",     DynatraceAdapter),
    ]

    console.rule("[bold blue]Extracting from sources")
    for src_key, adapter_cls in adapter_map:
        src_cfg = sources.get(src_key, {})
        if not src_cfg.get("enabled"):
            console.print(f"[dim]  {src_key:<16} disabled[/dim]")
            continue
        estate.configured_tools.append(src_key)
        _run_adapter(src_key, adapter_cls, src_cfg, estate, summary)

    estate.summary = summary

    # ── Signals / alerts / services counts ──
    console.print(
        f"[bold]Estate snapshot:[/bold] "
        f"{len(estate.signals)} signals · "
        f"{len(estate.alert_rules)} alert rules · "
        f"{len(estate.dashboards)} dashboards · "
        f"{len(estate.services)} services · "
        f"{len(estate.scrape_targets)} scrape targets"
    )

    console.rule("[bold blue]Generating Excel export")
    exporter = ExcelExporter()
    try:
        path = exporter.export(estate, Path(output))
    except ImportError:
        console.print("[red]✘ openpyxl is required for Excel export.[/red]")
        console.print("  Install it with:  pip install openpyxl>=3.1")
        raise SystemExit(1)

    console.print(f"[green]✔ Excel export: {path}[/green]")
    console.print()
    console.print("[bold]Open the .xlsx file in Excel or Google Sheets.[/bold]")
    console.print(
        f"  Sheets: Summary · Signals & Metrics · Services & Apps · "
        f"Scrape Targets · Alert Rules · Recording Rules & SLOs · "
        f"Dashboards · Dashboard Panels · Datasources · Alert Receivers · "
        f"Tracing · Tool Topology · OTel Pipelines · Label Inventory · Extraction Log"
    )


if __name__ == "__main__":
    cli()
