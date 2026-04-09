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
    GrafanaAdapter,
    JaegerAdapter,
    LokiAdapter,
    PrometheusAdapter,
)
from observascore.engine import ScoringEngine
from observascore.model import ExtractionSummary, ObservabilityEstate
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


@click.group()
@click.version_option("0.1.0", prog_name="observascore")
def cli():
    """ObservaScore — Observability & SRE Maturity Assessment."""


@cli.command()
@click.option("--config", "-c", type=click.Path(exists=True), required=True,
              help="Path to config YAML file")
@click.option("--output", "-o", type=click.Path(), default="./reports",
              help="Output directory for reports")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def assess(config: str, output: str, verbose: bool) -> None:
    """Run a full maturity assessment and produce an HTML + JSON report."""
    setup_logging(verbose)
    console.rule("[bold blue]ObservaScore — Assessment Starting")

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

    # --- Prometheus ---
    if sources.get("prometheus", {}).get("enabled"):
        console.print("[cyan]→ Prometheus[/cyan]")
        adapter = PrometheusAdapter(sources["prometheus"])
        if adapter.health_check():
            try:
                data = adapter.extract()
                estate.alert_rules.extend(data["alert_rules"])
                estate.recording_rules.extend(data["recording_rules"])
                estate.scrape_targets.extend(data["scrape_targets"])
                estate.signals.extend(data["signals"])
                summary.prometheus_targets = len(data["scrape_targets"])
                summary.prometheus_targets_up = sum(
                    1 for t in data["scrape_targets"] if t.health == "up"
                )
                summary.prometheus_alert_rules = len(data["alert_rules"])
                summary.prometheus_recording_rules = len(data["recording_rules"])
                summary.prometheus_metrics_sampled = len(data["signals"])
                summary.extraction_errors.extend(f"prometheus: {e}" for e in data["errors"])
            except Exception as e:
                summary.extraction_errors.append(f"prometheus: {e}")
                console.print(f"[red]  Prometheus extraction failed: {e}[/red]")
        else:
            summary.extraction_errors.append("prometheus: health check failed")
            console.print("[red]  Prometheus unreachable[/red]")

    # --- Grafana ---
    if sources.get("grafana", {}).get("enabled"):
        console.print("[cyan]→ Grafana[/cyan]")
        adapter = GrafanaAdapter(sources["grafana"])
        if adapter.health_check():
            try:
                data = adapter.extract()
                estate.dashboards.extend(data["dashboards"])
                estate.datasources.extend(data["datasources"])
                estate.alert_rules.extend(data["alert_rules"])
                summary.grafana_dashboards = len(data["dashboards"])
                summary.grafana_folders = len(data.get("folders", []))
                summary.grafana_datasources = len(data["datasources"])
                summary.grafana_alert_rules = len(data["alert_rules"])
                summary.extraction_errors.extend(f"grafana: {e}" for e in data["errors"])
            except Exception as e:
                summary.extraction_errors.append(f"grafana: {e}")
                console.print(f"[red]  Grafana extraction failed: {e}[/red]")
        else:
            summary.extraction_errors.append("grafana: health check failed")
            console.print("[red]  Grafana unreachable — check URL and API key[/red]")

    # --- Loki ---
    if sources.get("loki", {}).get("enabled"):
        console.print("[cyan]→ Loki[/cyan]")
        adapter = LokiAdapter(sources["loki"])
        if adapter.health_check():
            try:
                data = adapter.extract()
                estate.signals.extend(data["signals"])
                summary.loki_labels = len(data["labels"])
                summary.extraction_errors.extend(f"loki: {e}" for e in data["errors"])
            except Exception as e:
                summary.extraction_errors.append(f"loki: {e}")
                console.print(f"[red]  Loki extraction failed: {e}[/red]")
        else:
            summary.extraction_errors.append("loki: health check failed")
            console.print("[red]  Loki unreachable[/red]")

    # --- Jaeger ---
    if sources.get("jaeger", {}).get("enabled"):
        console.print("[cyan]→ Jaeger[/cyan]")
        adapter = JaegerAdapter(sources["jaeger"])
        if adapter.health_check():
            try:
                data = adapter.extract()
                estate.services.extend(data["services"])
                estate.signals.extend(data["signals"])
                summary.jaeger_services = len(data["services"])
                summary.jaeger_operations = sum(len(s.operations) for s in data["services"])
                summary.extraction_errors.extend(f"jaeger: {e}" for e in data["errors"])
            except Exception as e:
                summary.extraction_errors.append(f"jaeger: {e}")
                console.print(f"[red]  Jaeger extraction failed: {e}[/red]")
        else:
            summary.extraction_errors.append("jaeger: health check failed")
            console.print("[red]  Jaeger unreachable[/red]")

    estate.summary = summary

    console.rule("[bold blue]Evaluating rules")
    engine = RulesEngine()
    findings = engine.evaluate(estate)

    console.rule("[bold blue]Scoring")
    scorer = ScoringEngine()
    result = scorer.score(findings)

    console.print(
        f"[bold]Overall maturity level: {result.overall_level} "
        f"— {result.overall_level_name} "
        f"({result.overall_score:.1f}/100)[/bold]"
    )
    for d in result.dimension_scores:
        console.print(
            f"  {d.label:<30} L{d.level} ({d.score:.0f}/100) — {d.findings_count} findings"
        )

    console.rule("[bold blue]Generating report")
    generator = ReportGenerator()
    paths = generator.generate(estate, result, Path(output), cfg)
    console.print(f"[green]✔ HTML report: {paths['html']}[/green]")
    console.print(f"[green]✔ JSON report: {paths['json']}[/green]")
    console.print()
    console.print("[bold]Open the HTML report in your browser to view the assessment.[/bold]")


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
    ]
    console.rule("[bold blue]Connectivity Check")
    for name, cls in adapters:
        scfg = sources.get(name, {})
        if not scfg.get("enabled"):
            console.print(f"[dim]  {name:<12} disabled[/dim]")
            continue
        url = scfg.get("url", "?")
        try:
            adapter = cls(scfg)
            ok = adapter.health_check()
        except Exception as e:
            ok = False
            console.print(f"[red]  {name:<12} ✘ error: {e}[/red]")
            continue
        if ok:
            console.print(f"[green]  {name:<12} ✔ reachable[/green]  {url}")
        else:
            console.print(f"[red]  {name:<12} ✘ unreachable[/red]  {url}")


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
            f"[{rd.dimension:<18}] {rd.title}"
        )


if __name__ == "__main__":
    cli()
