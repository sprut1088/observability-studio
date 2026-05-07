"""Report generator — produces HTML and JSON reports."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from observascore.engine import MaturityResult
from observascore.model import ObservabilityEstate

logger = logging.getLogger(__name__)


class ReportGenerator:
    def __init__(self, template_dir: Path | None = None):
        if template_dir is None:
            template_dir = Path(__file__).parent / "templates"
        self.env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=select_autoescape(["html"]),
        )

    def generate(
        self,
        estate: ObservabilityEstate,
        result: MaturityResult,
        output_dir: Path,
        config: dict[str, Any],
    ) -> dict[str, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)

        ai = estate.ai_analysis

        # JSON report
        json_path = output_dir / "observascore-report.json"
        report_data: dict[str, Any] = {
            "metadata": {
                "client": estate.client_name,
                "environment": estate.environment,
                "generated_at": estate.timestamp,
                "tool_version": "0.2.0",
                "configured_tools": estate.configured_tools,
            },
            "summary": {
                "prometheus_targets": estate.summary.prometheus_targets,
                "prometheus_targets_up": estate.summary.prometheus_targets_up,
                "prometheus_alert_rules": estate.summary.prometheus_alert_rules,
                "prometheus_recording_rules": estate.summary.prometheus_recording_rules,
                "prometheus_metrics_sampled": estate.summary.prometheus_metrics_sampled,
                "grafana_dashboards": estate.summary.grafana_dashboards,
                "grafana_folders": estate.summary.grafana_folders,
                "grafana_datasources": estate.summary.grafana_datasources,
                "loki_labels": estate.summary.loki_labels,
                "jaeger_services": estate.summary.jaeger_services,
                "alertmanager_receivers": estate.summary.alertmanager_receivers,
                "alertmanager_silences": estate.summary.alertmanager_silences,
                "alertmanager_integrations": estate.summary.alertmanager_integrations,
                "tempo_services": estate.summary.tempo_services,
                "elasticsearch_indices": estate.summary.elasticsearch_indices,
                "otel_receivers": estate.summary.otel_receivers,
                "otel_exporters": estate.summary.otel_exporters,
                "otel_pipelines": estate.summary.otel_pipelines,
                # AppDynamics
                "appdynamics_applications": estate.summary.appdynamics_applications,
                "appdynamics_tiers": estate.summary.appdynamics_tiers,
                "appdynamics_health_rules": estate.summary.appdynamics_health_rules,
                "appdynamics_business_transactions": estate.summary.appdynamics_business_transactions,
                "appdynamics_has_eum": estate.summary.appdynamics_has_eum,
                "appdynamics_has_sim": estate.summary.appdynamics_has_sim,
                "appdynamics_has_db_monitoring": estate.summary.appdynamics_has_db_monitoring,
                "appdynamics_apps_with_baselines": estate.summary.appdynamics_apps_with_baselines,
                # Datadog
                "datadog_monitors": estate.summary.datadog_monitors,
                "datadog_monitors_with_notifications": estate.summary.datadog_monitors_with_notifications,
                "datadog_dashboards": estate.summary.datadog_dashboards,
                "datadog_hosts": estate.summary.datadog_hosts,
                "datadog_slos": estate.summary.datadog_slos,
                "datadog_synthetics": estate.summary.datadog_synthetics,
                "datadog_has_apm": estate.summary.datadog_has_apm,
                "datadog_has_log_management": estate.summary.datadog_has_log_management,
                "datadog_has_security_monitoring": estate.summary.datadog_has_security_monitoring,
                "datadog_has_service_catalog": estate.summary.datadog_has_service_catalog,
                # Dynatrace
                "dynatrace_services": estate.summary.dynatrace_services,
                "dynatrace_hosts": estate.summary.dynatrace_hosts,
                "dynatrace_applications": estate.summary.dynatrace_applications,
                "dynatrace_problems_open": estate.summary.dynatrace_problems_open,
                "dynatrace_slos": estate.summary.dynatrace_slos,
                "dynatrace_synthetics": estate.summary.dynatrace_synthetics,
                "dynatrace_alerting_profiles": estate.summary.dynatrace_alerting_profiles,
                "dynatrace_notification_integrations": estate.summary.dynatrace_notification_integrations,
                "dynatrace_has_log_management": estate.summary.dynatrace_has_log_management,
                "dynatrace_has_rum": estate.summary.dynatrace_has_rum,
                # Splunk
                "splunk_dashboards": estate.summary.splunk_dashboards,
                "splunk_alerts": estate.summary.splunk_alerts,
                "splunk_indexes": estate.summary.splunk_indexes,
                "splunk_saved_searches": estate.summary.splunk_saved_searches,
                "splunk_hec_configured": estate.summary.splunk_hec_configured,
                "extraction_errors": estate.summary.extraction_errors,
            },
            "result": result.to_dict(),
            "ai_analysis": ai.to_dict() if ai else None,
        }
        with open(json_path, "w") as f:
            json.dump(report_data, f, indent=2, default=str)
        logger.info("JSON report: %s", json_path)

        # HTML report
        html_path = output_dir / "observascore-report.html"
        template = self.env.get_template("report.html")

        findings_by_severity: dict[str, list] = {
            "critical": [], "high": [], "medium": [], "low": [], "info": []
        }
        for f in result.findings:
            findings_by_severity.setdefault(f.severity, []).append(f.to_dict())

        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        backlog = sorted(
            [f.to_dict() for f in result.findings],
            key=lambda f: (severity_order.get(f["severity"], 99), -f["weight"]),
        )
        quick_wins = [b for b in backlog if b["severity"] in ("low", "medium")][:10]
        strategic = [b for b in backlog if b["severity"] in ("critical", "high")][:10]

        footer_tagline = "Deterministic rules"
        if ai:
            footer_tagline = "Deterministic rules + AI-powered gap analysis"

        html = template.render(
            client_name=estate.client_name,
            environment=estate.environment,
            generated_at=estate.timestamp,
            title=config.get("report", {}).get("title", "Observability & SRE Maturity Assessment"),
            summary=report_data["summary"],
            result=result,
            findings_by_severity=findings_by_severity,
            backlog=backlog,
            quick_wins=quick_wins,
            strategic=strategic,
            dashboards=estate.dashboards[:50],
            alert_rules=estate.alert_rules[:50],
            datasources=estate.datasources,
            alert_receivers=estate.alert_receivers,
            ai_analysis=ai,
            footer_tagline=footer_tagline,
        )
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("HTML report: %s", html_path)

        return {"html": html_path, "json": json_path}